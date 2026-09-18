"""Performance metrics, from the book's OWN history.

CAGR, maximum drawdown, MAR, win rate, profit factor, average hold and return
concentration -- the figures the strategy specification reports, computed here
from what actually happened in this database rather than copied from the
backtest. If the two diverge, that divergence is the measurement, and the
specification itself expects it: 12-18% live against a 19.9% survivorship-
biased, in-sample headline.

**A trade is a closed POSITION row, not a fill.** `positions` already opens a
new row each time a contract is re-entered, so one row is one round trip with
its own entry time, exit time, gross P&L and charges. Counting fills instead
would turn a position that exited in three partial fills into three trades and
make the win rate, the average hold and the concentration all wrong.

**Cash flows are not returns.** A deposit raises equity without anyone trading
and a withdrawal lowers it; a CAGR computed across one is not a rate of return
and a drawdown that is really a withdrawal is not a drawdown. So when money
moved after the first trade, CAGR, MAR and maximum drawdown are WITHHELD with
the reason attached, rather than reported as numbers that look like skill.
That is the same rule `BalanceService` applies to an unmarked position.

Nothing here re-derives realised P&L: `pnl_service` owns that, by replaying
fills through the live position book's own rules, and the two agree by
construction (backend/CLAUDE.md section 6).
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from src.core.time_utils import to_ist
from src.logging_config import get_logger

logger = get_logger("reports.metrics")

MONEY = Decimal("0.01")
ZERO = Decimal("0")
DAYS_PER_YEAR = Decimal("365.25")

# How many of the best trades the concentration figure is measured over. Ten,
# because that is what the specification reports (top 10 of 672 trades = 55% of
# the summed return) and it calls that the single most important statistic on
# its page.
CONCENTRATION_TOP_N = 10


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Trade:
    """One closed round trip, as the metrics see it."""

    symbol: str
    opened_at: datetime
    closed_at: datetime
    quantity: int
    gross_pnl: Decimal
    charges: Decimal
    order_ids: tuple = ()

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.charges

    @property
    def hold_days(self) -> float:
        return max(
            (self.closed_at - self.opened_at).total_seconds() / 86400.0, 0.0
        )


@dataclass
class PerformanceMetrics:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    scratches: int = 0
    win_rate: Optional[float] = None
    gross_profit: Decimal = ZERO
    gross_loss: Decimal = ZERO
    profit_factor: Optional[float] = None
    average_win: Optional[Decimal] = None
    average_loss: Optional[Decimal] = None
    average_trade: Optional[Decimal] = None
    median_trade: Optional[Decimal] = None
    average_hold_days: Optional[float] = None
    median_hold_days: Optional[float] = None
    best_trade: Optional[Decimal] = None
    worst_trade: Optional[Decimal] = None

    # Concentration: what share of the summed trade return the best handful
    # produced. Undefined when the sum is not positive -- a "share" of a loss
    # is not a number anyone can read.
    concentration_top_n: int = CONCENTRATION_TOP_N
    concentration_share: Optional[float] = None
    concentration_note: Optional[str] = None

    # Curve metrics. Withheld together, because they are wrong together.
    starting_equity: Optional[Decimal] = None
    ending_equity: Optional[Decimal] = None
    peak_equity: Optional[Decimal] = None
    days_covered: Optional[int] = None
    cagr: Optional[float] = None
    max_drawdown: Optional[float] = None
    max_drawdown_amount: Optional[Decimal] = None
    max_drawdown_from: Optional[str] = None
    max_drawdown_to: Optional[str] = None
    mar: Optional[float] = None
    curve_note: Optional[str] = None
    cash_flows_after_first_trade: int = 0

    extras: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "scratches": self.scratches,
            "winRate": self.win_rate,
            "grossProfit": str(self.gross_profit),
            "grossLoss": str(self.gross_loss),
            "profitFactor": self.profit_factor,
            "averageWin": _str(self.average_win),
            "averageLoss": _str(self.average_loss),
            "averageTrade": _str(self.average_trade),
            "medianTrade": _str(self.median_trade),
            "averageHoldDays": self.average_hold_days,
            "medianHoldDays": self.median_hold_days,
            "bestTrade": _str(self.best_trade),
            "worstTrade": _str(self.worst_trade),
            "concentrationTopN": self.concentration_top_n,
            "concentrationShare": self.concentration_share,
            "concentrationNote": self.concentration_note,
            "startingEquity": _str(self.starting_equity),
            "endingEquity": _str(self.ending_equity),
            "peakEquity": _str(self.peak_equity),
            "daysCovered": self.days_covered,
            "cagr": self.cagr,
            "maxDrawdown": self.max_drawdown,
            "maxDrawdownAmount": _str(self.max_drawdown_amount),
            "maxDrawdownFrom": self.max_drawdown_from,
            "maxDrawdownTo": self.max_drawdown_to,
            "mar": self.mar,
            "curveNote": self.curve_note,
            "cashFlowsAfterFirstTrade": self.cash_flows_after_first_trade,
            **self.extras,
        }


def _str(value: Optional[Decimal]) -> Optional[str]:
    return str(value) if value is not None else None


class MetricsService:
    """Trade statistics and curve statistics, computed once."""

    def __init__(self, session):
        self.session = session

    # --- trades ------------------------------------------------------------
    async def closed_trades(
        self,
        strategy_key: Optional[str] = None,
        portfolio_id: Optional[int] = None,
    ) -> List[Trade]:
        """Every closed round trip, oldest first.

        Read from `positions` rather than replayed from fills because a
        position row IS a round trip -- it carries the entry time, the exit
        time and the charges attributed to it, none of which a realisation
        event has.
        """
        from sqlalchemy import select

        from src.positions.database.db_models.position_model import Position

        query = select(Position).where(Position.is_open.is_(False))
        if strategy_key:
            query = query.where(Position.strategy_key == strategy_key)
        if portfolio_id is not None:
            query = query.where(Position.portfolio_id == int(portfolio_id))
        result = await self.session.execute(query.order_by(Position.opened_at.asc()))

        trades: List[Trade] = []
        for position in result.scalars().all():
            if position.closed_at is None:
                continue
            trades.append(
                Trade(
                    symbol=position.trading_symbol,
                    opened_at=position.opened_at,
                    closed_at=position.closed_at,
                    quantity=int(position.sell_quantity or 0),
                    gross_pnl=Decimal(str(position.realized_pnl or 0)),
                    charges=Decimal(str(position.total_charges or 0)),
                )
            )
        return trades

    # --- the metrics -------------------------------------------------------
    def compute(
        self,
        trades: List[Trade],
        equity_curve: Optional[List[Dict[str, Any]]] = None,
        cash_flow_days: Optional[List[date]] = None,
    ) -> PerformanceMetrics:
        metrics = PerformanceMetrics(trades=len(trades))
        if trades:
            self._trade_metrics(metrics, trades)
        self._curve_metrics(
            metrics, equity_curve or [], cash_flow_days or [], trades
        )
        return metrics

    @staticmethod
    def _trade_metrics(metrics: PerformanceMetrics, trades: List[Trade]) -> None:
        nets = sorted(trade.net_pnl for trade in trades)
        wins = [value for value in nets if value > 0]
        losses = [value for value in nets if value < 0]

        metrics.wins = len(wins)
        metrics.losses = len(losses)
        # A trade that came out exactly flat is neither, and counting it as a
        # loss would understate the win rate on a strategy whose median trade
        # is +0.11%.
        metrics.scratches = len(nets) - len(wins) - len(losses)
        metrics.win_rate = round(len(wins) / len(nets), 4) if nets else None

        metrics.gross_profit = _q(sum(wins, ZERO))
        metrics.gross_loss = _q(abs(sum(losses, ZERO)))
        metrics.profit_factor = (
            round(float(metrics.gross_profit / metrics.gross_loss), 4)
            if metrics.gross_loss > 0
            else None
        )
        metrics.average_win = _q(sum(wins, ZERO) / len(wins)) if wins else None
        metrics.average_loss = (
            _q(abs(sum(losses, ZERO)) / len(losses)) if losses else None
        )
        metrics.average_trade = _q(sum(nets, ZERO) / len(nets))
        metrics.median_trade = _q(_median(nets))
        metrics.best_trade = _q(nets[-1])
        metrics.worst_trade = _q(nets[0])

        holds = sorted(trade.hold_days for trade in trades)
        metrics.average_hold_days = round(sum(holds) / len(holds), 2)
        metrics.median_hold_days = round(_median(holds), 2)

        # Return concentration. The specification calls this the single most
        # important statistic on its page: the top 10 of 672 trades were 55% of
        # the summed return, which means wide error bars on every other figure
        # here.
        total = sum(nets, ZERO)
        if total > 0:
            top = sorted(nets, reverse=True)[:CONCENTRATION_TOP_N]
            metrics.concentration_share = round(float(sum(top, ZERO) / total), 4)
            metrics.concentration_note = (
                f"The best {min(CONCENTRATION_TOP_N, len(nets))} of {len(nets)} "
                f"trade(s) produced "
                f"{metrics.concentration_share * 100:.0f}% of the summed trade "
                f"return. The backtest's figure is 55% of 672 trades -- momentum "
                f"returns are concentrated by nature, which is what puts wide "
                f"error bars on every other number here."
            )
        else:
            metrics.concentration_note = (
                "Concentration is undefined: the summed trade return is not "
                "positive, and a share of a loss is not a figure anyone can read."
            )

    @staticmethod
    def _curve_metrics(
        metrics: PerformanceMetrics,
        curve: List[Dict[str, Any]],
        cash_flow_days: List[date],
        trades: List[Trade],
    ) -> None:
        points = [point for point in curve if point.get("equity") is not None]
        if len(points) < 2:
            metrics.curve_note = (
                "Not enough history to measure a curve. CAGR, drawdown and MAR "
                "need at least two days of equity."
            )
            return

        first_trade_day = (
            to_ist(trades[0].closed_at).date() if trades else None
        )
        flows_after = [
            day
            for day in cash_flow_days
            if first_trade_day is not None and day > first_trade_day
        ]
        metrics.cash_flows_after_first_trade = len(flows_after)

        equities = [Decimal(str(point["equity"])) for point in points]
        metrics.starting_equity = _q(equities[0])
        metrics.ending_equity = _q(equities[-1])
        metrics.peak_equity = _q(max(equities))

        start = date.fromisoformat(points[0]["date"])
        end = date.fromisoformat(points[-1]["date"])
        metrics.days_covered = (end - start).days

        if flows_after:
            # A deposit is not a gain and a withdrawal is not a drawdown.
            # Reporting either as one would be exactly the flattery this
            # application refuses everywhere else.
            metrics.curve_note = (
                f"CAGR, maximum drawdown and MAR are withheld: money moved into "
                f"or out of this book on {len(flows_after)} day(s) after the "
                f"first trade closed, so the equity curve mixes deposits and "
                f"withdrawals with returns. A deposit is not a gain and a "
                f"withdrawal is not a drawdown."
            )
            return

        # Maximum drawdown, peak to trough on the equity line.
        peak = equities[0]
        peak_day = points[0]["date"]
        worst = Decimal("0")
        worst_amount = ZERO
        worst_from = worst_to = None
        for point, equity in zip(points, equities):
            if equity > peak:
                peak = equity
                peak_day = point["date"]
            if peak > 0:
                drop = (peak - equity) / peak
                if drop > worst:
                    worst = drop
                    worst_amount = peak - equity
                    worst_from = peak_day
                    worst_to = point["date"]
        metrics.max_drawdown = round(float(worst), 6)
        metrics.max_drawdown_amount = _q(worst_amount)
        metrics.max_drawdown_from = worst_from
        metrics.max_drawdown_to = worst_to

        # CAGR. Undefined without a positive starting equity and at least a few
        # days; an annualised figure from a fortnight is noise wearing a
        # percentage sign.
        if metrics.starting_equity and metrics.starting_equity > 0 and metrics.days_covered:
            years = Decimal(metrics.days_covered) / DAYS_PER_YEAR
            if years > 0:
                ratio = metrics.ending_equity / metrics.starting_equity
                if ratio > 0:
                    metrics.cagr = round(
                        float(ratio) ** float(1 / years) - 1.0, 6
                    )
        if metrics.days_covered is not None and metrics.days_covered < 90:
            metrics.curve_note = (
                f"Only {metrics.days_covered} day(s) of history. CAGR is an "
                f"annualised figure and over this window it is arithmetic, not "
                f"a rate of return -- the strategy has no live track record yet."
            )

        if metrics.cagr is not None and metrics.max_drawdown:
            metrics.mar = round(metrics.cagr / metrics.max_drawdown, 4)


def _median(values: List) -> Any:
    if not values:
        return ZERO
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    left, right = ordered[middle - 1], ordered[middle]
    if isinstance(left, Decimal):
        return (left + right) / Decimal("2")
    return (left + right) / 2
