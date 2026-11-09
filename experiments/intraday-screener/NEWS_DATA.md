# Data check: corporate announcements for a news-filtered gap fade (2026-09-28)

**The question:** can we get NSE/BSE corporate announcements for the 210 F&O
names, 2017 onward, with timestamps precise enough to say "public before
09:15", legitimately?

## What exists

| Source | Fields | Timestamp | History | Can we use it? |
|---|---|---|---|---|
| NSE `/api/corporate-announcements` (behind nseindia.com's filings page) | `symbol`, `desc` (category), `attchmntText` (one-line summary), PDF link | `exchdisstime` **to the second** ("18-Oct-2023 22:11:48") | date-range parameters exist; depth **unverified** | **No.** NSE's Terms of Use (read 2026-09-28 at nseindia.com/static/nse-terms-of-use): "User is prohibited to conduct any systematic or automated data collection activities (including scraping…)". The site also rejects non-browser clients. |
| BSE `AnnSubCategoryGetData` (behind bseindia.com's announcements page) | `SUBCATNAME`, `HEADLINE`, PDF | `DissemDT` **to the second** | depth **unverified** | **Unconfirmed.** Automated fetches of BSE's disclaimer return 403, the documented disclaimer URL returns 404, and no terms link appears on the home page. Given NSE's explicit rule and the same blocking, assume the same until BSE's terms are read. |
| stockinsights.ai "Announcements Tagged Feed" | ticker (NSE+BSE), category (26 types), **AI summary and sentiment** | `published_date`, UTC | **not documented** | Licensed product; pricing and depth need asking |
| APIdatafeed "Announcement NSE EOD" | company, caption, date, memo | DateTime, precision unstated | not documented | EOD delivery, so no use for a same-morning decision; subscription |
| TrueData | real-time NSE/BSE filings, REST + WebSocket | real time | not stated | licensed vendor; history and price need asking |

The field names above come from the sample responses in the unofficial
wrappers (BennyThadikaran/NseIndiaApi and BseIndiaApi on GitHub). **This check
fetched nothing from either exchange's API.**

## What that means for the idea

- **The data needed exists** at the right precision: exchange dissemination
  time to the second.
- **The free route is closed.** NSE forbids automated collection in writing,
  and building on it would also put a banned source into a repo whose rules
  are about containment (`test_outbound_hosts.py`).
- **A historical test needs a licensed vendor** that can supply 2017-onward
  history. None of them documents its history depth publicly.
- **A forward-only test needs only a live licensed feed.** It is slower, but it
  has no lookahead at all.
- **stockinsights.ai already sells LLM-generated categories and sentiment.**
  If it's bought, the vendor's labels are what get tested. Whether those
  labels were produced at publication time or back-filled later decides
  whether they can be backtested; that has to be asked.
