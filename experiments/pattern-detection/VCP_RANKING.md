# Does it matter which candidate gets the slot?

**Answer: no, not reliably.** This was the largest open item in
`FINAL_LOGIC.md` and it turned out to be a negative result. Recorded here
because a negative result that is not written down gets re-investigated.

```bash
.venv/bin/python scripts/vcp_ranking.py
```

---

## 1. The setup

79% of signals are skipped for want of a free slot, and signals cluster —
mean 3.0 per day, 73% arriving alongside two or more others, up to 22 at
once. So the choice of who gets the slot is a real one, made on ~1,050 days.

Seven ranking keys were tested, all computable at the signal bar, each also
run in exact reverse as a sign check, against two baselines: **date order**
(first-come-first-served, what `FINAL_LOGIC.md` assumed) and **random
allocation** over 40 seeds.

The random spread is the yardstick: mean 21.5% CAGR, sd 1.54%, and the
absolute difference between two random allocations has a median of 1.30% and
a 95th percentile of **4.47%**. Any ranking key that beats date order by less
than about four and a half points of CAGR has not demonstrated anything.

## 2. Results

12 slots, 1% risk, 0.3% round-trip cost, rule `early entry + EMA50 trail`.

| Allocation | CAGR | Max DD | Taken | vs date order |
|---|---|---|---|---|
| **date order (baseline)** | 21.3% | 15.8% | 991 | — |
| **random, mean of 40 seeds** | 21.5% | 17.2% | 971 | +0.2% |
| highest detector score first | 19.8% | 17.4% | 1,027 | −1.5% |
| **lowest detector score first** | **24.4%** | 16.5% | 977 | +3.2% |
| **strongest 6m relative strength first** | **24.4%** | **15.4%** | 1,001 | +3.1% |
| weakest relative strength first | 19.6% | 15.4% | 988 | −1.7% |
| tightest range contraction first | 22.3% | 16.9% | 1,014 | +1.1% |
| driest final contraction first | 21.5% | 16.7% | 1,038 | +0.2% |
| closest to the pivot first | 21.4% | 15.1% | 980 | +0.2% |
| furthest from the pivot first | 23.1% | 15.9% | 987 | +1.8% |
| best reward:risk first | 20.7% | 15.2% | 1,117 | −0.6% |
| most contractions first | 22.0% | 16.2% | 1,019 | +0.8% |

**Date order was not costing anything.** Random allocation matches it to
within 0.2%, so the arbitrary rule `FINAL_LOGIC.md` used was not a
handicap — which was the worry that prompted this.

## 3. The paired test, and why the two apparent winners fail it

Comparing each key to the random *mean* is weak. The stronger test is the
spread between a key and its exact reverse, judged against the distribution
of differences between two random allocations:

| Key | Forward | Reversed | Spread | p |
|---|---|---|---|---|
| detector score (desc) | 19.8% | 24.4% | **−4.7%** | **0.049** |
| RS, 63-day | 22.7% | 21.3% | +1.3% | 0.482 |
| **RS, 126-day** | 24.4% | 19.6% | **+4.8%** | **0.042** |
| RS, 252-day | 18.4% | 20.3% | −1.9% | 0.342 |
| squeeze | 21.0% | 22.3% | −1.4% | 0.464 |

Two keys land at p ≈ 0.04–0.05. Neither survives scrutiny:

**Eleven keys were tested.** At p < 0.05 you expect roughly half a false
positive from eleven tries; two marginal hits is close to what noise alone
produces. Corrected for multiplicity, neither is significant.

**Relative strength only works at one lookback.** 126 days gives +4.8%;
63 days gives +1.3% and 252 days gives −1.9% — the wrong sign. A real
momentum effect would not appear at six months and vanish at three and
twelve. Having tried three lookbacks, the p of 0.042 on the winner is closer
to 0.12.

**RS is also not stable across slot counts.** Against the random mean:
−0.6% at 5 slots, +2.2% at 8, +2.9% at 12, +3.1% at 20. Positive where it
matters but negative at the tightest setting.

## 4. The one thing worth keeping

**Do not rank by the detector's score.** Highest-score-first is the worst of
the eleven keys and lowest-score-first is among the best, a 4.7-point spread
in the wrong direction. That is consistent with what was already measured
directly: the score/outcome correlation is +0.024, and the score measures
conformity to the textbook rather than anything about what happens next.
Using it to prioritise is mildly counterproductive.

Whether the *reverse* is genuinely useful — deliberately preferring the
scruffier setups — is not established and would need its own out-of-sample
test before anyone acted on it. It is the kind of result that is usually
noise and occasionally a real finding about crowded trades.

## 5. What this changes in `FINAL_LOGIC.md`

Open item 1 is closed: slot allocation is **not** the large hidden lever it
looked like. Keep date order, which is simple and costs nothing. Remove the
detector score from any prioritisation role.

The 79% skip rate remains, but it is now evidence that the binding
constraint is *capital*, not *selection* — the signals you miss are, on this
evidence, no worse than the ones you take.
