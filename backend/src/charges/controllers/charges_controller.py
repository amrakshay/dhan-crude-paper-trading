"""Charges orchestration."""
from fastapi import HTTPException

from src.charges.api_schemas.charges_schemas import (
    ChargeBreakdownResponse,
    ChargeEstimateRequest,
    RateCardResponse,
    RoundTripEstimateRequest,
)
from src.charges.services.charges_engine import ChargesConfigError, ChargesEngine
from src.logging_config import get_logger

logger = get_logger("charges.controller")


class ChargesController:
    def __init__(self) -> None:
        self.engine = ChargesEngine()

    @staticmethod
    def _to_response(breakdown) -> ChargeBreakdownResponse:
        return ChargeBreakdownResponse(**breakdown.as_dict())

    def estimate(self, request: ChargeEstimateRequest) -> ChargeBreakdownResponse:
        try:
            breakdown = self.engine.compute_order_charges(
                side=request.side,
                premium=request.premium,
                lot_size=request.lot_size,
                lots=request.lots,
                strike_price=request.strike_price,
            )
        except ValueError as exc:
            logger.warning("Rejected a charges estimate request: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ChargesConfigError as exc:
            logger.exception(
                "The charge rate card is unusable -- check conf/charges.yaml"
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return self._to_response(breakdown)

    def estimate_round_trip(
        self, request: RoundTripEstimateRequest
    ) -> ChargeBreakdownResponse:
        try:
            breakdown = self.engine.compute_round_trip_charges(
                buy_premium=request.buy_premium,
                sell_premium=request.sell_premium,
                lot_size=request.lot_size,
                lots=request.lots,
            )
        except ValueError as exc:
            logger.warning("Rejected a charges request: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return self._to_response(breakdown)

    def rate_card(self) -> RateCardResponse:
        rates = self.engine.rate_card()
        return RateCardResponse(
            version=str(rates.get("version", "unknown")),
            currency=str(rates.get("currency", "INR")),
            rates=rates,
        )

    def reload_rates(self) -> RateCardResponse:
        ChargesEngine.reload()
        return self.rate_card()
