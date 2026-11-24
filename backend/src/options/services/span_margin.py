"""SPAN + exposure margin for a set of NSE option legs. Pure: no I/O.

What a broker blocks for an F&O book is, per underlying ("combined commodity"):

    SPAN     = max(scan risk + calendar spread charge, short option minimum)
    exposure = ELM rate x notional, on SHORT options only
    expiry   = SEBI's extra ELM on short index options expiring today
    MARGIN   = SPAN + exposure + expiry          <- the figure a broker shows

and, separately, the NET OPTION VALUE (long option value minus short option
value at current prices). NSE credits NOV to a member's liquid net worth
rather than netting it inside SPAN, so what a book can no longer SPEND is

    BLOCKED  = MARGIN - NOV

which is what `BalanceService` subtracts from cash. It makes three cases come
out the way a broker's funds screen does: a long option blocks nothing beyond
the premium already paid (its scan risk equals its value); a short blocks
SPAN + exposure AND keeps its premium unspendable (Dhan: "Any option
Receivable will not be consider against initial margin shortfall"); a spread
nets in between. Verified against those three shapes on the 2026-11-23 file.

SCAN RISK is the worst of the exchange's 16 scenarios summed across every leg
on that underlying -- which is where a hedge pays: a short call's loss when
the market rallies is offset by the long call above it in the SAME scenario.
That is the whole "hedge benefit" a broker shows.

On EXPIRY DAY the expiring legs are scanned separately from the rest, which is
"no calendar-spread benefit for a contract expiring that day" (SEBI 2024/132).

When there is no SPAN file, or a contract is missing from it, a leg gets a
FALLBACK array built the SPAN way -- 16 scenarios, Black-76, the card's scan
ranges -- and the result says so. A fallback is never silent.
"""
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

from src.market.services import black76
from src.options.services.span_file import SCENARIOS, SpanData

# Price move (fraction of the scan range) and volatility direction per
# scenario, in SPAN's standard order. 15 and 16 are the extreme moves, whose
# loss is then multiplied by the cover fraction.
SCENARIO_MOVES: Tuple[Tuple[float, int], ...] = (
    (0.0, 1), (0.0, -1),
    (1 / 3, 1), (1 / 3, -1), (-1 / 3, 1), (-1 / 3, -1),
    (2 / 3, 1), (2 / 3, -1), (-2 / 3, 1), (-2 / 3, -1),
    (1.0, 1), (1.0, -1), (-1.0, 1), (-1.0, -1),
    (None, 0), (None, 0),   # extremes, filled from the card
)

KIND_INDEX = "INDEX"
KIND_STOCK = "STOCK"

SOURCE_SPAN = "span"
SOURCE_FALLBACK = "fallback"
SOURCE_MIXED = "mixed"
SOURCE_NONE = "none"


@dataclass(frozen=True)
class Leg:
    """One option position or prospective order, quantity SIGNED in units."""

    underlying: str
    kind: str                       # INDEX / STOCK
    right: str                      # C / P
    expiry: date
    strike: float
    quantity: int                   # +long / -short, in units (lots x lot size)
    spot: Optional[float] = None    # live underlying price, if known
    iv: Optional[float] = None      # live implied vol (fraction), for fallback
    price: Optional[float] = None   # live option price, for the long credit
    ref: Optional[str] = None       # security id, carried through for reporting


@dataclass
class UnderlyingMargin:
    underlying: str
    scan_risk: float = 0.0
    spread_charge: float = 0.0
    short_option_minimum: float = 0.0
    long_option_value: float = 0.0
    short_option_value: float = 0.0
    span: float = 0.0
    exposure: float = 0.0
    expiry_day_addon: float = 0.0
    worst_scenario: Optional[int] = None
    source: str = SOURCE_NONE
    fallback_legs: List[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        """The broker's margin figure: SPAN + exposure + expiry add-on."""
        return self.span + self.exposure + self.expiry_day_addon

    @property
    def net_option_value(self) -> float:
        return self.long_option_value - self.short_option_value

    @property
    def blocked(self) -> float:
        """What the book can no longer spend: margin less net option value."""
        return max(self.total - self.net_option_value, 0.0)

    def as_dict(self) -> Dict[str, object]:
        return {
            "underlying": self.underlying,
            "scanRisk": round(self.scan_risk, 2),
            "spreadCharge": round(self.spread_charge, 2),
            "shortOptionMinimum": round(self.short_option_minimum, 2),
            "longOptionValue": round(self.long_option_value, 2),
            "shortOptionValue": round(self.short_option_value, 2),
            "netOptionValue": round(self.net_option_value, 2),
            "blocked": round(self.blocked, 2),
            # What a broker labels SPAN: scan risk less net option value, so a
            # short's premium sits inside it (Zerodha's 1,55,675 on 2026-11-24
            # was exactly scan 1,38,543.6 + premium 17,131).
            "brokerSpan": round(max(self.span - self.net_option_value, 0.0), 2),
            "span": round(self.span, 2),
            "exposure": round(self.exposure, 2),
            "expiryDayAddon": round(self.expiry_day_addon, 2),
            "total": round(self.total, 2),
            "worstScenario": self.worst_scenario,
            "source": self.source,
            "fallbackLegs": list(self.fallback_legs),
        }


@dataclass
class PortfolioMargin:
    underlyings: Dict[str, UnderlyingMargin] = field(default_factory=dict)
    span_file_date: Optional[date] = None

    @property
    def span(self) -> float:
        return sum(one.span for one in self.underlyings.values())

    @property
    def exposure(self) -> float:
        return sum(one.exposure for one in self.underlyings.values())

    @property
    def expiry_day_addon(self) -> float:
        return sum(one.expiry_day_addon for one in self.underlyings.values())

    @property
    def total(self) -> float:
        return self.span + self.exposure + self.expiry_day_addon

    @property
    def net_option_value(self) -> float:
        return sum(one.net_option_value for one in self.underlyings.values())

    @property
    def blocked(self) -> float:
        return sum(one.blocked for one in self.underlyings.values())

    @property
    def source(self) -> str:
        sources = {one.source for one in self.underlyings.values() if one.source != SOURCE_NONE}
        if not sources:
            return SOURCE_NONE
        if sources == {SOURCE_SPAN}:
            return SOURCE_SPAN
        if sources == {SOURCE_FALLBACK}:
            return SOURCE_FALLBACK
        return SOURCE_MIXED

    def as_dict(self) -> Dict[str, object]:
        return {
            "span": round(self.span, 2),
            "exposure": round(self.exposure, 2),
            "expiryDayAddon": round(self.expiry_day_addon, 2),
            "total": round(self.total, 2),
            "netOptionValue": round(self.net_option_value, 2),
            "blocked": round(self.blocked, 2),
            "brokerSpan": round(sum(max(one.span - one.net_option_value, 0.0) for one in self.underlyings.values()), 2),
            "source": self.source,
            "spanFileDate": self.span_file_date.isoformat() if self.span_file_date else None,
            "underlyings": [one.as_dict() for one in self.underlyings.values()],
        }


class MarginCard:
    """The parts of conf/margins/<card>.yaml the calculator reads."""

    def __init__(self, document: Dict):
        elm = document["exposure_margin"]
        self.index = elm["index_options"]
        self.stock = elm["stock_options"]
        expiry = document.get("expiry_day") or {}
        self.expiry_index_rate = float(expiry.get("index_short_additional_rate", 0))
        self.remove_calendar_benefit = bool(expiry.get("remove_calendar_benefit", True))
        fallback = document["fallback"]
        self.index_price_scan = float(fallback["index_price_scan"])
        self.stock_price_scan = float(fallback["stock_price_scan"])
        self.volatility_scan = float(fallback["volatility_scan"])
        self.extreme_multiple = float(fallback["extreme_move_multiple"])
        self.extreme_cover = float(fallback["extreme_cover_fraction"])
        self.default_iv = float(fallback["default_iv"])
        self.version = str(document.get("version") or "")
        desk = document.get("risk_desk") or {}
        self.intraday_square_off_at = _hhmm(desk.get("intraday_square_off_at", "15:25"))
        self.intraday_entry_cutoff_at = _hhmm(desk.get("intraday_entry_cutoff_at", "15:23"))
        self.mtm_loss_fraction = float(desk.get("mtm_loss_fraction", 0.80))
        expiry = document.get("expiry_settlement") or {}
        self.no_fresh_stock_options_on_expiry = bool(
            expiry.get("no_fresh_stock_option_positions_on_expiry_day", True)
        )
        self.delivery_margin_from = _hhmm(expiry.get("delivery_margin_from", "10:00"))
        self.delivery_margin_fraction = float(expiry.get("delivery_margin_fraction", 1.0))
        self.settle_after = _hhmm(expiry.get("settle_after", "15:45"))
        self.exercise_fee_per_order = float(expiry.get("exercise_fee_per_order", 20.0))
        self.physical_delivery_charge_rate = float(expiry.get("physical_delivery_charge_rate", 0.001))
        self.delivery_rate_card = str(expiry.get("delivery_rate_card") or "nse-equity-delivery")
        parity = document.get("broker_parity") or {}
        self.broker = str(parity.get("broker") or "exchange")
        self.valuation = str(parity.get("valuation") or "live")
        self.count_premium_received = bool(parity.get("count_premium_received", True))

    @property
    def values_at_span_file(self) -> bool:
        return self.valuation == "span_file"


def _hhmm(value):
    from datetime import time as _time

    hour, minute = str(value).split(":")
    return _time(int(hour), int(minute))


# --- risk arrays --------------------------------------------------------------
def fallback_array(leg: Leg, card: MarginCard, today: date) -> Optional[Tuple[float, ...]]:
    """A SPAN-shaped array for one LONG unit, from Black-76. None without a spot."""
    if not leg.spot:
        return None
    spot = float(leg.spot)
    years = max((leg.expiry - today).days, 0) / 365.0
    vol = leg.iv if leg.iv and leg.iv > 0 else card.default_iv
    option_type = "CE" if leg.right == "C" else "PE"
    scan = spot * (card.index_price_scan if leg.kind == KIND_INDEX else card.stock_price_scan)
    now = black76.price(spot, leg.strike, years, vol, option_type=option_type)
    values: List[float] = []
    for index, (move, vol_direction) in enumerate(SCENARIO_MOVES):
        if move is None:
            direction = 1.0 if index == 14 else -1.0
            shocked = spot + direction * card.extreme_multiple * scan
            value = black76.price(max(shocked, 0.01), leg.strike, years, vol, option_type=option_type)
            values.append((now - value) * card.extreme_cover)
            continue
        shocked = spot + move * scan
        shocked_vol = max(vol + vol_direction * card.volatility_scan, 0.01)
        value = black76.price(max(shocked, 0.01), leg.strike, years, shocked_vol, option_type=option_type)
        values.append(now - value)
    return tuple(values)


def _resolve(leg: Leg, data: Optional[SpanData], card: MarginCard, today: date):
    """(array, delta, price, source) for one long unit, or None if impossible."""
    if data is not None:
        entry = data.entry(leg.underlying, leg.right, leg.expiry, leg.strike)
        if entry is not None:
            return entry.array, entry.delta, entry.price, SOURCE_SPAN
    if data is not None and not leg.spot:
        spot = data.underlying_price.get(leg.underlying)
        if spot:
            leg = Leg(**{**leg.__dict__, "spot": spot})
    array = fallback_array(leg, card, today)
    if array is None:
        return None
    years = max((leg.expiry - today).days, 0) / 365.0
    vol = leg.iv if leg.iv and leg.iv > 0 else card.default_iv
    option_type = "CE" if leg.right == "C" else "PE"
    delta = black76.greeks(float(leg.spot), leg.strike, years, vol, option_type=option_type)["delta"] if years > 0 else 0.0
    price = black76.price(float(leg.spot), leg.strike, years, vol, option_type=option_type)
    return array, delta, price, SOURCE_FALLBACK


# --- the calculation ---------------------------------------------------------
def _scan(arrays: Iterable[Tuple[int, Tuple[float, ...]]]) -> Tuple[float, Optional[int]]:
    totals = [0.0] * SCENARIOS
    any_leg = False
    for quantity, array in arrays:
        any_leg = True
        for index in range(SCENARIOS):
            totals[index] += quantity * array[index]
    if not any_leg:
        return 0.0, None
    worst = max(range(SCENARIOS), key=lambda index: totals[index])
    return max(totals[worst], 0.0), worst + 1


def _spread_charge(deltas: Dict[str, float], spreads) -> float:
    """Calendar spread charge: flat rate per unit of delta matched across expiries."""
    remaining = dict(deltas)
    charge = 0.0
    for spread in spreads or ():
        if len(spread.legs) != 2:
            continue
        (pe_a, _side_a, ratio_a), (pe_b, _side_b, ratio_b) = spread.legs
        a, b = remaining.get(pe_a, 0.0), remaining.get(pe_b, 0.0)
        if a == 0 or b == 0 or (a > 0) == (b > 0):
            continue
        matched = min(abs(a) / ratio_a, abs(b) / ratio_b)
        if matched <= 0:
            continue
        charge += matched * spread.rate
        remaining[pe_a] = a - math.copysign(matched * ratio_a, a)
        remaining[pe_b] = b - math.copysign(matched * ratio_b, b)
    return charge


def _elm_rate(leg: Leg, card: MarginCard, today: date) -> float:
    spot = float(leg.spot or 0)
    if leg.kind == KIND_INDEX:
        params = card.index
        if (leg.expiry - today).days > int(params.get("long_dated_after_days", 10 ** 6)):
            return float(params["long_dated_rate"])
    else:
        params = card.stock
    if spot > 0:
        otm = (leg.strike - spot) / spot if leg.right == "C" else (spot - leg.strike) / spot
        if otm > float(params["deep_otm_beyond"]):
            return float(params["deep_otm_rate"])
    return float(params["base_rate"])


def compute(
    legs: Iterable[Leg],
    data: Optional[SpanData],
    card: MarginCard,
    today: date,
) -> PortfolioMargin:
    """Margin for a whole portfolio's NSE option legs. Legs on one underlying net."""
    result = PortfolioMargin(span_file_date=data.business_date if data else None)
    by_underlying: Dict[str, List[Leg]] = {}
    for leg in legs:
        if leg.quantity:
            by_underlying.setdefault(leg.underlying, []).append(leg)

    for underlying, group in sorted(by_underlying.items()):
        margin = UnderlyingMargin(underlying=underlying)
        sources = set()
        expiring: List[Tuple[int, Tuple[float, ...]]] = []
        rest: List[Tuple[int, Tuple[float, ...]]] = []
        deltas: Dict[str, float] = {}
        short_units = 0
        for leg in group:
            resolved = _resolve(leg, data, card, today)
            if resolved is None:
                margin.fallback_legs.append(f"{leg.ref or leg.strike}: no price to scan")
                continue
            array, delta, file_price, source = resolved
            sources.add(source)
            if source == SOURCE_FALLBACK:
                margin.fallback_legs.append(str(leg.ref or f"{leg.strike}{leg.right}"))
            separate = card.remove_calendar_benefit and leg.expiry == today
            (expiring if separate else rest).append((leg.quantity, array))
            if not separate:
                key = leg.expiry.strftime("%Y%m%d")
                deltas[key] = deltas.get(key, 0.0) + leg.quantity * delta
            price = float(leg.price if leg.price is not None else (file_price or 0))
            if leg.quantity > 0:
                margin.long_option_value += leg.quantity * price
            else:
                margin.short_option_value += -leg.quantity * price
                short_units += -leg.quantity
                spot = leg.spot or (data.underlying_price.get(underlying) if data else None)
                if spot:
                    notional = float(spot) * -leg.quantity
                    priced = Leg(**{**leg.__dict__, "spot": spot})
                    margin.exposure += notional * _elm_rate(priced, card, today)
                    if leg.kind == KIND_INDEX and leg.expiry == today:
                        margin.expiry_day_addon += notional * card.expiry_index_rate

        scan_rest, worst_rest = _scan(rest)
        scan_expiring, worst_expiring = _scan(expiring)
        margin.scan_risk = scan_rest + scan_expiring
        margin.worst_scenario = worst_rest or worst_expiring
        margin.spread_charge = _spread_charge(deltas, data.spreads.get(underlying) if data else None)
        som_rate = data.som_rate.get(underlying, 0.0) if data else 0.0
        margin.short_option_minimum = som_rate * short_units
        risk = max(margin.scan_risk + margin.spread_charge, margin.short_option_minimum)
        margin.span = risk
        margin.source = (
            SOURCE_NONE if not sources
            else SOURCE_SPAN if sources == {SOURCE_SPAN}
            else SOURCE_FALLBACK if sources == {SOURCE_FALLBACK}
            else SOURCE_MIXED
        )
        result.underlyings[underlying] = margin
    return result
