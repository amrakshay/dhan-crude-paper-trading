"""Sign in to stockmojo.in ONCE, by hand, in a visible browser window.

Opens Chromium on a persistent profile (experiments/sentiment-sector-vwap/
.browser-profile, git-ignored) at stockmojo.in. Sign in yourself, check that
the "Login Required" banner has gone, then close the window. The live run
reuses that session from then on; this code never sees or types a password.

    ./start.sh login        (stop a running copy first: ./start.sh stop)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from svlib.scrape import OI_URL, PROFILE  # noqa: E402


async def main() -> None:
    from playwright.async_api import async_playwright
    PROFILE.mkdir(exist_ok=True)
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(PROFILE), headless=False, viewport={"width": 1440, "height": 1000})
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(OI_URL)
        print("A browser window is open. Sign in to stockmojo there, then CLOSE THE WINDOW.")
        closed = asyncio.Event()
        ctx.on("close", lambda *_: closed.set())
        await closed.wait()
    print(f"Saved the session in {PROFILE}. Start the run again: ./start.sh --bg")


if __name__ == "__main__":
    asyncio.run(main())
