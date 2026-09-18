# Universes

A **universe** is a named list of symbols a strategy may trade. One CSV per
universe, referenced from a strategy module by `universe.file`.

It is a file and not a scrape on purpose: refreshing it is a manual copy, so
this application gains no new outbound host and no new URL in
`tests/test_no_real_orders.py`'s allowlist.

## Format

```
symbol,security_id,name,exchange_segment
360ONE,13061,360 ONE WAM Ltd.,NSE_EQ
```

Only `symbol` is required and only `symbol` is authoritative.

**`security_id` in this file is NOT used to trade.** Security ids are resolved
from Dhan's instrument master by symbol, every refresh, because the master is
the only thing that is current. The column is kept so a divergence can be
*reported* rather than discovered as a wrong price: the instrument master
service logs a warning naming every symbol whose declared id differs from the
master's.

That warning is not theoretical. Checked on 2026-09-18 against the live master,
four of the 500 declared ids in `nifty500.csv` disagree:

| Symbol | Declared here | Master (NSE EQUITY) | What the declared id is in today's master |
|---|---|---|---|
| CHOLAFIN | 685 | 685 (EQ) — also 19257 (D1, an NCD) | correct; the file names the share |
| MOTHERSON | 4204 | 4204 (EQ) — also 25510 (D1, an NCD) | correct; the file names the share |
| HEG | 1336 | **7368** (BE) | a USDJPY currency option |
| HFCL | 21951 | **21954** (BE) | not an NSE EQUITY row |

HEG and HFCL matter: **HFCL is rank 2 in the specification's §13 snapshot.**
Whether those ids were correct when the research panel was downloaded in July
2026 and have since changed, or were wrong then, has **not been verified** —
see the "Known gaps" section of the root `README.md`.

## nifty500.csv

- **Source:** copied verbatim from
  `~/Workarea/local/pullback/backend/intrday_test_strategy/data/universe_nifty500.csv`
  on 2026-09-18. 500 rows.
- **Upstream:** NSE publishes the current constituents at
  `https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv`.
  That archive host serves the file to scripted clients; `www.nseindia.com`'s
  API returns 503 to them.
- **Refresh:** quarterly, by hand, after NSE's index review. Download the CSV
  above, keep the `symbol` column, and leave the `security_id` column alone or
  drop it — the master is what resolves ids.
- **Membership is not point-in-time.** This is today's list applied to the
  past, which is the source of the survivorship bias the strategy
  specification warns about. Fixing it needs paid historical
  index-constituent data.

### Symbols that do not resolve

Checked 2026-09-18 against the live master: **499 of 500** resolve to an NSE
EQUITY row in series EQ or BE. `JBCHEPHARM` has no NSE row in that snapshot at
all. The instrument master service reports unresolved symbols as warnings
rather than dropping them silently — a universe that quietly shrinks still
produces a ranking, just a different one.
