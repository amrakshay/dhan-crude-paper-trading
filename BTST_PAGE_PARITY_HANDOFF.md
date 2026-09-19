# Handoff — bring `/btst`'s tabs up to `/swing`'s standard

**Written:** 2026-09-19, by the session that built `/btst`.
**Status:** the BTST module works and is tested; its PAGE is thinner than the
rotation's and that is the whole job here.

---

## The ask, in one sentence

Compare the Swing Momentum page's tabs against the BTST Overnight page's tab by
tab, in detail, and bring BTST's up to the same standard — **without copying
the parts of swing that are true only of swing.**

---

## Read these first, in this order

1. `frontend/CLAUDE.md` — especially **§3 (honesty in the UI)**, **§5d (the
   Swing Momentum page)** and **§5e (the BTST Overnight page)**. §5d is the
   contract the rotation's page already meets; §5e is what BTST claims to meet.
   Much of this job is making §5e true.
2. `backend/src/btst/README.md` — what this strategy IS, and why it differs.
3. `backend/src/swing/README.md` — the same for the rotation.
4. The strategy specification, for any number that appears on screen:
   `~/Workarea/local/pullback/backend/intrday_test_strategy/research2/BTST_OVERNIGHT_HANDOFF.md`

**Do not re-derive the strategy.** It is built, tested and committed
(`4e1343c`). This is a presentation and payload job.

---

## The evidence — this is not a matter of taste

Measured on 2026-09-19:

| | Swing | BTST |
|---|---:|---:|
| `pages/SwingMomentumPage.jsx` / `BtstOvernightPage.jsx` | 1340 | 638 |
| Configuration tab | 611 | 311 |
| "How it works" tab | 839 | 228 |
| Health tab | 1350 | 306 |
| Signals tab | — | 252 |
| Shared funnel component | — | 62 |
| **Total** | **4140** | **1797** |

Size alone proves nothing. What follows is what the size is hiding.

---

## GAP 1 — the one that is a DEFECT, not a polish item

**`btst-exit-incomplete` has nowhere to appear.**

That alert is `strategy_scoped=True` in `alert_catalogue.py`, and
`frontend/CLAUDE.md` §4a says a strategy's own tab shows the rules about that
strategy while process-wide ones stay on System Health. The rotation's page
renders `AlertsPanel`; **BTST's page does not render it at all.**

So the most important alert in the module — *a position is still held after the
exit ran*, the one guarding the thing section 10.1 says IS the strategy —
is raised, stored, de-duplicated and delivered, and then cannot be seen on the
page about the strategy it belongs to.

Fix this first. It is the only item here that is a functional hole rather than
a thinness.

`JobProgress` is the same shape of omission, one notch less serious: the
rotation renders it on two tabs from the same field, and BTST renders it
nowhere.

---

## GAP 2 — the backend does not SEND what a richer page would need

This is not only a JSX job, and discovering that half way through would be
annoying. `GET /api/swing/status` and `GET /api/btst/status` carry:

**Swing has, BTST does not:**

- `scheduler` — what the clock is doing *this second*, what it will do next,
  what it last did, its recent runs, its missed runs. **BTST's Live tab cannot
  show any of this because it is not on the payload.** The rotation's
  `ActivityStrip` is built entirely from it.
- `balance` — the portfolio's four figures (cash, blocked margin, available,
  equity). `frontend/CLAUDE.md` §3: *"Four figures, never one."* BTST's page
  shows no money at all, on a strategy that deploys about a third of the book
  every day.
- `snapshot` / `snapshotError` — the live computation, and why it could not be
  made.
- `policies` / `policyContradiction` / `settings` — so the Live tab can warn
  that a rule is NOT ENFORCED without a second round trip.
- `closingAuction` — the CAS note. BTST needs this *more* than swing does: its
  scan is at 15:20, inside the auction window, which is the entire reason
  `fno.exclude` exists.
- `description` — the strategy's own one-liner, from the YAML.

Add the equivalents to `BtstService.status()` before touching the page. Follow
the existing rule: **the server composes the sentence, the page renders it**
(§5d — *"the page does not compose its own"*).

---

## GAP 3 — "How it works" has no diagrams, and the convention says it should

`SwingExplainer.jsx` draws **five inline SVG diagrams** — the day timeline, the
filter funnel, the breadth ramp, the volatility-adjusted score, and the
chandelier ratchet — in `theme.palette.*` / `theme.market.*` tokens, and
**where a live snapshot exists it draws them with TODAY's numbers.**

`BtstExplainer.jsx` contains **zero** `<svg>`.

`frontend/CLAUDE.md` §5d states the rule and the reason: *"five static pictures
do not justify a library"*, and *"a diagram of the rule in the abstract teaches
less than the same diagram with this morning's market in it."*

BTST has at least four diagrams worth drawing, and they are not swing's:

1. **The day timeline** — 09:15 open → 14:45 universe joins the feed → 15:15
   CAS closes continuous trading for F&O names → 15:20 scan and buy → overnight
   → 09:16 sell. This is the single most explanatory picture in the module and
   it does not exist. Swing's `DayTimeline` is the pattern to follow.
2. **The filter funnel B2→B8**, drawn with today's real census. `BtstFunnel`
   already renders the counts as bars on the Signals tab; the explainer wants
   the *shape* of the funnel with the thresholds labelled.
3. **CLV** — a candle with the day's range and the top 20% shaded, showing
   where the price has to close. B6 is the least intuitive rule in the strategy
   and a picture settles it instantly.
4. **The exit-timing cliff** — +0.617% at 71.4% held to the next OPEN against
   +0.428% at 49.0% held to the next CLOSE. The explainer states these numbers;
   drawn side by side they make the case the prose only asserts.

**Every number in a diagram comes from `GET /api/btst/explain`.** Hardcoding
`55` or `0.8` in JSX is a second source of truth (root `CLAUDE.md` §7) and will
go on saying 55 long after the YAML changes.

---

## GAP 4 — the Live tab answers "what did it decide", not "what is it doing"

`frontend/CLAUDE.md` §5d: *"The Live tab says what it is doing NOW, not only
what it decided."* The rotation's `ActivityStrip` carries the market state and
an IST clock, what the scheduler is doing this second, what it will do next and
in how long, what it last did, and the stop monitor's line — with the countdown
ticking **locally** off an absolute timestamp so a stalled poll shows up as a
clock running past a run that never happened.

BTST's Live tab has a four-metric row and three cards. It does have local
countdowns to the next scan and exit — keep those — but it has no activity
strip, no "what it last did", no run history, and no money.

It is also missing the rotation's **arming control and its confirmation
dialog** (`ArmingCard`). BTST ships unarmed and cannot be armed from its own
page at all; the rotation can, with the server's warnings in front of it.

---

## GAP 5 — the Health tab is five thin panels against a dozen rich ones

`SwingHealth.jsx` has, among others: *"The data it decides on"*, *"The rules it
is running under"*, *"What it holds, and what it is watching"*, *"The stop
watcher"*, *"Recent scheduled runs"*, *"Recent problems"* — with real figures
under each (bars stored, symbols with bars, clock checks, set to run at, active
stops, nearest stop, stops triggered) and three explicit statements of what the
tab **cannot** know.

`BtstHealth.jsx` has five panels and carries the three notes. The two panels
that are genuinely BTST's own — *"Did the exit run?"* and *"Is the universe
subscribed?"* — are the right ones and are the best part of the page. **Keep
their prominence.** What is missing is the depth underneath them: the scheduled
run list, the per-run outcome, the holdings detail, the money.

---

## GAP 6 — the Configuration tab is thinner than the contract it claims

BTST's Configuration tab implements the draft/Save/dialog shape and the
"only changed fields are in the draft" rule. It does **not** implement:

- **Ordering changes to avoid an illegal intermediate state.** The rotation's
  `orderPolicyChanges` exists because the server refuses one pair; BTST has no
  contradictory pair (`btst_policy.validate_policy_change` is a documented
  no-op), so this may genuinely not be needed — **verify, then say so in a
  comment** rather than leaving the absence ambiguous.
- The rotation's per-row `Row({ title, subtitle, chips, control })` treatment,
  which is what makes each switch readable at a glance.
- Any statement of the CADENCE distinction. Swing's tab says the rebalance
  cadence is P18 and lives in the YAML; BTST's says B1–B16 are not editable but
  does not name the specific things somebody will look for and not find.

---

## What NOT to copy — where BTST is different ON PURPOSE

This is the half of the job that is easy to get wrong. **Do not add these to
BTST to match swing:**

- **Anything about a stop.** B15 is `none` and none is possible: every position
  is opened near the close and sold at the next open, so the only risk window
  is one in which no order can execute at any price. The Health tab's
  `stopWatcher.applicable: false` and its note are correct and deliberate — a
  row reading "not running" would invite somebody to fix it.
- **The breadth ramp**, the **rotation rank**, the **volatility-adjusted
  score**, the **ATR**. None exists here.
- **The V3b off-gate variant** and the **entry-return filter**. The rotation has
  three policy switches; BTST has two, and the absent one is absent because the
  rule does not exist in this specification — not because it is switched off.
- **A rich equity curve / CAGR / drawdown block.** BTST's own performance card
  reports realised overnight gaps, which is the honest measure for an ~18-hour
  hold. Do not import the rotation's CAGR framing.

And **do not weaken** what BTST's page already does better than swing's:

- The **Signals tab** has no swing equivalent and is the most interesting thing
  on screen between 14:30 and 15:20.
- **"Nothing qualified today" reads as normal**, with the funnel always on
  screen. At ~0.54 signals a session this is the ordinary state, and a page
  that looked broken when nothing qualified would be wrong more often than
  right.
- **A late exit is shown as late, in the colour of a problem.**

---

## Rules that must not be broken

- **Undefined is never zero.** A CLV that could not be computed, a withheld
  equity figure, an overnight gap on a position that has not been sold — each
  says so. `null` and `0` are different answers.
- **The page restates no number.** Every threshold and lookback comes from
  `GET /api/btst/explain`, which reads the YAML.
- **The server composes the sentence.** A caveat, a note or a refusal is on the
  payload; the page renders it. Two descriptions of one rule drift.
- **Countdowns tick locally** off an absolute timestamp, so a stalled poll is
  visible as a clock running past an event that never happened.
- **One poll per page.** The page already polls at 10 s; new panels join it
  rather than adding a second. An admin-only read's failure is kept OFF the
  page's `error` state.
- **Health stays admin-only**; the rest of `/btst` is open to a `ROLE_USER`
  because a journal is history.
- **Both themes.** Test light and dark; colours come from `theme.palette.*` /
  `theme.market.*`, never a hex.

---

## Definition of done

- `AlertsPanel` renders on `/btst`, filtered to this strategy, and
  `btst-exit-incomplete` is visible there when it has fired.
- `JobProgress` renders where a long job would show.
- `GET /api/btst/status` carries the scheduler state, the portfolio balance,
  the effective policies and settings, and the closing-auction note; the Live
  tab renders an activity strip from them.
- The arming control and its confirmation dialog are reachable for BTST.
- `BtstExplainer` draws at least the day timeline, the CLV picture and the
  exit-timing comparison as inline SVG in theme tokens, with every number from
  `/explain` and today's figures where a snapshot exists.
- The Health tab keeps its two BTST-specific verdicts at the top and gains the
  run list, the holdings detail and the money.
- `frontend/CLAUDE.md` §5e is updated to describe what the page now does, and
  stays honest about what it does not.
- Full suite green (`1109 passed` at the time of writing), `npm run build`
  passes, and **the rotation is untouched** — it is live and ARMED, and
  `backend/tests/test_btst_does_not_disturb_swing.py` is the file that fails
  with a name saying why.
- Driven in a browser, in BOTH themes, with the strategy OFF and with nothing
  ever traded — every tab must still render. That state is the normal one
  today.

---

## Decisions still open

1. **Does BTST's Live tab need a book/ranking table?** The rotation has
   `BookTable` and `RankingTable` because it holds ten names for weeks. BTST
   holds up to five for eighteen hours, and the holdings card may be enough.
   Recommendation: **no separate ranking table** — the Signals tab is the
   ranking — but do enrich the holdings card with the entry reason.
2. **Should the Signals tab poll faster than 10 s during the scan window?**
   The funnel genuinely moves between 14:30 and 15:20. Recommendation: **leave
   it at 10 s**; a page that reports on load must not be a load source, and
   nothing here changes in under ten seconds that a person can act on.
3. **How much of section 9.4's decay table belongs on the Live tab** rather
   than only in the explainer? It is the most important fact about this
   strategy. Recommendation: a one-line summary on Live linking to the
   explainer, not the whole table twice.

---

## Reference

| Thing | Where |
|---|---|
| The page to match | `frontend/src/pages/SwingMomentumPage.jsx` and `components/Swing*.jsx` |
| The page to improve | `frontend/src/pages/BtstOvernightPage.jsx` and `components/Btst*.jsx` |
| The UI contract | `frontend/CLAUDE.md` §3, §4a, §5d, §5e |
| The payload to extend | `backend/src/btst/services/btst_service.py` |
| The health payload | `backend/src/btst/services/btst_health_service.py` |
| The shared panels | `frontend/src/components/AlertsPanel.jsx`, `JobProgress.jsx` |
| What must not regress | `backend/tests/test_btst_does_not_disturb_swing.py` |
| Login | `trader@abc.com` / `APP_ADMIN_PASSWORD` from `.env` |
