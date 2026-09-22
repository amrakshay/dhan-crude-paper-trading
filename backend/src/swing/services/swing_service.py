"""What the Swing Momentum page reads.

Everything the page shows is already stored -- `swing_sessions.ranking_json`,
`filter_counts_json`, `parameters_json`, the per-symbol `swing_decisions` rows
and the `swing_stops` table. This module assembles them; it decides nothing and
places nothing.

Three things it is careful about, all of them `frontend/CLAUDE.md` section 3
applied to an automated strategy:

* **Undefined is never zero.** A breadth that could not be measured is `None`,
  not 0%. An equity figure `BalanceService` withheld is `None` with the reason
  attached, not the cash balance standing in for it. A position with no mark
  has no distance to its stop.
* **Enabled and ARMED are reported separately**, because they are two switches
  and the difference is whether software may spend money.
* **There is no live track record**, and the payload says so until there is
  one. A page showing a CAGR from four weeks of paper trading next to the
  specification's 19.9% invites exactly the comparison that is not valid.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

import json

from src.core.time_utils import to_ist
from src.logging_config import get_logger
from src.strategies.services import market_clock
from src.strategies.services.strategy_definition import StrategyDefinition
from src.swing.database.db_models.swing_session_model import (
    RUN_NIGHTLY,
    RUN_REBALANCE,
)
from src.swing.database.db_models.swing_stop_model import (
    EXIT_MANUAL,
    EXIT_REGIME,
    EXIT_ROTATION,
    EXIT_TRAIL,
)
from src.swing.database.db_operations.swing_session_repository import (
    SwingDecisionRepository,
    SwingSessionRepository,
)
from src.swing.database.db_operations.swing_stop_repository import SwingStopRepository
from src.swing.services.gate_policy import (
    GatePolicyError,
    describe_policies,
    resolve_gate_policy,
)
from src.swing.services.schedule_settings import describe_settings
from src.swing.services.ranking_service import RankingService
from src.swing.services.rebalance_planner import effective_gate
from src.swing.services.stop_service import StopService
from src.swing.services.swing_parameters import SwingParameters

logger = get_logger("swing.service")

EXIT_LABELS = {
    EXIT_TRAIL: "Trailing stop",
    EXIT_ROTATION: "Rotation",
    EXIT_REGIME: "Regime exit",
    EXIT_MANUAL: "Closed by hand",
}


class SwingServiceError(Exception):
    pass


@dataclass
class SwingService:
    """Read models for the strategy page. Nothing here writes."""

    session: Any
    definition: StrategyDefinition
    parameters: SwingParameters
    book: Any = None

    @classmethod
    def for_strategy(cls, session, strategy_key: str, book=None) -> "SwingService":
        from src.strategies.services.strategy_registry import get_strategy_registry

        definition = get_strategy_registry().get(strategy_key)
        if definition is None:
            raise SwingServiceError(f"Unknown strategy module {strategy_key!r}")
        if not definition.automation.automated:
            raise SwingServiceError(
                f"{definition.label} is a discretionary module: it takes no "
                f"decisions of its own, so it has no decision journal to show."
            )
        return cls(
            session=session,
            definition=definition,
            parameters=SwingParameters.from_definition(definition),
            book=book,
        )

    # --- collaborators ------------------------------------------------------
    @property
    def sessions(self) -> SwingSessionRepository:
        return SwingSessionRepository(self.session)

    @property
    def decisions(self) -> SwingDecisionRepository:
        return SwingDecisionRepository(self.session)

    @property
    def stops(self) -> SwingStopRepository:
        return SwingStopRepository(self.session)

    def _ranking_service(self) -> RankingService:
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )

        return RankingService(
            DailyBarRepository(self.session), self.definition, self.parameters
        )

    def _balances(self):
        from src.portfolios.services.balance_service import BalanceService

        return BalanceService(self.session, book=self.book)

    def _equity_segment(self) -> str:
        for instrument_set in self.definition.instrument_sets:
            if instrument_set.from_universe:
                return instrument_set.exchange_segment
        return self.definition.exchange_segment

    # --- status -------------------------------------------------------------
    async def status(self, portfolio_id: Optional[int] = None) -> Dict[str, Any]:
        """The headline: what the rule says right now, and whether it may act."""
        from src.strategies.services.strategy_registry import get_strategy_registry
        from src.swing.services.scheduler import get_swing_scheduler
        from src.swing.services.stop_monitor import get_swing_stop_monitor

        registry = get_strategy_registry()
        enabled = registry.is_enabled(self.definition.key)

        latest_nightly = await self.sessions.latest(self.definition.key, RUN_NIGHTLY)
        latest_rebalance = await self.sessions.latest(
            self.definition.key, RUN_REBALANCE
        )

        # What is actually being ENFORCED, not what the YAML says. A page that
        # showed the file would teach a gate that is not being obeyed; one that
        # showed only the effective value would hide that a switch was moved.
        # `describe_policies` returns both, per switch.
        policies = describe_policies(self.definition, self.parameters)
        try:
            policy = resolve_gate_policy(self.definition, self.parameters)
        except GatePolicyError:
            # Two switches that contradict each other. The page renders the
            # refusal from `policies['contradiction']`; the snapshot below
            # falls back to the YAML so the rest of it still reads.
            policy = None

        snapshot_payload: Optional[Dict[str, Any]] = None
        snapshot_error: Optional[str] = None
        if enabled:
            try:
                snapshot = await self._ranking_service().session_snapshot()
                gate = effective_gate(snapshot, self.parameters, policy)
                snapshot_payload = snapshot.as_dict(top=15)
                snapshot_payload["effectiveGate"] = {
                    "variant": gate.variant,
                    "gateOn": gate.gate_on,
                    "liquidates": gate.liquidates,
                    "entriesAllowed": gate.entries_allowed,
                    # None, never 0: "could not be measured" and "measured as
                    # none" are different answers.
                    "slots": gate.slots,
                    "momentumFloor": gate.momentum_floor,
                    # WHICH RULES ARE BEING OBEYED. Surfaced beside the gate's
                    # own boolean so the page cannot show a gate that looks on
                    # while nothing is acting on it.
                    "enforceRegime": gate.enforce_regime,
                    "enforceEntryReturn": gate.enforce_entry_return,
                    "relaxed": gate.relaxed,
                }
            except Exception as error:  # noqa: BLE001 - the page must still render
                snapshot_error = str(error)
                logger.warning(
                    "Could not compute the live snapshot for %s: %s",
                    self.definition.key, error,
                )

        balance = None
        if portfolio_id is not None:
            balance = (await self._balances().balance_for(portfolio_id)).as_dict()

        return {
            "strategyKey": self.definition.key,
            "label": self.definition.label,
            "description": self.definition.description,
            "enabled": enabled,
            # Two switches, reported as two.
            "automated": self.definition.automation.automated,
            "armed": registry.is_armed(self.definition.key),
            "armedByDefault": self.definition.automation.armed_by_default,
            "marketOpen": market_clock.is_market_open(self.definition),
            "marketOpensAt": self.definition.market_hours.open.strftime("%H:%M"),
            "marketClosesAt": self.definition.market_hours.close.strftime("%H:%M"),
            "closingAuction": self._closing_auction_payload(),
            "snapshot": snapshot_payload,
            "snapshotError": snapshot_error,
            "latestNightly": self._session_payload(latest_nightly),
            "latestRebalance": self._session_payload(latest_rebalance),
            "scheduler": get_swing_scheduler().status(),
            "stopMonitor": get_swing_stop_monitor().status(),
            "policies": policies["policies"],
            "policyContradiction": policies["contradiction"],
            # The Configuration tab reads both from here rather than from the
            # Strategies payload, so the page it is on is the page it comes
            # from.
            "settings": describe_settings(self.definition, self.parameters)["settings"],
            "balance": balance,
            # Said on every payload, deliberately. The specification's 19.9% is
            # a survivorship-biased, in-sample backtest of a rule that has never
            # traded a rupee, and a page that shows this book's numbers beside
            # it without saying so invites exactly the wrong comparison.
            "trackRecord": {
                "live": False,
                "note": (
                    "No live track record. Every figure on this page comes from "
                    "paper orders in this database. The specification's 19.9% "
                    "CAGR is a backtest on today's Nifty 500 looked at backwards "
                    "-- survivorship-biased and chosen in-sample -- and its own "
                    "author expects 12-18% live."
                ),
            },
        }

    def _closing_auction_payload(self) -> Optional[Dict[str, Any]]:
        auction = self.definition.market_hours.closing_auction
        if auction is None:
            return None
        return {
            "appliesTo": auction.applies_to,
            "continuousClose": auction.continuous_close.strftime("%H:%M"),
            "auctionClose": auction.auction_close.strftime("%H:%M"),
            "note": (
                "For F&O-eligible names continuous cash trading ends at "
                f"{auction.continuous_close.strftime('%H:%M')}. A trailing stop "
                f"that fires after that is recorded and its exit waits for the "
                f"next session's open -- this simulator has no model of a call "
                f"auction."
            ),
        }

    # --- the rule, as configured --------------------------------------------
    def explain(self) -> Dict[str, Any]:
        """Every number that defines this strategy, read from its own YAML.

        The "How it works" page renders this rather than restating the values
        in JavaScript. Root `CLAUDE.md` section 7 is why: no number that
        affects a trade is written twice. A page that hardcoded "3.5 x ATR"
        would be a second source of truth, and it would go on saying 3.5 for
        as long as it took someone to notice the YAML had changed.

        Each entry carries the specification's own parameter code, so the page
        and `SWING_MOMENTUM_HANDOFF.md` can be read side by side.
        """
        parameters = self.parameters
        regime = parameters.regime
        schedule = parameters.schedule
        off_gate = parameters.off_gate
        universe = self.definition.universe
        policies = describe_policies(self.definition, parameters)
        effective = {row["key"]: row["enforced"] for row in policies["policies"]}

        from src.swing.services.schedule_settings import resolve_schedule

        effective_schedule = resolve_schedule(self.definition, parameters)

        return {
            "strategyKey": self.definition.key,
            "label": self.definition.label,
            "description": self.definition.description,
            "specification": (
                "~/Workarea/local/pullback/backend/intrday_test_strategy/"
                "research2/SWING_MOMENTUM_HANDOFF.md"
            ),
            "universe": {
                "name": universe.name if universe else None,
                "size": len(universe) if universe else None,
                "file": f"conf/universes/{universe.name}.csv" if universe else None,
                "segment": self._equity_segment(),
            },
            "regimeIndex": self._reference_payload(),
            "chargesRateCard": self.definition.charges_rate_card,
            "marketHours": {
                "open": self.definition.market_hours.open.strftime("%H:%M"),
                "close": self.definition.market_hours.close.strftime("%H:%M"),
                "timezone": self.definition.market_hours.timezone,
                "closingAuction": self._closing_auction_payload(),
            },
            "schedule": {
                # The YAML's own times, and the ones in force. The cadence has
                # no second value: it is P18 and is not editable at runtime.
                "nightlyAtIst": schedule.nightly_at,
                "rebalanceAtIst": schedule.rebalance_at,
                "effectiveNightlyAtIst": effective_schedule.nightly_at,
                "effectiveRebalanceAtIst": effective_schedule.rebalance_at,
                "cadence": schedule.rebalance_cadence,
            },
            "automation": {
                "automated": self.definition.automation.automated,
                "armedByDefault": self.definition.automation.armed_by_default,
                "maxLotsPerOrder": self.definition.automation.max_lots_per_order,
            },
            "offGate": {
                # The YAML's own value, and what is actually in force. Both,
                # always: the three enforcement switches are runtime state now,
                # so a page reporting only the file would teach a rule that is
                # not being obeyed. `policies` below carries the same pairing
                # for all three.
                "enabled": off_gate.enabled,
                "effectiveEnabled": effective.get("off_gate.enabled"),
                "slots": off_gate.slots,
                "momentumFloor": off_gate.momentum_floor,
                "requireEntryReturn": off_gate.require_entry_return,
            },
            # WHETHER EACH RULE IS ENFORCED -- not what it is. Every number
            # above is read from the YAML and is editable from nowhere; these
            # three switches say whether the application obeys P8/P17, P9 and
            # the V3b variant, and they are runtime state an admin flips on the
            # Strategies & Features page. See src/swing/services/gate_policy.py
            # and root CLAUDE.md section 3a.
            "policies": policies["policies"],
            "policyContradiction": policies["contradiction"],
            "parameters": [
                _param("P1", "Universe", universe.name if universe else "—",
                       "Nifty 500 members, refreshed by hand each quarter."),
                _param("P2", "Liquidity floor",
                       f"ADV20 >= Rs {parameters.liquidity_floor_rupees:,.0f}",
                       f"Mean of close x volume over "
                       f"{parameters.liquidity_window_sessions} sessions. RUPEE "
                       f"turnover, not share volume."),
                _param("P3", "Price floor", f"close >= Rs {parameters.price_floor:,.0f}",
                       "Keeps penny stocks out of a book sized in tenths."),
                _param("P4", "Trend qualifier",
                       f"close > SMA{parameters.trend_sma_sessions}",
                       "The stock's OWN moving average, not the index's."),
                _param("P5", "Momentum",
                       f"close[t-{parameters.momentum_skip_sessions}] / "
                       f"close[t-{parameters.momentum_lookback_sessions}] - 1",
                       f"Six months, with the most recent "
                       f"{parameters.momentum_skip_sessions} sessions skipped so a "
                       f"one-week spike cannot buy its way in."),
                _param("P6", "Momentum floor",
                       f"> {parameters.momentum_floor:.0%}",
                       "An absolute filter: a name must be going up, not merely "
                       "going up faster than the rest."),
                _param("P7", "Rank score",
                       f"momentum / (ATR{parameters.atr_sessions} / close)",
                       "THE differentiating idea. Dividing by ATR% penalises "
                       "momentum bought with volatility. Removing it measures "
                       "29% CAGR at an unacceptable -31% drawdown."),
                _param("P8", "Regime gate",
                       f"{self._index_symbol()} close > "
                       f"SMA{regime.sma_sessions}",
                       "The hard kill switch. Below it, the book goes to 100% cash."
                       + ("" if effective.get("regime.enforce", True) else
                          " CURRENTLY NOT ENFORCED: it is still computed and "
                          "still recorded on every session and every trade, and "
                          "it stops nothing.")),
                _param("P9", "Entry filter",
                       f"{self._index_symbol()} {regime.entry_return_sessions}-session "
                       f"return > {regime.entry_return_minimum:.0%}",
                       "Blocks NEW entries only. It never forces an exit."
                       + ("" if effective.get("regime.enforce_entry_return", True)
                          else " CURRENTLY NOT ENFORCED.")),
                _param("P10", "Breadth",
                       f"fraction of the liquid universe above its own "
                       f"SMA{parameters.trend_sma_sessions}",
                       "Measured over the names that passed P2 and P3 AND have a "
                       "defined SMA200 -- a name too young for one is in neither "
                       "the numerator nor the denominator."),
                _param("P11", "Slots allowed",
                       f"round({parameters.max_positions} x clamp((breadth - "
                       f"{parameters.breadth_lower}) / {parameters.breadth_span}, 0, 1))",
                       f"{parameters.breadth_lower:.0%} of the liquid universe buys "
                       f"nothing; "
                       f"{parameters.breadth_lower + parameters.breadth_span:.0%} buys "
                       f"the full book. The SPAN is configured rather than derived, "
                       f"because 0.65 - 0.35 is not 0.30 in IEEE-754 and the "
                       f"difference is a whole slot."),
                _param("P12", "Max positions", str(parameters.max_positions),
                       "The loss cap: one name down 20% is 2% of equity."),
                _param("P13", "Position size",
                       f"total equity / {parameters.position_size_divisor}",
                       f"Whole shares, floored. Deliberately separate from P12, so "
                       f"a narrow market holds cash rather than concentrating: "
                       f"sizing stays at "
                       f"{1 / parameters.position_size_divisor:.0%} of equity even "
                       f"when breadth allows only four slots."),
                _param("P14", "Initial stop",
                       f"entry - {parameters.trail_atr_multiple} x "
                       f"ATR{parameters.atr_sessions}",
                       "Set on the session of entry."),
                _param("P15", "Trailing stop",
                       f"max(stop, highest close since entry - "
                       f"{parameters.trail_atr_multiple} x ATR{parameters.atr_sessions})",
                       "Ratchets UP only. Never down -- ATR widens after a violent "
                       "day, and the naive formula would lower the stop on exactly "
                       "the session the position became more dangerous."),
                _param("P16", "Rotation exit",
                       f"rank > {parameters.rotation_exit_rank}",
                       "Sold at the next open. A holding that decays off the "
                       "leadership board leaves, whether or not it is losing money."),
                _param("P17", "Regime exit",
                       f"{self._index_symbol()} below its "
                       f"SMA{regime.sma_sessions}",
                       "Sell EVERYTHING at the next open. The gate does not "
                       "negotiate with a good position."
                       + ("" if effective.get("regime.enforce", True) else
                          " CURRENTLY NOT ENFORCED: nothing is liquidated when "
                          "the gate flips off. A position opened under this "
                          "policy keeps it -- re-enforcing the gate stops new "
                          "entries and does NOT sell the book.")),
                _param("P18", "Rebalance",
                       f"{schedule.rebalance_cadence}",
                       "Daily was chosen deliberately: 23.4% CAGR at -22.2% "
                       "drawdown against weekly's 19.9% and -18.3%. Weekly has the "
                       "better MAR. Changing this one value switches it."),
                _param("P19", "Execution", "market orders at the next open",
                       "Through this application's own pessimistic fill simulator, "
                       "not the backtest's 0.05%-per-side model."),
                _param("—", "Warm-up",
                       f"{parameters.minimum_sessions} sessions minimum",
                       "A symbol with less history is excluded from the universe "
                       "entirely, exactly as the backtest's loader excludes it."),
            ],
            # Said here as well as on the live tab. A page that teaches the rule
            # must teach its limits in the same breath.
            "caveats": [
                "This is a BACKTEST, not a track record. No capital has traded "
                "this rule anywhere.",
                "The universe is today's Nifty 500 looked at backwards, so the "
                "backtest is survivorship-biased. The specification's own author "
                "expects 12-18% live against the 19.9% headline.",
                "Every parameter above was chosen in-sample on the full period, "
                "with no walk-forward and no holdout. They are not knobs to turn "
                "until the numbers improve.",
                "Returns are concentrated: the top 10 of 672 backtested trades "
                "were 55% of the summed return. Miss them and roughly half the "
                "result disappears.",
                "Long underwater stretches are inherent -- about two and a half "
                "years within -16% between 2018 and mid-2020. Abandoning the "
                "system mid-drawdown is the main way it loses money.",
                "Returns are pre-tax. STCG applies to holds under twelve months.",
            ],
        }

    def _index_symbol(self) -> str:
        reference = self.definition.reference_instrument(
            self.parameters.regime.index_role
        )
        return reference.label if reference else "the index"

    def _reference_payload(self) -> Optional[Dict[str, Any]]:
        reference = self.definition.reference_instrument(
            self.parameters.regime.index_role
        )
        if reference is None:
            return None
        return {
            "symbol": reference.symbol,
            "label": reference.label,
            "exchangeSegment": reference.exchange_segment,
            # Read, never traded, and deliberately not a row in `instruments`:
            # Dhan's security ids are unique per SEGMENT, and id 13 is NIFTY in
            # IDX_I and ABB in NSE_EQ -- and ABB is in this universe.
            "traded": False,
        }

    # --- the open book ------------------------------------------------------
    async def book_for(self, portfolio_id: int) -> Dict[str, Any]:
        """Every open position, with its stop and the distance to it."""
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        positions = await PositionRepository(self.session).list_all(
            include_closed=False,
            strategy_key=self.definition.key,
            portfolio_id=portfolio_id,
        )
        balances = self._balances()
        latest = await self.sessions.latest(self.definition.key, RUN_NIGHTLY)
        ranks = self._ranks_from(latest)

        rows: List[Dict[str, Any]] = []
        unmarked = 0
        for position in positions:
            net = int(position.net_quantity or 0)
            if net == 0:
                continue
            mark = balances.mark_for(position.security_id)
            if mark is None:
                unmarked += 1
            stop = await self.stops.get_active(portfolio_id, position.security_id)
            entry = Decimal(str(position.average_price or 0))
            rows.append(
                {
                    "symbol": position.trading_symbol,
                    "securityId": position.security_id,
                    "quantity": net,
                    "averagePrice": str(entry),
                    "mark": str(mark) if mark is not None else None,
                    # None, not 0.00, when there is no mark. A P&L of zero
                    # beside a position that is down 8% is the worst possible
                    # way to say "no price".
                    "unrealised": (
                        str(((mark - entry) * net).quantize(Decimal("0.01")))
                        if mark is not None
                        else None
                    ),
                    "rank": ranks.get(position.trading_symbol),
                    "rotationExitRank": self.parameters.rotation_exit_rank,
                    "stop": (
                        StopService.describe(stop, mark) if stop is not None else None
                    ),
                    # A position with no stop is a real and visible state, not
                    # an absent field: the ATR was unavailable at entry and the
                    # next nightly ratchet will set one. Since every entry now
                    # writes a row, "has a row" and "has a LEVEL" came apart --
                    # this is the second one, which is what protects anything.
                    "hasStop": stop is not None and stop.stop_price is not None,
                    # The policy this position was opened under, which is what
                    # decides whether a regime exit reaches it.
                    "entryGateOn": stop.entry_gate_on if stop is not None else None,
                    "entryRegimeEnforced": (
                        stop.entry_regime_enforced if stop is not None else None
                    ),
                }
            )

        return {
            "portfolioId": portfolio_id,
            "positions": rows,
            "openPositions": len(rows),
            "maxPositions": self.parameters.max_positions,
            "unmarkedPositions": unmarked,
            "rankedAsOf": (
                latest.session_date.isoformat() if latest is not None else None
            ),
        }

    @staticmethod
    def _ranks_from(session_row) -> Dict[str, int]:
        """Ranks out of a stored session, including the HELD names' own.

        A holding that has fallen to rank 40 is exactly the row a rotation exit
        has to be explained by, which is why the journal stores it separately
        from the top of the list.
        """
        if session_row is None or not session_row.ranking_json:
            return {}
        try:
            payload = json.loads(session_row.ranking_json)
        except (TypeError, ValueError):
            return {}
        ranks: Dict[str, int] = {}
        for entry in payload.get("top", []):
            if entry.get("symbol") and entry.get("rank"):
                ranks[entry["symbol"]] = int(entry["rank"])
        for entry in payload.get("held", []):
            if entry.get("symbol") and entry.get("rank"):
                ranks[entry["symbol"]] = int(entry["rank"])
        return ranks

    # --- history ------------------------------------------------------------
    async def history(
        self, limit: int = 30, run_kind: Optional[str] = None
    ) -> Dict[str, Any]:
        rows = await self.sessions.list_recent(
            self.definition.key, limit=limit, run_kind=run_kind
        )
        return {
            "strategyKey": self.definition.key,
            "sessions": [self._session_payload(row) for row in rows],
            "count": len(rows),
        }

    async def session_detail(self, session_id: int) -> Dict[str, Any]:
        row = await self.sessions.get_by_id(session_id)
        if row is None or row.strategy_key != self.definition.key:
            raise SwingServiceError(f"No session {session_id} for this strategy")
        decisions = await self.decisions.for_session(session_id)
        payload = self._session_payload(row) or {}
        payload["decisions"] = [
            {
                "symbol": decision.symbol,
                "action": decision.action,
                "rank": decision.rank,
                "score": (
                    str(decision.score) if decision.score is not None else None
                ),
                "quantity": decision.quantity,
                "referencePrice": (
                    str(decision.reference_price)
                    if decision.reference_price is not None
                    else None
                ),
                "orderId": decision.order_id,
                "reason": decision.reason,
            }
            for decision in decisions
        ]
        payload["ranking"] = _load_json(row.ranking_json)
        payload["filterCounts"] = _load_json(row.filter_counts_json)
        payload["parameters"] = _load_json(row.parameters_json)
        return payload

    def _session_payload(self, row) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        breadth = None
        if row.breadth_liquid:
            breadth = round(int(row.breadth_above or 0) / int(row.breadth_liquid), 4)
        return {
            "id": row.id,
            "sessionDate": row.session_date.isoformat(),
            "runKind": row.run_kind,
            "status": row.status,
            "portfolioId": row.portfolio_id,
            # WHEN THE RUN HAPPENED, which is not `sessionDate`. The session is
            # the newest stored bar date -- the data the decision was computed
            # FROM -- and the two are routinely different days: Dhan publishes
            # a daily bar after the nightly's 18:15 slot, so a run on Monday
            # evening decides Friday's session and the journal correctly says
            # so. Without this on the row there is no way to tell a run that
            # happened tonight from one that happened last week.
            #
            # `to_ist`, and the key says Ist, for the reason backend/CLAUDE.md
            # section 10f-bis gives: these are stored naive UTC, and a naive
            # ISO string is parsed by the browser as LOCAL, so the column would
            # read five and a half hours early beside an IST clock showing the
            # right time. That shipped once already on the health tab.
            "startedAtIst": to_ist(row.started_at).isoformat() if row.started_at else None,
            "completedAtIst": (
                to_ist(row.completed_at).isoformat() if row.completed_at else None
            ),
            "indexSymbol": row.index_symbol,
            "indexClose": _money(row.index_close),
            "indexSma": _money(row.index_sma),
            "gateOn": row.gate_on,
            "indexReturnOverWindow": _money(row.index_return_over_window),
            "entriesAllowed": row.entries_allowed,
            "breadthAbove": row.breadth_above,
            "breadthLiquid": row.breadth_liquid,
            # None when the denominator is zero: breadth that could not be
            # measured is not a breadth of 0%.
            "breadth": breadth,
            "slots": row.slots,
            "universeSize": row.universe_size,
            "symbolsWithBars": row.symbols_with_bars,
            "candidateCount": row.candidate_count,
            "message": row.message,
        }

    # --- performance ---------------------------------------------------------
    async def performance(self, portfolio_id: Optional[int] = None) -> Dict[str, Any]:
        """The specification's own statistics, on this book's own history."""
        from src.orders.database.db_operations.order_repository import OrderRepository
        from src.reports.services.metrics_service import MetricsService
        from src.reports.services.pnl_service import PnlService

        metrics_service = MetricsService(self.session)
        trades = await metrics_service.closed_trades(
            strategy_key=self.definition.key, portfolio_id=portfolio_id
        )

        pnl = PnlService(OrderRepository(self.session), book=self.book)
        report = await pnl.build_report(
            strategy_key=self.definition.key, portfolio_id=portfolio_id
        )
        cash_flow_days = await self._cash_flow_days(portfolio_id)

        metrics = metrics_service.compute(
            trades, report.equity_curve, cash_flow_days
        )
        payload = metrics.as_dict()
        payload["exitMix"] = await self.exit_mix(portfolio_id)
        payload["realisedGross"] = str(report.realised_gross)
        payload["totalCharges"] = str(report.total_charges)
        payload["realisedNet"] = str(report.realised_net)
        payload["equityCurve"] = report.equity_curve
        payload["averageHoldSessions"] = await self._average_hold_sessions(trades)
        payload["byRegimeAtEntry"] = await self._split_by_regime(
            metrics_service, trades, portfolio_id
        )
        payload["trackRecord"] = {
            "live": False,
            "note": (
                "Paper orders only. These are this book's own numbers, not the "
                "backtest's -- the specification expects them to differ, and "
                "measuring that difference is the point."
            ),
        }
        return payload

    async def _split_by_regime(
        self, metrics_service, trades, portfolio_id: Optional[int]
    ) -> Dict[str, Any]:
        """The same trade statistics, split by the regime at ENTRY.

        This is the entire payoff of recording the policy per trade. Trading
        while the regime gate is off is a deliberate divergence from the
        specification -- section 11 tested thirteen ways of doing it and not
        one beat holding cash -- and the only thing that makes the divergence
        reversible is being able to take those trades back out of the numbers
        afterwards. Without this split the columns are data nobody looks at.

        Attribution is through `swing_stops`, which carries one row per
        position with the gate state at entry on it. A trade whose row cannot
        be found is reported as `unattributed` rather than being quietly
        dropped into one of the buckets -- and a bucket with NO trades reports
        null, not zero: "no trades were taken with the gate off" and "the
        trades taken with the gate off averaged nothing" are different
        statements.

        Curve metrics (CAGR, drawdown, MAR) are deliberately NOT computed per
        bucket. An equity curve is a property of the whole book over time and
        cannot be sliced by a subset of its trades without inventing a
        counterfactual book that never existed.
        """
        rows = await self.stops.history(self.definition.key, portfolio_id, limit=5000)

        # symbol -> stop rows, oldest first. The rotation holds a name once at
        # a time, so matching the nth closed trade of a symbol to the nth stop
        # row of that symbol is unambiguous while that stays true.
        by_symbol: Dict[str, List[Any]] = {}
        for row in sorted(rows, key=lambda one: (one.entry_session, one.id)):
            by_symbol.setdefault(row.symbol, []).append(row)

        buckets: Dict[str, List[Any]] = {"gateOn": [], "gateOff": []}
        unattributed = 0
        cursors: Dict[str, int] = {}
        for trade in sorted(trades, key=lambda one: one.opened_at):
            candidates = by_symbol.get(trade.symbol, [])
            index = cursors.get(trade.symbol, 0)
            if index >= len(candidates):
                unattributed += 1
                continue
            row = candidates[index]
            cursors[trade.symbol] = index + 1
            if row.entry_gate_on is None:
                # Entered before the column existed. Not guessed at.
                unattributed += 1
                continue
            buckets["gateOn" if row.entry_gate_on else "gateOff"].append(trade)

        def summarise(bucket: List[Any]) -> Optional[Dict[str, Any]]:
            if not bucket:
                return None
            summary = metrics_service.compute(bucket).as_dict()
            # The curve half is meaningless for a subset; strip it rather than
            # publishing a CAGR computed from no curve at all.
            for key in (
                "startingEquity", "endingEquity", "peakEquity", "daysCovered",
                "cagr", "maxDrawdown", "maxDrawdownAmount", "maxDrawdownFrom",
                "maxDrawdownTo", "mar", "curveNote", "cashFlowsAfterFirstTrade",
            ):
                summary.pop(key, None)
            return summary

        return {
            "gateOn": summarise(buckets["gateOn"]),
            "gateOff": summarise(buckets["gateOff"]),
            "unattributed": unattributed,
            "note": (
                "Split by what the regime gate was doing when each position was "
                "OPENED, from that position's own record. Trades opened with "
                "the gate off were taken because enforcement was switched off "
                "deliberately -- the specification's own research found no "
                "variant of trading through the gate that beat holding cash, "
                "so this is the comparison that matters. CAGR, drawdown and MAR "
                "are not split: an equity curve is a property of the whole book "
                "and slicing it by a subset of trades would describe a book "
                "that never existed."
            ),
        }

    async def exit_mix(self, portfolio_id: Optional[int] = None) -> Dict[str, Any]:
        """Trail stops against rotations, counted from `swing_stops`.

        Counted from the stop rows rather than by reading English out of a
        reason string: every closed stop carries `exit_kind`, set at the moment
        the position left. The specification's own mix is 348 trail stops
        against 324 rotations over 672 trades, and it is why the stop must not
        be tightened -- the trail exits are only 34% profitable and still
        average +2.45%, because winners leave through the ratchet too.
        """
        rows = await self.stops.history(self.definition.key, portfolio_id, limit=5000)
        counts: Dict[str, int] = {}
        for row in rows:
            if row.exit_kind is None:
                continue
            counts[row.exit_kind] = counts.get(row.exit_kind, 0) + 1
        total = sum(counts.values())
        return {
            "total": total,
            "byKind": [
                {
                    "kind": kind,
                    "label": EXIT_LABELS.get(kind, kind),
                    "count": count,
                    "share": round(count / total, 4) if total else None,
                }
                for kind, count in sorted(counts.items())
            ],
            "openStops": sum(1 for row in rows if row.exit_kind is None),
        }

    async def _average_hold_sessions(self, trades) -> Optional[float]:
        """Hold measured in SESSIONS, which is what the specification reports.

        Calendar days and sessions differ by roughly 40%, and the backtest's
        20.1 is sessions. The calendar is the regime index's own bar dates, the
        same one everything else here uses.
        """
        if not trades:
            return None
        reference = self.definition.reference_instrument(
            self.parameters.regime.index_role
        )
        if reference is None:
            return None

        from src.core.time_utils import to_ist
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )

        dates = await DailyBarRepository(self.session).trading_dates(
            reference.symbol, reference.exchange_segment
        )
        if not dates:
            return None
        index = {value: position for position, value in enumerate(dates)}

        spans: List[int] = []
        for trade in trades:
            opened = _nearest(index, dates, to_ist(trade.opened_at).date())
            closed = _nearest(index, dates, to_ist(trade.closed_at).date())
            if opened is None or closed is None:
                continue
            spans.append(max(closed - opened, 0))
        if not spans:
            return None
        return round(sum(spans) / len(spans), 2)

    async def _cash_flow_days(self, portfolio_id: Optional[int]) -> List[date]:
        if portfolio_id is None:
            return []
        from sqlalchemy import select

        from src.core.time_utils import to_ist
        from src.portfolios.database.db_models.portfolio_model import (
            ENTRY_DEPOSIT,
            ENTRY_WITHDRAWAL,
            CashLedgerEntry,
        )

        result = await self.session.execute(
            select(CashLedgerEntry.entry_at).where(
                CashLedgerEntry.portfolio_id == int(portfolio_id),
                CashLedgerEntry.entry_type.in_([ENTRY_DEPOSIT, ENTRY_WITHDRAWAL]),
            )
        )
        return sorted({to_ist(value).date() for (value,) in result.all()})


def _nearest(index: Dict[date, int], dates: List[date], value: date) -> Optional[int]:
    """Where a calendar date sits in the session calendar.

    A trade can open or close on a date with no bar (an order placed before the
    first session of the series, say), so this walks back to the last session
    on or before it rather than failing.
    """
    if value in index:
        return index[value]
    position = None
    for offset, session_date in enumerate(dates):
        if session_date <= value:
            position = offset
        else:
            break
    return position


def _param(code: str, name: str, value: str, note: str) -> Dict[str, str]:
    """One row of the parameter table, carrying the specification's own code."""
    return {"code": code, "name": name, "value": value, "note": note}


def _load_json(value) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _money(value) -> Optional[str]:
    return str(value) if value is not None else None
