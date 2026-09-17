"""Charges engine.

The ENGINE is generic; the RATES are not. A strategy module names the rate card
it is charged under, and the card is read from
``<CONFIG_PATH>/charges/<card>.yaml``. Commodity options pay CTT on the sell
side of the premium; an equity strategy would pay STT, which is a different tax
on a different transaction -- so it gets a different card, not a different
number in this one.

Correct and auditable is the whole point of this module. Rules it follows:

* Every rate comes from the rate card, which carries a primary source URL and
  an as-of date beside each number. No rate is hardcoded here.
* All arithmetic is ``Decimal``. Floats would make the totals irreproducible.
* Every computed charge is returned with the inputs and the formula that
  produced it, so any figure on a contract note can be explained later.
* Commodity options attract **CTT, not STT**, and CTT is charged on the SELL
  side of the premium.
* GST applies to brokerage + exchange transaction charge + SEBI turnover fee.
  It does NOT apply to the trade value, to CTT, or to stamp duty.
* **The component list is the breakdown.** `ChargeBreakdown.components` is the
  source of truth and is what gets persisted, returned and exported; the named
  attributes (`ctt`, `gst`, ...) are conveniences that read out of it. That is
  what lets a new tax appear without a schema migration, and what stops an
  equity card having to pretend its STT is a CTT.

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
    # The unrounded figure `amount` was quantised from. Kept so a disputed
    # paisa can be traced to the arithmetic rather than to the 2dp result.
    raw_amount: Optional[Decimal] = None
    label: Optional[str] = None

    def display_label(self) -> str:
        """Human label. Falls back to the name, so a card can add a tax
        without the UI needing to learn it."""
        if self.label:
            return self.label
        return self.name.replace("_", " ").strip().capitalize()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.display_label(),
            "amount": str(self.amount),
            "rawAmount": str(self.raw_amount) if self.raw_amount is not None else None,
            "rate": str(self.rate) if self.rate is not None else None,
            "base": str(self.base) if self.base is not None else None,
            "formula": self.formula,
            "note": self.note,
        }

    def with_label(self, label: Optional[str]) -> "ChargeComponent":
        if not label:
            return self
        return ChargeComponent(
            name=self.name, amount=self.amount, rate=self.rate, base=self.base,
            formula=self.formula, note=self.note, raw_amount=self.raw_amount,
            label=label,
        )

    def rounded(self, amount: Decimal) -> "ChargeComponent":
        """The same component carrying its final, rounded amount."""
        return ChargeComponent(
            name=self.name,
            amount=amount,
            rate=self.rate,
            base=self.base,
            formula=self.formula,
            note=self.note,
            raw_amount=self.amount,
            label=self.label,
        )


@dataclass(frozen=True)
class ChargeBreakdown:
    """Full charges for one order (or one leg of a round trip).

    `components` is the breakdown. `turnover`, `total`, `rates_version` and
    `rounding_mode` are the only scalars that stand on their own -- they are
    aggregated and queried, so they stay real columns. Everything else is a
    line item whose NAME comes from the rate card, which is what lets a card
    introduce a tax this codebase has never heard of without a migration, a
    schema change or a hardcoded label.

    The named properties below (`ctt`, `gst`, ...) read out of the component
    list. They are conveniences for a commodity card, not structure.
    """

    turnover: Decimal
    total: Decimal
    rates_version: str
    rounding_mode: str
    components: List[ChargeComponent] = field(default_factory=list)

    # --- reading components ------------------------------------------------
    def amount(self, name: str) -> Decimal:
        """One component's final amount, or zero when the card has no such
        charge. Zero is correct here: a card that does not levy a tax levies
        zero of it."""
        return sum(
            (component.amount for component in self.components if component.name == name),
            ZERO,
        )

    @property
    def brokerage(self) -> Decimal:
        return self.amount("brokerage")

    @property
    def ctt(self) -> Decimal:
        return self.amount("ctt")

    @property
    def exchange_transaction_charge(self) -> Decimal:
        return self.amount("exchange_transaction_charge")

    @property
    def sebi_turnover_fee(self) -> Decimal:
        return self.amount("sebi_turnover_fee")

    @property
    def stamp_duty(self) -> Decimal:
        return self.amount("stamp_duty")

    @property
    def gst(self) -> Decimal:
        return self.amount("gst")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "turnover": str(self.turnover),
            "total": str(self.total),
            "ratesVersion": self.rates_version,
            "roundingMode": self.rounding_mode,
            "components": [component.as_dict() for component in self.components],
        }

    def component_amounts(self) -> Dict[str, Decimal]:
        """{name: amount}, summed across legs. What the reports aggregate."""
        totals: Dict[str, Decimal] = {}
        for component in self.components:
            totals[component.name] = totals.get(component.name, ZERO) + component.amount
        return totals

    def __add__(self, other: "ChargeBreakdown") -> "ChargeBreakdown":
        """Combine two legs (e.g. the buy and sell of a round trip).

        Components with the same name are merged, so a round trip shows one
        `ctt` line rather than two. The formula and note of the first leg are
        kept; the rate and base are dropped, because a merged line item has no
        single base and printing one of the two would be a lie.
        """
        if not isinstance(other, ChargeBreakdown):
            return NotImplemented

        merged: List[ChargeComponent] = []
        seen: Dict[str, int] = {}
        for component in [*self.components, *other.components]:
            if component.name in seen:
                index = seen[component.name]
                existing = merged[index]
                merged[index] = ChargeComponent(
                    name=existing.name,
                    amount=existing.amount + component.amount,
                    rate=existing.rate if existing.rate == component.rate else None,
                    base=None,
                    formula="combined across legs",
                    note=existing.note,
                    raw_amount=(
                        (existing.raw_amount or existing.amount)
                        + (component.raw_amount or component.amount)
                    ),
                    label=existing.label,
                )
            else:
                seen[component.name] = len(merged)
                merged.append(component)

        return ChargeBreakdown(
            turnover=self.turnover + other.turnover,
            total=self.total + other.total,
            rates_version=self.rates_version,
            rounding_mode=self.rounding_mode,
            components=merged,
        )


def _decimal(value: Any, name: str) -> Decimal:
    if value is None:
        raise ChargesConfigError(f"Missing charge rate: {name}")
    return Decimal(str(value))


class ChargesEngine:
    """Computes charges from a rate card.

    The card is chosen by name -- ``<CONFIG_PATH>/charges/<card>.yaml`` -- and
    each strategy module names the card it is charged under. Cards are cached
    per name; call ``reload()`` after editing one.
    """

    # One cache entry per card, so two strategies on two cards do not evict
    # each other.
    _rate_cards: Dict[str, Dict[str, Any]] = {}

    DEFAULT_RATE_CARD = "mcx-commodity-options"
    CHARGES_DIRNAME = "charges"
    # The card lived at conf/charges.yaml until 2026-09-18, when it moved into
    # conf/charges/ so a second strategy could have its own. A config directory
    # that still has the old file keeps working rather than failing to price.
    LEGACY_RATE_CARD_FILE = "charges.yaml"

    def __init__(
        self,
        rates: Optional[Dict[str, Any]] = None,
        rate_card: Optional[str] = None,
    ) -> None:
        self._override = rates
        self._rate_card = rate_card or self.DEFAULT_RATE_CARD

    @classmethod
    def for_strategy(cls, strategy) -> "ChargesEngine":
        """The engine a strategy's trades are charged by."""
        return cls(rate_card=strategy.charges_rate_card)

    @classmethod
    def for_strategy_key(cls, strategy_key: Optional[str]) -> "ChargesEngine":
        """The engine for a stored strategy key.

        A key that no longer names a configured strategy falls back to the
        default card rather than failing: an old order still has to be
        explainable after its strategy has been removed.
        """
        from src.strategies.services.strategy_registry import get_strategy_registry

        strategy = get_strategy_registry().get(strategy_key) if strategy_key else None
        if strategy is None:
            return cls()
        return cls.for_strategy(strategy)

    @property
    def rate_card_name(self) -> str:
        """Which card this engine is charging under."""
        return self._rate_card

    # --- rate card ---------------------------------------------------------
    @classmethod
    def card_path(cls, card: str) -> str:
        import os

        return os.path.join(
            config_utils.get_config_path(), cls.CHARGES_DIRNAME, f"{card}.yaml"
        )

    @classmethod
    def load_rates(
        cls, force: bool = False, card: Optional[str] = None
    ) -> Dict[str, Any]:
        card = card or cls.DEFAULT_RATE_CARD
        if not force and card in cls._rate_cards:
            return cls._rate_cards[card]

        import os

        import yaml

        path = cls.card_path(card)
        if not os.path.exists(path):
            legacy = os.path.join(
                config_utils.get_config_path(), cls.LEGACY_RATE_CARD_FILE
            )
            if card == cls.DEFAULT_RATE_CARD and os.path.exists(legacy):
                logger.warning(
                    "Reading the rate card from the pre-2026-09-18 location %s. "
                    "Move it to %s so a second strategy can have its own card.",
                    legacy, path,
                )
                path = legacy
            else:
                raise ChargesConfigError(
                    f"Charge rate card {card!r} not found at {path}. A rate card "
                    f"carries every rate used to charge one strategy, with its "
                    f"source and as-of date."
                )
        with open(path, "r", encoding="utf-8") as handle:
            cls._rate_cards[card] = yaml.safe_load(handle) or {}
        logger.info(
            "Loaded charge rate card %s version %s",
            card, cls._rate_cards[card].get("version"),
        )
        return cls._rate_cards[card]

    @classmethod
    def reload(cls, card: Optional[str] = None) -> Dict[str, Any]:
        if card is None:
            cls._rate_cards = {}
            return cls.load_rates(force=True)
        return cls.load_rates(force=True, card=card)

    @property
    def rates(self) -> Dict[str, Any]:
        if self._override is not None:
            return self._override
        return self.load_rates(card=self._rate_card)

    @property
    def version(self) -> str:
        return str(self.rates.get("version", "unknown"))

    @property
    def rounding_mode(self) -> str:
        return str((self.rates.get("rounding") or {}).get("mode", "exact"))

    def label_for(self, name: str) -> Optional[str]:
        """The card's display label for a line item, if it names one."""
        labels = self.rates.get("labels") or {}
        label = labels.get(name)
        return str(label) if label else None

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

        # Unrounded component values, so a disputed total can be traced back to
        # the exact arithmetic that produced it rather than to the 2dp figures
        # the API returns.
        logger.debug(
            "Charges (%s, turnover=%s, rates=%s, rounding=%s) before rounding: "
            "brokerage=%s ctt=%s exchange=%s sebi=%s (raw %s) stamp=%s (raw %s) "
            "gst=%s total=%s",
            side, turnover, self.version, self.rounding_mode,
            brokerage.amount, ctt.amount, exchange_charge.amount,
            sebi_amount, sebi_fee.amount, stamp_amount, stamp_duty.amount,
            gst_amount, total,
        )

        # Each component carries its FINAL rounded amount and the raw figure it
        # came from. The total is still the rounded sum of the raw components,
        # not the sum of the rounded ones -- rounding each line first would
        # change totals that have been verified against a broker calculator.
        # The two can therefore differ by a paisa, which is a property of
        # rounding rather than an error, and is why the raw amount is kept.
        components = [
            component.rounded(self._round_money(amount)).with_label(
                self.label_for(component.name)
            )
            for component, amount in (
                (brokerage, brokerage.amount),
                (ctt, ctt.amount),
                (exchange_charge, exchange_charge.amount),
                (sebi_fee, sebi_amount),
                (stamp_duty, stamp_amount),
                (gst, gst_amount),
            )
        ]

        return ChargeBreakdown(
            turnover=self._round_money(turnover),
            total=self._round_money(total),
            rates_version=self.version,
            rounding_mode=self.rounding_mode,
            components=components,
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
            total=self._round_money(total),
            rates_version=self.version,
            rounding_mode=self.rounding_mode,
            # Exercise is a DIFFERENT taxable transaction from a trade -- Sl.5
            # rather than Sl.3 -- so its components keep their own names. A
            # breakdown that called this "ctt" would merge two taxes that a
            # contract note keeps apart.
            components=[
                component.rounded(self._round_money(component.amount)).with_label(
                    self.label_for(component.name)
                )
                for component in components
            ],
        )

    def rate_card(self) -> Dict[str, Any]:
        """The active rates, for display in the UI."""
        return self.rates
