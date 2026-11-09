"""Read the two stockmojo.in pages the strategy runs on.

Both pages are client-rendered and their data API (data.stockmojo.in) answers
403 to anything but the page's own session, so this drives a headless
Chromium and reads the RENDERED DOM -- the same numbers a person sees.
Verified by hand against both pages on 2026-10-07.

  * /open-interest/nifty -- the "Market Sentiment (based on OI)" card. On a
    wide viewport it renders expanded ("Bearish / Bearish market conditions /
    70%"); on a narrow one collapsed ("Bearish · 70%"). The regex reads both.
    Before its data arrives the card reads "Neutral · %" with NO number,
    which is why a reading without a percentage is treated as not-loaded.
  * /intraday-booster -- one `#sector-card-<NAME>` per sector: an <h3> name,
    a "(-2.33%)" change, and rows of four grid cells whose FIRST cell is a
    gainer ("+3.82%" + symbol) and LAST cell a loser ("-4.74%" + symbol).

Read-only: nothing here clicks, logs in or submits anything.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

OI_URL = "https://stockmojo.in/open-interest/nifty"
# A browser profile the operator signs in to ONCE, by hand (./start.sh login).
# Signed out, stockmojo stops live data at ~10:00 behind a "Login Required"
# banner: the OI page's sentiment froze at its 10:00 reading and the
# booster's stock %s at ~10:05 on both 2026-10-08 and 2026-10-09, while the
# booster's SECTOR chart stayed live. This code never types a password; it
# only reuses the session the operator created.
PROFILE = Path(__file__).resolve().parent.parent / ".browser-profile"
_GATE_JS = "() => /Login Required/i.test(document.body.innerText)"
BOOSTER_URL = "https://stockmojo.in/intraday-booster"

_SENTIMENT_JS = r"""
() => {
  const heads = [...document.querySelectorAll('div')].filter(
    e => e.childElementCount <= 1 && /^Market Sentiment/.test(e.textContent.trim()));
  for (const h of heads) {
    let card = h;
    for (let i = 0; i < 4 && card; i++) {
      card = card.parentElement;
      if (card && /\d+(\.\d+)?%/.test(card.innerText)) return card.innerText;
    }
  }
  return null;
}
"""

_SECTORS_JS = r"""
() => [...document.querySelectorAll('[id^="sector-card-"]')].map(card => {
  const name = (card.querySelector('h3') || {}).textContent || card.id.slice(12);
  const pctEl = card.querySelector('h3 + span');
  const rows = [...card.querySelectorAll('div.grid')].map(row => {
    const cells = row.children;
    const read = c => {
      if (!c) return null;
      const t = c.innerText.trim();
      if (!t) return null;
      const m = t.match(/([+-]?\d+(?:\.\d+)?)%/);
      const sym = t.replace(/[+-]?\d+(?:\.\d+)?%/, '').trim();
      return m && sym ? [sym, parseFloat(m[1])] : null;
    };
    return [read(cells[0]), read(cells[cells.length - 1])];
  });
  return {name: name.trim(), pct: pctEl ? pctEl.textContent : null, rows};
})
"""

_SENT_RE = re.compile(r"\b(Bullish|Bearish|Neutral)\b[\s\S]*?(\d+(?:\.\d+)?)\s*%")
_PCR_RE = re.compile(r"PCR:\s*([\d.]+)")
_PCT_RE = re.compile(r"([+-−]?\d+(?:\.\d+)?)\s*%")


@dataclass
class Sentiment:
    label: str            # BULLISH / BEARISH / NEUTRAL
    pct: float
    pcr: Optional[float]
    raw: str


@dataclass
class Sector:
    name: str
    pct: float
    stocks: dict = field(default_factory=dict)   # symbol -> % change


def parse_sentiment(text: Optional[str]) -> Optional[Sentiment]:
    if not text:
        return None
    m = _SENT_RE.search(text)
    if not m:
        return None
    pcr = _PCR_RE.search(text)
    return Sentiment(m.group(1).upper(), float(m.group(2)),
                     float(pcr.group(1)) if pcr else None,
                     " ".join(text.split())[:300])


def parse_sectors(cards: list) -> list[Sector]:
    out = []
    for c in cards or []:
        m = _PCT_RE.search(c.get("pct") or "")
        if not m:
            continue
        stocks = {}
        for gainer, loser in c.get("rows", []):
            for item in (gainer, loser):
                if item:
                    stocks[item[0].split()[-1]] = float(item[1])
        out.append(Sector(c["name"].upper(), float(m.group(1).replace("−", "-")), stocks))
    return out


class StockMojo:
    """One headless browser; every read opens the page in a NEW tab.

    Signed in (a `.browser-profile` exists): one persistent context carries
    the operator's session, and each read is a new tab in it, closed after.
    Signed out: each read gets a brand-new context. `gated[url]` says whether
    the last read of that page showed the "Login Required" banner -- i.e.
    whether its numbers are the frozen ~10:00 copy.
    """

    def __init__(self, headless: bool = True, timeout_s: float = 45.0):
        self.headless = headless
        self.timeout_ms = int(timeout_s * 1000)
        self._pw = self._browser = self._persistent = None
        self.gated: dict[str, bool] = {}

    @property
    def signed_in_profile(self) -> bool:
        return PROFILE.is_dir() and any(PROFILE.iterdir())

    async def start(self) -> None:
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        if self.signed_in_profile:
            self._persistent = await self._pw.chromium.launch_persistent_context(
                str(PROFILE), headless=self.headless, viewport={"width": 1440, "height": 1000})
        else:
            self._browser = await self._pw.chromium.launch(headless=self.headless)

    async def close(self) -> None:
        if self._persistent:
            await self._persistent.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._pw = self._browser = self._persistent = None

    def _alive(self) -> bool:
        if self._persistent is not None:
            return True
        return self._browser is not None and self._browser.is_connected()

    async def _fresh_page(self, url: str):
        """(context to close or None, page). The page is always new."""
        if self._persistent is not None:
            ctx, page = None, await self._persistent.new_page()
        else:
            ctx = await self._browser.new_context(viewport={"width": 1440, "height": 1000})
            page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        return ctx, page

    async def _poll(self, page, js, ok, wait_s: float = 25.0):
        """Evaluate `js` until `ok(result)` AND the same result again a second
        later -- the data arrives after the HTML, and a fresh page must not be
        read while it is still filling in."""
        result = previous = None
        for _ in range(int(wait_s * 2)):
            result = await page.evaluate(js)
            if ok(result) and result == previous:
                return result
            previous = result if ok(result) else None
            await asyncio.sleep(1.0 if previous is not None else 0.5)
        return result

    async def restart(self) -> None:
        """A browser can come back broken from a Mac sleep (lid closed). Start a
        fresh one rather than failing every cycle for the rest of the day."""
        try:
            await self.close()
        except Exception:  # noqa: BLE001
            pass
        await self.start()

    async def _read(self, url: str, js: str, ok):
        for attempt in (1, 2):
            ctx = page = None
            try:
                if not self._alive():
                    await self.restart()
                ctx, page = await self._fresh_page(url)
                result = await self._poll(page, js, ok)
                self.gated[url] = bool(await page.evaluate(_GATE_JS))
                return result
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    raise
                await self.restart()
                ctx = page = None
            finally:
                for closable in (page, ctx):
                    if closable is not None:
                        try:
                            await closable.close()
                        except Exception:  # noqa: BLE001
                            pass

    async def sentiment(self) -> Optional[Sentiment]:
        text = await self._read(OI_URL, _SENTIMENT_JS, lambda r: parse_sentiment(r) is not None)
        return parse_sentiment(text)

    async def sectors(self) -> list[Sector]:
        cards = await self._read(BOOSTER_URL, _SECTORS_JS,
                                 lambda r: bool(r) and any(c["rows"] for c in r))
        return parse_sectors(cards)
