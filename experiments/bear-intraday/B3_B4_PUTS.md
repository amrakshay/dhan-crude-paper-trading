# B3 Index put / B4 bear put spread: **UNTESTED** (gated on 0d)

The option track needs **intraday** option prices, and none has been obtained
honestly yet. See `FEASIBILITY.md`: the endpoint exists in Dhan's
documentation, but the probe was refused because the stored token had expired.

Nothing is synthesised in the meantime. The handoff's third path (Black-Scholes
off the 5-minute underlying, with the IV anchored to the bhavcopy) would assume
the intraday IV path, and the put buyer is paid for exactly that path.

## What the futures track already says about B3

B3's trigger is B1's applied to NIFTY: the first close below the opening range
on a regime-on day. **A put is worth buying only if the identical futures
short makes money and the put beats it after theta, skew and charges.** The
evidence so far:

- **Shorting NIFTY 09:30 → 15:10 on every regime-on day loses** (−6.2 bps gross;
  `INDEX_SHORT.md`). The held window drifts up on bear days.
- **A long put loses that drift, plus theta, plus the skew premium.** The
  hedged-option-selling work measured a 16-delta put at 12.8% IV against 10.6%
  for the call. The relief rallies that sit inside the regime are also the days
  the put's IV collapses.

The B3 futures leg (an opening-range-breakdown short on NIFTY) was **not run**.
It's B3's comparison leg, and running it without the put would add a sixth
cash/futures test that wasn't registered as one. When 0d unblocks, B3 runs both
legs together, as `METHOD.md` defines.

## To unblock

1. Save a fresh Dhan token on the Settings page.
2. Run the probe. It makes 8 requests and stops at the first 401:

   ```bash
   cd backend && CONFIG_PATH=conf .venv/bin/python ../experiments/bear-intraday/scripts/probe_expired_options.py
   ```

3. If it returns 5-minute ATM put bars with OI and IV, report the URL to the
   operator before any bulk download. Per Dhan's docs that is 5 years of
   history, so about 2021-10 onward.
4. **Development data would be thin.** Five years back from 2026-10 leaves
   2021-10 → 2022-12 in development: one bear episode (2021-11 → 2022-07). The
   rest falls in the holdout. A put result could not satisfy "majority of
   development episodes" on its own data. Say so before running, not after.
