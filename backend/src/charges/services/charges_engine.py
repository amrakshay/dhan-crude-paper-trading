"""MCX commodity-options charges engine.

Correct and auditable is the whole point of this module. Rules it follows:

* Every rate comes from ``conf/charges.yaml``, which carries a primary source
  URL and an as-of date beside each number. No rate is hardcoded here.
* All arithmetic is ``Decimal``. Floats would make the totals irreproducible.
* Every computed charge is returned with the inputs and the formula that
  produced it, so any figure on a contract note can be explained later.
* Commodity options attract **CTT, not STT**, and CTT is charged on the SELL
  side of the premium.
* GST applies to brokerage + exchange transaction charge + SEBI turnover fee.
  It does NOT apply to the trade value, to CTT, or to stamp duty.

Rounding is a deliberate, configurable choice rather than an accident -- see
`rounding.mode` in the rate card. Discount brokers round stamp duty to whole
rupees, which changes small-premium totals by up to a rupee; both behaviours
are reproducible and tested against a published broker calculator.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from src import config_utils
from src.constants import OrderSide
from src.logging_config import get_logger

logger = get_logger("charges.engine")

ZERO = Decimal("0")
PAISE = Decimal("0.01")
RUPEE = Decimal("1")


class ChargesConfigError(Exception):
    pass


@dataclass(frozen=True)
class ChargeComponent:
    """One line item, with enough context to audit it."""

    name: str
    amount: Decimal
    rate: Optional[Decimal] = None
    base: Optional[Decimal] = None
    formula: str = ""
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "amount": str(self.amount),
            "rate": str(self.rate) if self.rate is not None else None,
            "base": str(self.base) if self.base is not None else None,
            "formula": self.formula,
            "note": self.note,
        }


@dataclass(frozen=True)
class ChargeBreakdown:
    """Full charges for one order (or one leg of a round trip)."""

    turnover: Decimal
    brokerage: Decimal
    ctt: Decimal
    exchange_transaction_charge: Decimal
    sebi_turnover_fee: Decimal
    stamp_duty: Decimal
    gst: Decimal
    total: Decimal
    rates_version: str
    rounding_mode: str
    components: List[ChargeComponent] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "turnover": str(self.turnover),
            "brokerage": str(self.brokerage),
            "ctt": str(self.ctt),
            "exchangeTransactionCharge": str(self.exchange_transaction_charge),
            "sebiTurnoverFee": str(self.sebi_turnover_fee),
            "stampDuty": str(self.stamp_duty),
            "gst": str(self.gst),
            "total": str(self.total),
            "ratesVersion": self.rates_version,
            "roundingMode": self.rounding_mode,
            "components": [component.as_dict() for component in self.components],
        }

    def __add__(self, other: "ChargeBreakdown") -> "ChargeBreakdown":
        """Combine two legs (e.g. the buy and sell of a round trip)."""
        if not isinstance(other, ChargeBreakdown):
            return NotImplemented
        return ChargeBreakdown(
            turnover=self.turnover + other.turnover,
            brokerage=self.brokerage + other.brokerage,
            ctt=self.ctt + other.ctt,
            exchange_transaction_charge=(
                self.exchange_transaction_charge + other.exchange_transaction_charge
            ),
            sebi_turnover_fee=self.sebi_turnover_fee + other.sebi_turnover_fee,
            stamp_duty=self.stamp_duty + other.stamp_duty,
            gst=self.gst + other.gst,
            total=self.total + other.total,
            rates_version=self.rates_version,
            rounding_mode=self.rounding_mode,
            components=[*self.components, *other.components],
        )


def _decimal(value: Any, name: str) -> Decimal:
    if value is None:
        raise ChargesConfigError(f"Missing charge rate: {name}")
    return Decimal(str(value))


class ChargesEngine:
    """Computes charges from the YAML rate card.

    The rate card is loaded from ``<CONFIG_PATH>/charges.yaml`` and cached; call
    ``reload()`` after editing it.
    """

    _rates: Optional[Dict[str, Any]] = None

    def __init__(self, rates: Optional[Dict[str, Any]] = None) -> None:
        self._override = rates

    # --- rate card ---------------------------------------------------------
    @classmethod
    def load_rates(cls, force: bool = False) -> Dict[str, Any]:
        if cls._rates is not None and not force:
            return cls._rates

        import os

        import yaml

        path = os.path.join(config_utils.get_config_path(), "charges.yaml")
        if not os.path.exists(path):
            raise ChargesConfigError(
                f"Charge rate card not found at {path}. It carries every rate "
                "used by this application, with its source and as-of date."
            )
        with open(path, "r", encoding="utf-8") as handle:
            cls._rates = yaml.safe_load(handle) or {}
        logger.info("Loaded charge rates version %s", cls._rates.get("version"))
        return cls._rates

    @classmethod
    def reload(cls) -> Dict[str, Any]:
        return cls.load_rates(force=True)

    @property
    def rates(self) -> Dict[str, Any]:
        return self._override if self._override is not None else self.load_rates()

    @property
    def version(self) -> str:
        return str(self.rates.get("version", "unknown"))

    @property
    def rounding_mode(self) -> str:
        return str((self.rates.get("rounding") or {}).get("mode", "exact"))

    def _total_dp(self) -> Decimal:
        places = int(
            (self.rates.get("rounding") or {}).get("final_total_decimal_places", 2)
        )
        return Decimal(1).scaleb(-places)

    # --- rounding policy ---------------------------------------------------
    def _round_money(self, value: Decimal) -> Decimal:
        return value.quantize(self._total_dp(), rounding=ROUND_HALF_UP)

    def _apply_intermediate_rounding(
        self, sebi_fee: Decimal, stamp_duty: Decimal
    ) -> tuple:
        """Round components before GST, per the configured mode.

        In broker_compatible mode this reproduces a discount broker's displayed
        note: stamp duty to the nearest rupee, SEBI fee to 2dp. The SEBI
        rounding uses ROUND_HALF_EVEN because that is what a JavaScript
        toFixed(2) does to the binary representation of values like 0.025.
        """
        if self.rounding_mode != "broker_compatible":
            return sebi_fee, stamp_duty
        return (
            sebi_fee.quantize(PAISE, rounding=ROUND_HALF_EVEN),
            stamp_duty.quantize(RUPEE, rounding=ROUND_HALF_UP),
        )

    # --- individual components --------------------------------------------
    def compute_turnover(
        self, premium: Decimal, lot_size: int, lots: int
    ) -> Decimal:
        """Premium turnover in rupees.

        CRUDEOIL is quoted in rupees per barrel and one lot is 100 barrels, so
        a premium of 100 on one lot is a turnover of Rs 10,000.
        """
        return _decimal(premium, "premium") * Decimal(lot_size) * Decimal(lots)

    def compute_brokerage(
        self, turnover: Decimal, strike_price: Optional[Decimal] = None,
        lot_size: int = 1, lots: int = 1,
    ) -> ChargeComponent:
        config = self.rates.get("brokerage") or {}
        mode = str(config.get("mode", "flat"))

        if mode == "flat":
            amount = _decimal(config.get("flat_per_order"), "brokerage.flat_per_order")
            return ChargeComponent(
                name="brokerage",
                amount=amount,
                base=None,
                formula=f"flat Rs {amount} per executed order",
            )

        rate = _decimal(
            config.get("percentage_of_turnover"), "brokerage.percentage_of_turnover"
        )
        base = turnover
        note = ""
        if config.get("include_strike_in_base") and strike_price is not None:
            # Zerodha's published commodity-options formula puts the strike in
            # the base alongside the premium. Off by default because it is a
            # broker quirk, not a market convention.
            scale = Decimal(lot_size) * Decimal(lots)
            base = (base / scale + Decimal(strike_price)) * scale if scale else base
            note = "strike included in the brokerage base (broker-specific formula)"

        amount = rate * base
        cap = config.get("max_per_order")
        if cap is not None:
            capped = _decimal(cap, "brokerage.max_per_order")
            if amount > capped:
                amount = capped
                note = (note + "; " if note else "") + f"capped at Rs {capped}"

        return ChargeComponent(
            name="brokerage",
            amount=amount,
            rate=rate,
            base=base,
            formula=f"{rate} x {base}",
            note=note,
        )

    def compute_ctt(self, side: str, turnover: Decimal) -> ChargeComponent:
        """CTT -- Commodities Transaction Tax, sell side of the premium only."""
        rate = _decimal(
            (self.rates.get("ctt") or {}).get("sell_option_premium_rate"),
            "ctt.sell_option_premium_rate",
        )
        if str(side).upper() != OrderSide.SELL.value:
            return ChargeComponent(
                name="ctt",
                amount=ZERO,
                rate=rate,
                base=ZERO,
                formula="not charged on the buy side",
                note="Commodity options attract CTT (not STT), on the sell side only",
            )
        return ChargeComponent(
            name="ctt",
            amount=rate * turnover,
            rate=rate,
            base=turnover,
            formula=f"{rate} x {turnover}",
            note="Finance Act 2013 s.117 Sl.3: sale of option on commodity derivative",
        )

    def compute_exchange_transaction_charge(self, turnover: Decimal) -> ChargeComponent:
        rate = _decimal(
            (self.rates.get("exchange_transaction_charge") or {}).get(
                "option_premium_rate"
            ),
            "exchange_transaction_charge.option_premium_rate",
        )
        return ChargeComponent(
            name="exchange_transaction_charge",
            amount=rate * turnover,
            rate=rate,
            base=turnover,
            formula=f"{rate} x {turnover}",
            note="MCX circular MCX/F&A/631/2024: Rs 41.80 per lakh of premium turnover",
        )

    def compute_sebi_turnover_fee(self, turnover: Decimal) -> ChargeComponent:
        rate = _decimal(
            (self.rates.get("sebi_turnover_fee") or {}).get("rate"),
            "sebi_turnover_fee.rate",
        )
        return ChargeComponent(
            name="sebi_turnover_fee",
            amount=rate * turnover,
            rate=rate,
            base=turnover,
            formula=f"{rate} x {turnover}",
            note="Rs 10 per crore, non-agri commodity derivatives",
        )

    def compute_stamp_duty(self, side: str, turnover: Decimal) -> ChargeComponent:
        """Stamp duty -- buy side only."""
        rate = _decimal(
            (self.rates.get("stamp_duty") or {}).get("option_premium_rate"),
            "stamp_duty.option_premium_rate",
        )
        if str(side).upper() != OrderSide.BUY.value:
            return ChargeComponent(
                name="stamp_duty",
                amount=ZERO,
                rate=rate,
                base=ZERO,
                formula="not charged on the sell side",
            )
        return ChargeComponent(
            name="stamp_duty",
            amount=rate * turnover,
            rate=rate,
            base=turnover,
            formula=f"{rate} x {turnover}",
            note="Indian Stamp Act rules 2019, buy side only",
        )

    def compute_gst(
        self, brokerage: Decimal, exchange_charge: Decimal, sebi_fee: Decimal
    ) -> ChargeComponent:
        """GST on the service charges only -- never on the trade value."""
        config = self.rates.get("gst") or {}
        rate = _decimal(config.get("rate"), "gst.rate")
        base = brokerage + exchange_charge + sebi_fee
        return ChargeComponent(
            name="gst",
            amount=rate * base,
            rate=rate,
            base=base,
            formula=f"{rate} x (brokerage + exchange charge + SEBI fee) = {rate} x {base}",
            note="Not applied to trade value, CTT or stamp duty",
        )

    # --- order-level API ---------------------------------------------------
    def compute_order_charges(
        self,
        side: str,
        premium: Decimal,
        lot_size: int,
        lots: int = 1,
        strike_price: Optional[Decimal] = None,
    ) -> ChargeBreakdown:
        """Charges for one executed order."""
        if lots <= 0:
            raise ValueError("lots must be positive")
        if lot_size <= 0:
            raise ValueError("lot_size must be positive")

        side = str(side).upper()
        premium = _decimal(premium, "premium")
        if premium < 0:
            raise ValueError("premium cannot be negative")

        turnover = self.compute_turnover(premium, lot_size, lots)
        return self._compute(side, turnover, strike_price, lot_size, lots)

    def _compute(
        self,
        side: str,
        turnover: Decimal,
        strike_price: Optional[Decimal],
        lot_size: int,
        lots,
    ) -> ChargeBreakdown:
        """Shared charge computation over a turnover figure."""
        brokerage = self.compute_brokerage(turnover, strike_price, lot_size, lots)
        ctt = self.compute_ctt(side, turnover)
        exchange_charge = self.compute_exchange_transaction_charge(turnover)
        sebi_fee = self.compute_sebi_turnover_fee(turnover)
        stamp_duty = self.compute_stamp_duty(side, turnover)

        sebi_amount, stamp_amount = self._apply_intermediate_rounding(
            sebi_fee.amount, stamp_duty.amount
        )
        gst = self.compute_gst(brokerage.amount, exchange_charge.amount, sebi_amount)
        gst_amount = gst.amount
        if self.rounding_mode == "broker_compatible":
            gst_amount = gst_amount.quantize(PAISE, rounding=ROUND_HALF_UP)

        total = (
            brokerage.amount
            + ctt.amount
            + exchange_charge.amount
            + sebi_amount
            + stamp_amount
            + gst_amount
        )

        return ChargeBreakdown(
            turnover=self._round_money(turnover),
            brokerage=self._round_money(brokerage.amount),
            ctt=self._round_money(ctt.amount),
            exchange_transaction_charge=self._round_money(exchange_charge.amount),
            sebi_turnover_fee=self._round_money(sebi_amount),
            stamp_duty=self._round_money(stamp_amount),
            gst=self._round_money(gst_amount),
            total=self._round_money(total),
            rates_version=self.version,
            rounding_mode=self.rounding_mode,
            components=[brokerage, ctt, exchange_charge, sebi_fee, stamp_duty, gst],
        )

    def compute_order_charges_for_quantity(
        self,
        side: str,
        premium: Decimal,
        quantity: int,
        lot_size: int,
        strike_price: Optional[Decimal] = None,
    ) -> ChargeBreakdown:
        """Charges for an executed quantity that may not be a whole number of lots.

        A partially filled order has executed some number of barrels, not some
        number of lots. Turnover is premium x quantity directly; brokerage is
        still charged once, because it is per executed ORDER.
        """
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        # Expressed as a fractional lot count so the shared implementation does
        # the turnover arithmetic; lot_size cancels out.
        equivalent_lots = Decimal(quantity) / Decimal(lot_size)
        return self._compute(
            side=str(side).upper(),
            turnover=_decimal(premium, "premium") * Decimal(quantity),
            strike_price=strike_price,
            lot_size=lot_size,
            lots=equivalent_lots,
        )

    def compute_round_trip_charges(
        self,
        buy_premium: Decimal,
        sell_premium: Decimal,
        lot_size: int,
        lots: int = 1,
        strike_price: Optional[Decimal] = None,
    ) -> ChargeBreakdown:
        """Charges for a complete round trip, as the sum of its two legs."""
        buy = self.compute_order_charges(
            OrderSide.BUY.value, buy_premium, lot_size, lots, strike_price
        )
        sell = self.compute_order_charges(
            OrderSide.SELL.value, sell_premium, lot_size, lots, strike_price
        )
        return buy + sell

    def compute_exercise_charges(
        self, settlement_price: Decimal, lot_size: int, lots: int = 1
    ) -> ChargeBreakdown:
        """CTT payable by the option BUYER when an option is exercised.

        Finance Act 2013 s.117 Sl.5: 0.0001% of the settlement price. No broker
        calculator publishes this leg, so it will not tie to one -- it is
        modelled because MCX crude options are European and devolve into
        futures at expiry.
        """
        settlement_price = _decimal(settlement_price, "settlement_price")
        notional = settlement_price * Decimal(lot_size) * Decimal(lots)

        rate = _decimal(
            (self.rates.get("ctt") or {}).get("exercise_settlement_rate"),
            "ctt.exercise_settlement_rate",
        )
        ctt = ChargeComponent(
            name="ctt_exercise",
            amount=rate * notional,
            rate=rate,
            base=notional,
            formula=f"{rate} x {notional}",
            note="Finance Act 2013 s.117 Sl.5, payable by the purchaser on exercise",
        )

        sebi_config = self.rates.get("sebi_turnover_fee") or {}
        sebi_amount = ZERO
        components = [ctt]
        if sebi_config.get("charge_on_exercise_notional"):
            sebi_rate = _decimal(sebi_config.get("rate"), "sebi_turnover_fee.rate")
            sebi_component = ChargeComponent(
                name="sebi_turnover_fee_exercise",
                amount=sebi_rate * notional,
                rate=sebi_rate,
                base=notional,
                formula=f"{sebi_rate} x {notional}",
                note="SEBI charges the fee on exercised/assigned notional; brokers omit it",
            )
            sebi_amount = sebi_component.amount
            components.append(sebi_component)

        total = ctt.amount + sebi_amount
        return ChargeBreakdown(
            turnover=self._round_money(notional),
            brokerage=ZERO,
            ctt=self._round_money(ctt.amount),
            exchange_transaction_charge=ZERO,
            sebi_turnover_fee=self._round_money(sebi_amount),
            stamp_duty=ZERO,
            gst=ZERO,
            total=self._round_money(total),
            rates_version=self.version,
            rounding_mode=self.rounding_mode,
            components=components,
        )

    def rate_card(self) -> Dict[str, Any]:
        """The active rates, for display in the UI."""
        return self.rates
