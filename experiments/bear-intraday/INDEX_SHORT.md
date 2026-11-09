# INDEX_SHORT — the key control, judged as a strategy: **FAILS**

**Rule:** on every regime-B-on day, short NIFTY futures:
- decided at the 09:30 close;
- filled at the bar-3 open, or at the first tradeable bar after a halt;
- covered at the bar-71 close;
- no stop.

The spot index stands in for the future. Costs: the date-keyed futures card
(`conf/index_futures_costs.yaml`) at the real lot size. Slippage is 1 bp per
side. Source: `out/dev_index_short.json`.

## Development, 2017-04 → 2022-12, 364 regime-on days

| | Value |
|---|---|
| Gross per day (after 1 bp/side) | **−6.2 bps** |
| Charges | 2.3 bps |
| **Net @ 1 bp** | **−8.5 bps, CI [−23.3, +6.3]** |
| Net @ 2 / 5 bps | −10.5 / −16.5 |
| After dropping the best 10 days | −18.3 |
| Without 2020-02 → 05 | −7.8 |
| Episodes positive | **3 / 8** |
| Years positive | 0 / 6 |
| Same rule on regime-**off** days | +0.7 bps, CI [−3.3, +4.9] |

**Portfolio** (floor(₹25 L ÷ lot notional) lots, every regime-on day):
- **30%** of traded months positive (11 of 37);
- worst month **−31% of capital** (2020-03), longest losing streak 4;
- cumulative **−135% of capital**. Sized at the 5× cap with no stop, the
  account would not have survived.

At 1 lot: −43% of capital in total, worst day −21%.

## Why it loses: the fall happens before 09:30

`scripts/decompose_day.py` (development only, an exploratory diagnostic) splits
NIFTY's spot move on regime-on days into four legs. Mean bps per day:

| Leg | Regime on (364 d) | Regime off (1,055 d) |
|---|---|---|
| Overnight (previous close → 09:15 open) | +5.2 | +16.2 |
| 09:15 → 09:30 (before any decision) | **−8.8** | −3.5 |
| **09:30 → 15:10 (what an intraday short holds)** | **+4.2** | −4.9 |
| 15:10 → close | +1.9 | −0.3 |

**On bear-regime days, the part of the session a rule-based intraday short
can hold drifts up, not down.** The down-move is concentrated in the first 15
minutes, before the 09:30 decision the screener's ground rules require. The
relief rallies of `REGIME.md` sit inside the held window. 2020-03-13 is the
extreme case: the market was halted at the lower circuit at 09:20, reopened at
10:20, and closed 16.6% above the reopen.

## Pass bar

| Condition | Result |
|---|---|
| 1. Net > 0, CI clear | **no** (−8.5) |
| 3. Majority of episodes | **no** (3/8) |
| 4. > 0 after the best 10 days | **no** |
| 5. Monthly bar | **no** (30%, −31%, streak 4) |

**"Short NIFTY every bear day" is not the edge.** It's a loss, which also
makes it an **easy** control to beat. Beating it says little.

## Robustness (pre-registered variants)

| Regime | Days | Gross | Net @ 1 bp | Episodes + |
|---|---|---|---|---|
| A sma200 | 140 | −20.4 | −22.7 [−58, +10] | 0/3 |
| **B breadth** | 364 | −6.2 | −8.5 [−23, +6] | 3/8 |
| C drawdown | 195 | −21.0 | −23.3 [−51, +1] | 0/4 |

The later rules (A and C) do far worse. That matches `REGIME.md`'s timing:
they are on through the rebounds.

## The harness bug this run found

The first run "entered" on 2020-03-13 at 8,630.75, during the market-wide halt.
The vendor prints a halt as flat bars, not missing ones. `engine.market_halt`
now treats flat index bars, plus the three pre-open bars after a run of them, as
untradeable for every instrument. Six days are affected. The headline barely
moved (net @ 5 bps −16.2 → −16.5), and the corrected figure is the one above.
