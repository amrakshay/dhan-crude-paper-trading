"""Live paper run: OI sentiment -> sector -> 3 stocks -> 9 EMA / VWAP trades.

Runs forever. Outside market hours (and on weekends) it sleeps; from 09:15 to
15:30 IST it wakes every 5 minutes, just after each 5-minute bar closes:

  every cycle   read NIFTY OI sentiment                 -> sentiment.csv
  from 09:45    read the intraday booster, pick the
                sector and its top 3 names               -> tracking.csv, booster.csv
                fetch 5m bars for tracked + open names,
                trail stops, look for entries            -> signals/decisions/bars.csv
  from 10:00    entries allowed; the PLAN book takes at most 2 a day, the ALL
                book takes every signal                  -> trades_plan/all.csv
  15:00         square off everything; no entries from the bar closing at 15:00
  15:30         measure every trade's excursions, write the day's summary,
                sleep until the next weekday

Between cycles a Dhan Ticker feed watches open trades' LTP so a stop is taken
when it is breached, not at the next bar.

Start it with ../start.sh (see README.md). Directly, from backend/:
    CONFIG_PATH=conf .venv/bin/python ../experiments/sentiment-sector-vwap/scripts/run_live.py
    ... --check              one read of both pages + bars for the 3 names, then exit
    ... --enrich YYYY-MM-DD  (re)compute a past day's excursions, e.g. after a crash
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import fcntl
import json
import logging
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

EXP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXP))

import yaml  # noqa: E402

from svlib import dhan  # noqa: E402  (puts backend/ on sys.path, loads app config)
from svlib import logsetup, market  # noqa: E402
from svlib.book import Book, append_daily_ledger, csv_append, ts, write_summaries  # noqa: E402
from svlib.costs import RateCard  # noqa: E402
from svlib.engine import (  # noqa: E402
    LONG, SHORT, Setup, bar_stop_fill, entry_features, excursions, pick_sector, pick_stocks,
    simulate_trail, stop_hit, stop_is_valid, trail, trail_level, with_indicators,
)
from svlib.scrape import BOOSTER_URL, OI_URL, StockMojo  # noqa: E402
from src.core.time_utils import ist_now  # noqa: E402
from src.market.services.dhan_option_chain_client import DhanOptionChainClient  # noqa: E402

DATA = EXP / "data"
LOGS = EXP / "logs"
CYCLE_TIMEOUT_S = 270          # a cycle that has not finished by then is abandoned, never left hanging
log = logging.getLogger("sv")


def say(msg: str) -> None:
    """A line for the console (and the log files)."""
    log.info(msg, extra={"console": True})


def now_ist() -> datetime:
    return ist_now().replace(tzinfo=None)


def hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def at(day: date, t: time) -> datetime:
    return datetime.combine(day, t)


def money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}Rs{abs(x):,.0f}"


def span(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60}h{m % 60:02d}m"


async def sleep_until(target: datetime, why: str = "", heartbeat_s: float = 3600.0) -> None:
    if why:
        say(f"sleeping until {target:%a %d %b %H:%M} {why} -- {span((target - now_ist()).total_seconds())} from now")
    last_beat = now_ist()
    # Chunked, so a laptop that slept through the target wakes up promptly.
    while (left := (target - now_ist()).total_seconds()) > 0:
        await asyncio.sleep(min(left, 60.0))
        if why and (now_ist() - last_beat).total_seconds() >= heartbeat_s:
            last_beat = now_ist()
            left = (target - now_ist()).total_seconds()
            if left > 60:
                say(f"still sleeping, alive -- next session in {span(left)}")


class Day:
    def __init__(self, cfg: dict, day: date) -> None:
        self.cfg, self.day = cfg, day
        sch = cfg["schedule"]
        self.t_start, self.t_end = hhmm(sch["session_start"]), hhmm(sch["session_end"])
        self.t_track = hhmm(sch["tracking_start"])
        self.t_first_entry = at(day, hhmm(sch["no_entries_before"]))
        self.t_no_entry = at(day, hhmm(sch["no_new_entries_from"]))
        self.t_square = at(day, hhmm(sch["square_off_at"]))
        self.step = timedelta(minutes=int(sch["cycle_minutes"]))
        self.delay = int(sch["cycle_delay_seconds"])
        self.bar_wait = int(sch["bar_wait_seconds"])
        self.dir = DATA / day.isoformat()
        rates = RateCard.load(EXP / cfg["costs"]["rate_card"])
        self.plan = Book("plan", self.dir, cfg, rates)
        self.all = Book("all", self.dir, cfg, rates)
        self.books = (self.plan, self.all)
        self.plan_cap = int(cfg["plan"]["max_trades_per_day"])
        self.scraper = StockMojo(cfg["scrape"]["headless"], cfg["scrape"]["page_timeout_seconds"])
        self.bars = dhan.Bars()
        self.feed = dhan.TickerFeed(self.on_tick) if cfg["feed"]["enabled"] else None
        self.chain = DhanOptionChainClient()
        self.expiry: Optional[str] = None
        self.members: dict = {}
        self.index_ids: dict = {}
        self.prev_close: dict = {}
        self.universe_keys: set = set()
        self.sentiment = None
        self.sentiment_flips = 0
        self.side = None
        self.sector = None
        self.picked: dict[str, tuple[int, float]] = {}      # symbol -> (rank, booster %)
        self.tracked: list[str] = []
        self.tracked_since: dict[str, datetime] = {}
        self.setups: dict[str, Setup] = {}
        self.sids: dict[str, str] = {}
        self.last_logged_bar: dict[str, datetime] = {}
        self.signal_seq = 0
        self.nifty_pct = None
        self.squared_off = False
        self.session_confirmed = False
        # A restart mid-day resumes the day's record rather than replacing it.
        gaps_file = self.dir / "gaps.json"
        self.gaps: list[dict] = json.loads(gaps_file.read_text()) if gaps_file.exists() else []
        self.ran_until = self._last_cycle_run()
        # Staleness alarm: during the session the OI card's PCR and OI figures,
        # and the booster's stock %s, change every few minutes. The same text
        # for STALE_AFTER reads in a row means the page is not updating.
        self._same = {"sentiment": [None, 0], "booster": [None, 0]}
        self._gate_warned: set[str] = set()
        for b in self.books:
            for t in b.open.values():
                self.sids[t["symbol"]] = t["security_id"]

    # --- the day --------------------------------------------------------------
    async def run(self) -> None:
        say(f"=== session {self.day:%a %d %b %Y} === entries {self.t_first_entry:%H:%M}-{self.t_no_entry:%H:%M}, "
            f"plan book max {self.plan_cap} trades/day, square-off {self.t_square:%H:%M}")
        if self.plan.open or self.all.open:
            say(f"resumed open trades: plan {list(self.plan.open)} / all {list(self.all.open)}")
        await dhan.reload_credentials()
        if not self.bars.has_credentials():
            log.error("no Dhan credentials stored; set them on the app's Settings page")
        await self.scraper.start()
        try:
            await self.load_universe()
        except Exception:  # noqa: BLE001
            log.exception("could not load the sector universe; Dhan sector selection will be empty")
        if self.feed:
            self.feed.start()
            await self._sync_feed()
        try:
            boundary = at(self.day, self.t_start)
            end = at(self.day, self.t_end)
            missed: list[datetime] = []
            while boundary <= end:
                due = boundary + timedelta(seconds=self.delay)
                if now_ist() > due + self.step:
                    # Too late for this cycle: the Mac was asleep (lid closed),
                    # or the process started late. Skip to the latest boundary.
                    # A cycle an EARLIER run of today already did (a restart)
                    # is not missed, and is not reported as a gap.
                    if self.ran_until is None or boundary > self.ran_until:
                        missed.append(boundary)
                    boundary += self.step
                    continue
                if missed:
                    self._report_gap(missed)
                    missed = []
                await sleep_until(due)
                try:
                    holiday = await asyncio.wait_for(self.cycle(boundary), CYCLE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    log.error("cycle %s did not finish in %ss; abandoned", boundary.strftime("%H:%M"), CYCLE_TIMEOUT_S)
                    holiday = False
                except Exception:  # noqa: BLE001 -- one bad cycle must not end the day
                    log.exception("cycle %s failed", boundary.strftime("%H:%M"))
                    holiday = False
                if holiday:
                    say(f"no Nifty bars for {self.day} by {boundary:%H:%M}: market holiday, nothing to do today")
                    self.note(now_ist(), "-", "HOLIDAY", "no Nifty 50 index bars")
                    break
                boundary += self.step
            if missed:
                self._report_gap(missed)
            if not self.squared_off and (self.plan.open or self.all.open):
                await self.square_off(now_ist(), late=True)
            await self.finalize()
        finally:
            if self.feed:
                await self.feed.stop()
            await self.scraper.close()

    async def cycle(self, boundary: datetime) -> bool:
        now = now_ist()
        log.debug("cycle %s start", boundary.strftime("%H:%M"))
        try:
            await dhan.reload_credentials()     # the server rotates the token on renewal
        except Exception:  # noqa: BLE001
            log.exception("credential reload failed; keeping the loaded token")

        holiday = False
        if boundary.time() >= time(9, 20):
            fetched = False
            try:
                await self.read_nifty(boundary)
                fetched = True
            except Exception as exc:  # noqa: BLE001
                log.warning("Nifty index fetch failed: %s", exc)
            # Only a SUCCESSFUL fetch with no bars is a holiday; a failed one is not.
            if fetched and not self.session_confirmed and boundary.time() >= time(9, 30):
                holiday = True

        if holiday:
            return True
        await self.read_sentiment(now)

        if boundary.time() >= self.t_track and boundary < at(self.day, self.t_end):
            await self.read_sectors(now)
        # Nothing to trade after the square-off -- and F&O names have no
        # continuous-session bars after 15:15 anyway (NSE's closing auction).
        if boundary.time() >= self.t_track and not self.squared_off:
            await self.evaluate(boundary)
        if boundary >= self.t_square and not self.squared_off:
            await self.square_off(now_ist())
        await self._sync_feed()
        write_summaries(self.dir, self.books)
        say(self.status_line(boundary))
        return False

    def _last_cycle_run(self) -> Optional[datetime]:
        """The latest 5-minute boundary an earlier run of today recorded."""
        path = self.dir / "sentiment.csv"
        if not path.exists():
            return None
        with path.open() as fh:
            times = [r["time"] for r in csv.DictReader(fh) if r.get("time")]
        if not times:
            return None
        t = datetime.strptime(times[-1], "%Y-%m-%d %H:%M:%S")
        return t.replace(minute=t.minute - t.minute % 5, second=0)

    STALE_AFTER = 6          # 6 identical reads = 30 minutes

    def _gate_check(self, what: str, url: str) -> None:
        """Warn (once per page per session) when stockmojo shows its "Login
        Required" banner: signed out, its live data stops at ~10:00."""
        if self.scraper.gated.get(url) and what not in self._gate_warned:
            self._gate_warned.add(what)
            uses = self.cfg["selection"].get(f"{'sentiment' if what == 'sentiment' else 'sector'}_source", "dhan")
            tail = ("decisions use Dhan, so this only affects the comparison data" if uses == "dhan"
                    else "decisions USE it: sign in (./start.sh stop; ./start.sh login; ./start.sh --bg)")
            log.warning("stockmojo %s page says 'Login Required': its data freezes at ~10:00; %s",
                        what, tail, extra={"console": True})
            self.note(now_ist(), "-", "LOGIN_GATE", f"{what} page gated")

    async def _stale_check(self, what: str, value) -> None:
        if self.scraper.gated.get(OI_URL if what == "sentiment" else BOOSTER_URL):
            return     # already explained by the login gate; a restart cannot fix it
        last = self._same[what]
        last[1] = last[1] + 1 if value == last[0] else 1
        last[0] = value
        if last[1] == self.STALE_AFTER:
            log.warning("%s page unchanged for %d reads (%d min): data looks STALE; restarting the browser",
                        what, last[1], last[1] * 5, extra={"console": True})
            self.note(now_ist(), "-", "STALE", f"{what} unchanged for {last[1]} reads")
            if not self.scraper.gated.get(OI_URL if what == "sentiment" else BOOSTER_URL):
                await self.scraper.restart()   # a restart cannot lift the login gate
            last[1] = 0

    def _report_gap(self, missed: list[datetime]) -> None:
        """Say so, and keep a record: data with a silent hole in it would be
        mistaken for a quiet market when the days are analysed."""
        first, last = missed[0], missed[-1]
        msg = (f"missed {len(missed)} cycle(s) {first:%H:%M}-{last:%H:%M} "
               f"(Mac asleep / lid closed, or started late)")
        log.warning("%s; resuming now", msg, extra={"console": True})   # never de-duplicated
        self.note(now_ist(), "-", "GAP", msg)
        self.gaps.append({"from": first.strftime("%H:%M"), "to": last.strftime("%H:%M"),
                          "cycles": len(missed)})
        (self.dir / "gaps.json").write_text(json.dumps(self.gaps, indent=1))

    def note(self, now: datetime, symbol: str, event: str, detail: str = "") -> None:
        """A decisions.csv row that belongs to neither book (setups, the list)."""
        csv_append(self.dir / "decisions.csv", {"time": ts(now), "book": "-",
                                                 "symbol": symbol, "event": event, "detail": detail})

    def status_line(self, boundary: datetime) -> str:
        s = self.sentiment
        parts = [f"{boundary:%H:%M}",
                 f"{s.label} {s.pct:.0f}% PCR {s.pcr:.2f}" if s and s.pcr is not None else (f"{s.label} {s.pct:.0f}%" if s else "sentiment ?"),
                 f"Nifty {self.nifty_pct:+.2f}%" if self.nifty_pct is not None else "Nifty ?"]
        if boundary.time() >= self.t_track:
            parts.append(f"{self.sector.name} {self.sector.pct:+.2f}%" if self.sector else "no sector")
            parts.append(" ".join(f"{n} {self.picked.get(n, (0, 0))[1]:+.1f}" for n in self.tracked) or "-")
            opens = []
            for sym, t in self.plan.open.items():
                live = self.feed.price(t["security_id"]) if self.feed else None
                pnl = f" {money(self.plan.unrealised(sym, live))}" if live else ""
                opens.append(f"{sym} {t['side'][0]} @{t['entry_price']:.2f} sl {t['stop']:.2f}"
                             f"{f' ltp {live:.2f}' if live else ''}{pnl}")
            plan_net = sum(t["net_pnl"] for t in self.plan.closed)
            parts.append(f"PLAN {self.plan.taken_today}/{self.plan_cap} "
                         + (", ".join(opens) if opens else "flat") + f" closed {money(plan_net)}")
            all_net = sum(t["net_pnl"] for t in self.all.closed)
            parts.append(f"all: {self.all.taken_today} taken {len(self.all.open)} open {money(all_net)}")
            if self.feed:
                parts.append(f"feed {self.feed.state}")
            if any(self.scraper.gated.values()):
                parts.append("stockmojo gated (reference only)")
        return " | ".join(parts)

    # --- reads ------------------------------------------------------------------
    async def read_sentiment(self, now: datetime) -> None:
        try:
            s = await self.scraper.sentiment()
        except Exception as exc:  # noqa: BLE001
            s = None
            log.warning("sentiment read failed: %s", exc)
        csv_append(self.dir / "sentiment.csv", {
            "time": ts(now), "ok": bool(s), "sentiment": s.label if s else "", "pct": s.pct if s else "",
            "pcr": s.pcr if s and s.pcr is not None else "",
            "login_gate": self.scraper.gated.get(OI_URL, ""), "raw": s.raw if s else ""})
        self._gate_check("sentiment", OI_URL)
        if s:
            await self._stale_check("sentiment", s.raw)
        if self.cfg["selection"].get("sentiment_source", "dhan") == "dhan":
            s = await self.read_dhan_sentiment(now, reference=s)
        if not s:
            log.warning("sentiment not read this cycle; keeping the last reading")
            return
        if self.sentiment and s.label != self.sentiment.label:
            self.sentiment_flips += 1
            say(f"SENTIMENT FLIP {self.sentiment.label} -> {s.label} {s.pct:.0f}%")
        self.sentiment = s

    async def read_dhan_sentiment(self, now: datetime, reference) -> Optional[object]:
        """Our own sentiment from Dhan's NIFTY option chain, recorded next to
        stockmojo's reading of the same moment (`reference`)."""
        try:
            if self.expiry is None:
                expiries = await self.chain.fetch_expiry_list(int(dhan.NIFTY_INDEX[0]), dhan.NIFTY_INDEX[1])
                today = self.day.isoformat()
                self.expiry = next(e for e in expiries if e >= today)
            snap = await self.chain.fetch_option_chain(int(dhan.NIFTY_INDEX[0]), dhan.NIFTY_INDEX[1], self.expiry)
        except Exception as exc:  # noqa: BLE001
            log.warning("Dhan option chain read failed: %s", exc)
            return None
        spot = snap.underlying_last_price or (self.feed.price("IDX_I:13", 900) if self.feed else None)
        sent, rec = market.dhan_sentiment(snap.strikes, spot, self.nifty_pct, self.cfg["sentiment_rule"])
        csv_append(self.dir / "dhan_sentiment.csv", {
            "time": ts(now), "expiry": self.expiry, **rec,
            # stockmojo at the same moment, for calibrating the rule above
            "stockmojo_label": reference.label if reference else "",
            "stockmojo_pct": reference.pct if reference else "",
            "stockmojo_pcr": reference.pcr if reference else "",
            "stockmojo_gated": self.scraper.gated.get(OI_URL, ""),
            "agree": (reference.label == sent.label) if reference else ""})
        return sent

    async def load_universe(self) -> None:
        """Sector members, their security ids and previous closes -- once a
        day (cached in prev_close.json, so a restart does not refetch)."""
        sel = self.cfg["selection"]
        self.members = {k: list(v) for k, v in
                        yaml.safe_load((EXP / sel["members_file"]).read_text()).items()}
        self.index_ids = {k: (str(v) if v else None) for k, v in sel["sector_indices"].items()}
        symbols = sorted({s for v in self.members.values() for s in v})
        self.sids.update(dhan.security_ids([s for s in symbols if s not in self.sids]))
        cache = self.dir / "prev_close.json"
        self.prev_close = json.loads(cache.read_text()) if cache.exists() else {}
        wanted = [("NSE_EQ", self.sids[s], "EQUITY", s) for s in symbols if s in self.sids]
        wanted += [("IDX_I", i, "INDEX", f"IDX_I:{i}") for i in self.index_ids.values() if i]
        wanted.append(("IDX_I", dhan.NIFTY_INDEX[0], "INDEX", f"IDX_I:{dhan.NIFTY_INDEX[0]}"))
        todo = [w for w in wanted if w[3] not in self.prev_close]
        if todo:
            say(f"fetching previous closes for {len(todo)} stocks and indices (about a minute)")
        gate = asyncio.Semaphore(2)       # two at a time: 4 met Dhan rate limits, 1 took ~4 minutes

        async def one(seg, sid, inst, key):
            async with gate:
                try:
                    pc = await self.bars.prev_close(sid, self.day, seg, inst)
                except Exception as exc:  # noqa: BLE001
                    log.warning("previous close for %s failed: %s", key, exc)
                    return
                if pc:
                    self.prev_close[key] = pc
        await asyncio.gather(*(one(*w) for w in todo))
        cache.write_text(json.dumps(self.prev_close, indent=0))
        missing = [w[3] for w in wanted if w[3] not in self.prev_close]
        if missing:
            log.warning("no previous close for %d of %d instruments: %s", len(missing), len(wanted),
                        " ".join(missing[:15]))
        self.universe_keys = {w[3] if w[0] == "IDX_I" else w[1] for w in wanted}

    def dhan_sector_list(self) -> list:
        """Sector objects from the feed's live prices (stale up to 15 min is
        fine for a day change; a quiet stock does not tick every minute)."""
        price = (lambda key: self.feed.price(key, 900) if self.feed else None)
        change = {s: market.day_change(price(self.sids.get(s, "")), self.prev_close.get(s))
                  for v in self.members.values() for s in v}
        index_change = {name: market.day_change(price(f"IDX_I:{i}"), self.prev_close.get(f"IDX_I:{i}"))
                        for name, i in self.index_ids.items() if i}
        members = self.members
        if not self.cfg["selection"].get("include_unindexed_sectors", False):
            # Broad buckets (NIFTY, MIDCAP, ...) and CHEMICALS have no sector
            # index here. A members' average is a different number from the
            # one stockmojo shows (CHEMICALS: 0.99 vs 0.42 on 2026-10-09), so
            # such a sector is left out of the pick rather than mis-ranked.
            members = {k: v for k, v in members.items() if self.index_ids.get(k)}
        sectors, skipped = market.dhan_sectors(members, self.index_ids, change, index_change)
        if skipped:
            log.debug("sectors left out this cycle: %s", "; ".join(skipped))
        return sectors

    async def read_nifty(self, boundary: datetime) -> None:
        sid, seg, inst = dhan.NIFTY_INDEX
        bars = await self.bars.fetch(sid, at(self.day - timedelta(days=6), time(9, 0)), boundary,
                                     segment=seg, instrument=inst)
        today = [b for b in bars if b.start.date() == self.day]
        before = [b for b in bars if b.start.date() < self.day]
        if today:
            self.session_confirmed = True
            # The official previous close (daily candle) when loaded; the last
            # 5-minute bar of yesterday otherwise.
            prev = self.prev_close.get(f"IDX_I:{sid}") or (before[-1].close if before else None)
            live = self.feed.price(f"IDX_I:{sid}", 120) if self.feed else None
            if prev:
                self.nifty_pct = ((live or today[-1].close) / prev - 1) * 100

    async def read_sectors(self, now: datetime) -> None:
        sel = self.cfg["selection"]
        try:
            sectors = await self.scraper.sectors()
        except Exception as exc:  # noqa: BLE001
            sectors = []
            log.warning("booster read failed: %s", exc)
        if sectors:
            await self._stale_check("booster", tuple(sorted(
                (sym, pct) for sec in sectors for sym, pct in sec.stocks.items())))
        for sec in sectors:      # the whole page, so other selection rules can be tested later
            for sym, pct in sec.stocks.items():
                csv_append(self.dir / "booster.csv", {"time": ts(now), "sector": sec.name,
                                                       "sector_pct": sec.pct, "symbol": sym, "pct": pct})
        source = sel.get("sector_source", "dhan")
        if source == "dhan":
            sectors = self.dhan_sector_list()
            for sec in sectors:
                src = "index" if self.index_ids.get(sec.name) else "members_avg"
                for sym, pct in sec.stocks.items():
                    csv_append(self.dir / "dhan_sectors.csv", {
                        "time": ts(now), "sector": sec.name, "sector_pct": sec.pct,
                        "sector_from": src, "symbol": sym, "pct": pct})
            if not sectors:
                log.warning("no sector could be priced from Dhan this cycle (feed down?)")
        label = self.sentiment.label if self.sentiment else ""
        sector = pick_sector(sectors, label, set(sel["exclude_sectors"])) if sectors else None
        if not sectors or self.sentiment is None:
            # A failed read keeps the last good list rather than emptying it.
            log.warning("booster or sentiment unavailable; tracking list kept")
            names, note, picked = self.tracked, "read failed; list kept", self.picked
        elif sector is None:
            names, note, picked = [], f"sentiment {label}: no direction, nothing tracked", {}
        else:
            ranked = pick_stocks(sector, label, sel["tracked_names"])
            names, note = [s for s, _ in ranked], ""
            picked = {s: (i + 1, pct) for i, (s, pct) in enumerate(ranked)}
        if sectors and self.sentiment is not None:
            self.side = LONG if label == "BULLISH" else SHORT if label == "BEARISH" else None
            self.sector = sector

        added = [n for n in names if n not in self.tracked]
        removed = [n for n in self.tracked if n not in names]
        self._gate_check("booster", BOOSTER_URL)
        row = {"time": ts(now), "source": source,
               "login_gate": self.scraper.gated.get(BOOSTER_URL, ""), "sentiment": label,
               "sentiment_pct": self.sentiment.pct if self.sentiment else "",
               "side": self.side or "", "sector": sector.name if sector else "",
               "sector_pct": sector.pct if sector else ""}
        for i in range(sel["tracked_names"]):
            sym = names[i] if i < len(names) else ""
            row[f"stock{i + 1}"] = sym
            row[f"stock{i + 1}_pct"] = picked.get(sym, (0, ""))[1] if sym else ""
        row.update({"added": " ".join(added), "removed": " ".join(removed), "note": note,
                    "all_sectors": " | ".join(f"{s.name} {s.pct:+.2f}" for s in
                                              sorted(sectors, key=lambda s: -s.pct))})
        csv_append(self.dir / "tracking.csv", row)
        if added or removed:
            say(f"LIST {self.side or '-'} {sector.name if sector else '-'}: {' '.join(names) or '(empty)'}"
                + (f"  +{' +'.join(added)}" if added else "") + (f"  -{' -'.join(removed)}" if removed else ""))
        self.tracked, self.picked = names, picked
        for sym in added:
            self.tracked_since[sym] = now
        for sym in removed:
            self.setups.pop(sym, None)
            self.tracked_since.pop(sym, None)
            self.note(now, sym, "UNTRACKED", "")
        for sym, st in list(self.setups.items()):
            if st.side != self.side:
                self.setups.pop(sym)
        missing = [n for n in names if n not in self.sids]
        self.sids.update(dhan.security_ids(missing))
        for n in names:
            if n not in self.sids:
                log.warning("%s is not in the instrument master; it cannot be traded", n)

    # --- trading ------------------------------------------------------------------
    async def _bars_all(self, symbols: list[str], boundary: datetime) -> dict:
        """Bars for every symbol at once, waiting (shared deadline) for the
        just-closed bar to be served."""
        since = at(self.day - timedelta(days=self.cfg["strategy"]["history_days"]), time(9, 0))
        want = boundary - self.step
        deadline = now_ist() + timedelta(seconds=self.bar_wait)
        out: dict = {}
        pending = list(symbols)
        while pending:
            got = await asyncio.gather(*(self.bars.fetch(self.sids[s], since, boundary) for s in pending),
                                       return_exceptions=True)
            still = []
            for sym, bars in zip(pending, got):
                if isinstance(bars, Exception):
                    log.warning("%s: bar fetch failed: %s", sym, bars)
                    continue
                out[sym] = bars
                if not bars or bars[-1].start < want:
                    still.append(sym)
            if not still or now_ist() >= deadline:
                for sym in still:
                    log.warning("%s: bar %s not served yet; using what Dhan has", sym, want.strftime("%H:%M"))
                break
            pending = still
            await asyncio.sleep(10)
        return out

    async def evaluate(self, boundary: datetime) -> None:
        names = list(dict.fromkeys(self.tracked + list(self.plan.open) + list(self.all.open)))
        names = [n for n in names if n in self.sids]
        all_bars = await self._bars_all(names, boundary)
        for sym, bars in all_bars.items():
            points = with_indicators(bars, self.cfg["strategy"]["ema_length"])
            today = [p for p in points if p.bar.start.date() == self.day]
            self._log_bars(sym, today)
            now = now_ist()
            for book in self.books:
                if sym in book.open:
                    self._manage(book, now, sym, today)
            if sym in self.tracked and self.side:
                self._look_for_entry(now, sym, points, boundary)

    def _log_bars(self, sym: str, today) -> None:
        last = self.last_logged_bar.get(sym)
        setup = self.setups.get(sym)
        for p in today:
            if last and p.bar.start <= last:
                continue
            b = p.bar
            csv_append(self.dir / "bars.csv", {
                "symbol": sym, "bar": b.start.strftime("%Y-%m-%d %H:%M"), "open": b.open,
                "high": b.high, "low": b.low, "close": b.close, "volume": b.volume,
                "ema9": round(p.ema, 3) if p.ema else "", "vwap": round(p.vwap, 3) if p.vwap else "",
                "setup": setup.mode if setup else ""})
            self.last_logged_bar[sym] = b.start

    def _manage(self, book: Book, now: datetime, sym: str, today) -> None:
        t = book.open[sym]
        last = datetime.strptime(t["last_trailed_bar"], "%Y-%m-%d %H:%M:%S")
        for p in today:
            if p.bar.start <= last:
                continue
            fill = bar_stop_fill(t["side"], t["stop"], p.bar)
            if fill is not None:
                self._exit(book, now, sym, fill, "STOP", "BAR")
                return
            level = trail_level(p, t.get("trail_with", "vwap"))   # pre-2026-10-09 trades: vwap
            book.move_stop(now, sym, trail(t["side"], t["stop"], level), p.bar.start)
            log.debug("[%s] %s stop -> %.2f", book.name, sym, t["stop"])
        # The trailed stop can sit beyond the price: a long whose bar closed
        # under its trail line is now under its own stop, and is out.
        live = self.feed.price(t["security_id"]) if self.feed else None
        if live is not None:
            price, source = live, "LIVE"
        elif today:
            price, source = today[-1].bar.close, "BAR_CLOSE"
        else:
            return
        if stop_hit(t["side"], t["stop"], price):
            self._exit(book, now, sym, price, "STOP_TRAILED_PAST_PRICE", source)

    def _exit(self, book: Book, now: datetime, sym: str, price: float, reason: str, source: str) -> None:
        t = book.exit(now, sym, price, reason, source)
        if book is self.plan:
            say(f"PLAN EXIT  {t['side']} {sym} @ {t['exit_price']:.2f} {reason} "
                f"net {money(t['net_pnl'])} ({t['r_multiple']}R, {t['minutes_in_trade']:.0f} min)")
        else:
            log.info("[all] exit %s %s net %.2f", sym, reason, t["net_pnl"])

    def _context(self, now: datetime, sym: str) -> dict:
        s = self.sentiment
        rank, bpct = self.picked.get(sym, (None, None))
        since = self.tracked_since.get(sym)
        return {
            "time_of_day": now.strftime("%H:%M"),
            "minutes_since_open": int((now - at(self.day, time(9, 15))).total_seconds() // 60),
            "sentiment": s.label if s else "", "sentiment_pct": s.pct if s else "",
            "pcr": s.pcr if s else "", "sentiment_flips_today": self.sentiment_flips,
            "sector": self.sector.name if self.sector else "",
            "sector_pct": self.sector.pct if self.sector else "",
            "stock_rank_in_sector": rank, "stock_booster_pct": bpct,
            "minutes_tracked": int((now - since).total_seconds() // 60) if since else "",
            "nifty_day_pct": round(self.nifty_pct, 3) if self.nifty_pct is not None else "",
        }

    def _look_for_entry(self, now: datetime, sym: str, points, boundary: datetime) -> None:
        setup = self.setups.get(sym)
        if setup is None:
            setup = Setup.start(sym, self.side, points)
            self.setups[sym] = setup
            self.note(now, sym, "ARMED", f"{self.side} {setup.mode} after bar {setup.armed_after:%H:%M}")
            return
        before = setup.mode
        signals = setup.update(points)
        if setup.mode != before:
            self.note(now, sym, "SETUP", f"{before} -> {setup.mode}")
        latest = boundary - self.step
        starts = [p.bar.start for p in points]
        for sig in signals:
            self.signal_seq += 1
            live = self.feed.price(self.sids[sym]) if self.feed else None
            ref, source = (live, "LIVE") if live is not None else (sig.price, "BAR_CLOSE")
            # Rules that apply to every book.
            why = None
            if sig.bar.start < latest:
                why = f"stale: bar {sig.bar.start:%H:%M} was not the latest"
            elif sig.bar.start + self.step < self.t_first_entry:
                why = "no entries before " + self.t_first_entry.strftime("%H:%M")
            elif sig.bar.start + self.step >= self.t_no_entry:
                why = "no new entries from " + self.t_no_entry.strftime("%H:%M")
            elif not stop_is_valid(sig.side, ref, sig.stop):
                why = f"price {ref:.2f} already through stop {sig.stop:.2f}"
            features = entry_features(points, starts.index(sig.bar.start), sig.side, sig.stop)
            extra = {"signal_id": f"{self.day}-s{self.signal_seq:03d}", **self._context(now, sym), **features}
            outcome = {}
            for book in self.books:
                b_why = why
                if b_why is None and sym in book.open:
                    b_why = "already in a trade"
                if b_why is None and book is self.plan and book.taken_today >= self.plan_cap:
                    b_why = f"plan cap of {self.plan_cap} trades reached"
                if b_why:
                    book.decision(now, sym, f"SKIP_{sig.kind}", b_why)
                    outcome[book.name] = "SKIP: " + b_why
                    continue
                t = book.enter(now, sig, ref, self.sids[sym], source, extra)
                outcome[book.name] = f"TAKEN {t['id']}" if t else "REFUSED"
                if t and book is self.plan:
                    say(f"PLAN ENTER {sig.side} {sym} {sig.kind} @ {t['entry_price']:.2f} ({source}) "
                        f"stop {sig.stop:.2f} qty {t['qty']} risk {features['risk_pct']}% "
                        f"angle {features['angle_between_deg']}deg  [{book.taken_today}/{self.plan_cap}]")
            csv_append(self.dir / "signals.csv", {
                **extra, "symbol": sym, "side": sig.side, "kind": sig.kind,
                "signal_bar": ts(sig.bar.start), "close": sig.price, "stop": sig.stop,
                "ref_price": round(ref, 2), "ref_source": source,
                "ema": round(sig.ema, 3), "vwap": round(sig.vwap, 3),
                "plan": outcome.get("plan", ""), "all": outcome.get("all", "")})

    async def square_off(self, now: datetime, late: bool = False) -> None:
        """Close everything at 15:00.

        `late`: the Mac was asleep through 15:00 (lid closed), so the cycle
        never ran. The rule is still applied as written, from the candles:
        stops are checked and trailed on every candle the process missed up
        to 15:00, and whatever survives exits at the 14:55 candle's close --
        the 15:00 price -- stamped 15:00 and marked SQUARE_OFF_LATE. Never at
        the post-close price that happens to be live when the lid opens."""
        self.squared_off = True
        for book in self.books:
            for sym in list(book.open):
                t = book.open[sym]
                if late:
                    await self._late_square_off(book, sym, t)
                    continue
                live = self.feed.price(t["security_id"]) if self.feed else None
                if live is None:
                    bars = await self.bars.fetch(t["security_id"], at(self.day, time(9, 0)), now)
                    live, source = (bars[-1].close, "BAR_CLOSE") if bars else (t["entry_ref"], "NO_PRICE")
                else:
                    source = "LIVE"
                self._exit(book, now, sym, live, "SQUARE_OFF_1500", source)

    async def _late_square_off(self, book: Book, sym: str, t: dict) -> None:
        bars = await self.bars.fetch(t["security_id"], at(self.day, time(9, 0)), self.t_square)
        points = [p for p in with_indicators(bars, self.cfg["strategy"]["ema_length"])
                  if p.bar.start.date() == self.day]
        last = datetime.strptime(t["last_trailed_bar"], "%Y-%m-%d %H:%M:%S")
        for p in points:
            if p.bar.start <= last:
                continue
            fill = bar_stop_fill(t["side"], t["stop"], p.bar)
            if fill is not None:
                self._exit(book, p.bar.start + self.step, sym, fill, "STOP", "BAR_LATE")
                return
            level = trail_level(p, t.get("trail_with", "vwap"))
            book.move_stop(p.bar.start + self.step, sym, trail(t["side"], t["stop"], level), p.bar.start)
        if points:
            self._exit(book, self.t_square, sym, points[-1].bar.close, "SQUARE_OFF_LATE", "BAR_1500")
        else:
            self._exit(book, self.t_square, sym, t["entry_ref"], "SQUARE_OFF_LATE", "NO_PRICE")

    def on_tick(self, sid: str, ltp: float, _wall: float) -> None:
        if self.squared_off:
            return
        for book in self.books:
            for sym, t in list(book.open.items()):
                if t["security_id"] == sid and stop_hit(t["side"], t["stop"], ltp):
                    self._exit(book, now_ist(), sym, ltp, "STOP", "LIVE")

    async def _sync_feed(self) -> None:
        if not self.feed:
            return
        wanted = {self.sids[s] for s in self.tracked if s in self.sids} | set(self.universe_keys)
        for book in self.books:
            wanted |= {t["security_id"] for t in book.open.values()}
        await self.feed.set_instruments(wanted)

    async def finalize(self) -> None:
        try:
            await enrich(self.day, self.cfg, self.books, self.bars)
        except Exception:  # noqa: BLE001
            log.exception("excursion analysis failed; re-run with --enrich %s", self.day)
        rows = write_summaries(self.dir, self.books)
        append_daily_ledger(DATA, rows)
        for r in rows:
            say(f"DAY DONE [{r['book']}] {r['trades']} trades, {r['wins']} won, gross {money(r['gross_pnl'])}, "
                f"charges {money(r['charges'])}, net {money(r['net_pnl'])}")


async def enrich(day: date, cfg: dict, books, bars_client=None) -> None:
    """Add MFE/MAE, R reached before the stop, and R-if-held-to-15:00 to every
    closed trade -- the data an exit rule is optimised on."""
    bars_client = bars_client or dhan.Bars()
    cache: dict[str, list] = {}
    square = at(day, hhmm(cfg["schedule"]["square_off_at"]))
    for book in books:
        for t in book.closed:
            sid = t["security_id"]
            if sid not in cache:
                hist = await bars_client.fetch(sid, at(day - timedelta(days=7), time(9, 0)), square)
                cache[sid] = [p for p in with_indicators(hist, cfg["strategy"]["ema_length"])
                              if p.bar.start.date() == day]
            signal = datetime.strptime(t["signal_bar"], "%Y-%m-%d %H:%M:%S")
            exit_at = datetime.strptime(t["exit_time"], "%Y-%m-%d %H:%M:%S") - timedelta(minutes=5)
            after = [p for p in cache[sid] if p.bar.start > signal]
            t.update(excursions(t["side"], t["entry_price"], t["initial_stop"], [p.bar for p in after], exit_at))
            # The same entry under each trail, so the two can be compared on
            # identical trades (bar-based, gross R).
            for how in ("ema9", "vwap"):
                t[f"r_trail_{how}_bars"] = simulate_trail(t["side"], t["entry_price"],
                                                          t["initial_stop"], after, how)
        book.write_trades()
        book.save()


def acquire_lock():
    LOGS.mkdir(parents=True, exist_ok=True)
    fh = open(LOGS / ".run.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another copy is already running (logs/.run.lock is held). Not starting a second one.")
        sys.exit(1)
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


async def check(cfg: dict) -> None:
    """One pass, printed: both pages, the pick, and each name's EMA/VWAP state."""
    s = StockMojo(cfg["scrape"]["headless"], cfg["scrape"]["page_timeout_seconds"])
    await s.start()
    try:
        sent = await s.sentiment()
        say(f"sentiment: {sent and (sent.label, sent.pct, sent.pcr)}")
        sectors = await s.sectors()
        say("sectors: " + " | ".join(f"{x.name} {x.pct:+.2f}" for x in sorted(sectors, key=lambda x: -x.pct)))
    finally:
        await s.close()
    if not sent:
        return
    sector = pick_sector(sectors, sent.label, set(cfg["selection"]["exclude_sectors"]))
    if not sector:
        say("no sector picked")
        return
    picks = pick_stocks(sector, sent.label, cfg["selection"]["tracked_names"])
    say(f"sector: {sector.name} {sector.pct:+.2f}  ->  {picks}")
    await dhan.reload_credentials()
    bars_client = dhan.Bars()
    sids = dhan.security_ids([p for p, _ in picks])
    side = LONG if sent.label == "BULLISH" else SHORT
    now = now_ist()
    for sym, _ in picks:
        if sym not in sids:
            say(f"  {sym}: no security id")
            continue
        bars = await bars_client.fetch(sids[sym], now - timedelta(days=7), now)
        pts = with_indicators(bars, cfg["strategy"]["ema_length"])
        last = pts[-1]
        st = Setup.start(sym, side, pts)
        say(f"  {sym} sid {sids[sym]}: {len(bars)} bars, last {last.bar.start:%Y-%m-%d %H:%M} "
            f"close {last.bar.close} ema9 {last.ema:.2f} vwap {last.vwap:.2f} -> {st.mode}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(EXP / "conf" / "config.yaml"))
    ap.add_argument("--check", action="store_true", help="one read of both pages and bars, then exit")
    ap.add_argument("--enrich", metavar="YYYY-MM-DD", help="recompute a past day's trade excursions")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    logsetup.setup(LOGS)

    if args.check:
        await check(cfg)
        return 0
    if args.enrich:
        day = date.fromisoformat(args.enrich)
        await dhan.reload_credentials()
        rates = RateCard.load(EXP / cfg["costs"]["rate_card"])
        books = [Book(n, DATA / day.isoformat(), cfg, rates) for n in ("plan", "all")]
        await enrich(day, cfg, books)
        append_daily_ledger(DATA, write_summaries(DATA / day.isoformat(), books))
        say(f"enriched {day}: {[len(b.closed) for b in books]} trades (plan, all)")
        return 0

    lock = acquire_lock()  # noqa: F841 -- held for the life of the process
    say(f"sentiment-sector-vwap started, pid {os.getpid()}. Console: 1 line per 5-min cycle + trades. "
        "Details in logs/run.log and logs/debug.log; data in data/.")
    start, end = hhmm(cfg["schedule"]["session_start"]), hhmm(cfg["schedule"]["session_end"])
    wake = (datetime.combine(date.min, start) - timedelta(minutes=2)).time()
    done: date | None = None
    while True:
        now = now_ist()
        today = now.date()
        if (now.weekday() >= 5 or today == done
                or now.time() > (datetime.combine(today, end) + timedelta(minutes=2)).time()):
            nxt = today + timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
            await sleep_until(at(nxt, wake), "(market closed)")
            continue
        if now.time() < wake:
            await sleep_until(at(today, wake), "(before the open)")
            continue        # re-check the clock: the lid may have been opened after the close
        try:
            await Day(cfg, today).run()
        except Exception:  # noqa: BLE001 -- the process must outlive a bad day
            log.exception("session %s crashed; sleeping until the next one", today)
        done = today


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nstopped.")
