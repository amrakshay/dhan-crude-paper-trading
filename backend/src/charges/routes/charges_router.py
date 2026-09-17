"""Charges endpoints.

Order entry shows estimated charges and the net debit/credit BEFORE the order
is confirmed, which is what these serve.
"""
from fastapi import APIRouter, Depends

from src.auth.dependencies import SessionPrincipal, require_session
from src.charges.api_schemas.charges_schemas import (
    ChargeBreakdownResponse,
    ChargeEstimateRequest,
    RateCardResponse,
    RoundTripEstimateRequest,
)
from src.charges.controllers.charges_controller import ChargesController
from src.core.singleton_utils import SingletonDepends

charges_router = APIRouter(prefix="/charges", tags=["Charges"])


def get_charges_controller() -> ChargesController:
    return SingletonDepends(ChargesController, called_inside_fastapi_depends=True)()


@charges_router.post("/estimate", response_model=ChargeBreakdownResponse)
async def estimate_charges(
    request: ChargeEstimateRequest,
    controller: ChargesController = Depends(get_charges_controller),
    _: SessionPrincipal = Depends(require_session),
) -> ChargeBreakdownResponse:
    """Full charges breakdown for one prospective order."""
    return controller.estimate(request)


@charges_router.post("/estimate/round-trip", response_model=ChargeBreakdownResponse)
async def estimate_round_trip(
    request: RoundTripEstimateRequest,
    controller: ChargesController = Depends(get_charges_controller),
    _: SessionPrincipal = Depends(require_session),
) -> ChargeBreakdownResponse:
    """Charges for a complete round trip, as the sum of both legs."""
    return controller.estimate_round_trip(request)


@charges_router.get("/rates", response_model=RateCardResponse)
async def get_rate_card(
    controller: ChargesController = Depends(get_charges_controller),
    _: SessionPrincipal = Depends(require_session),
) -> RateCardResponse:
    """The active rate card, including each rate's source and as-of date."""
    return controller.rate_card()


@charges_router.post("/rates/reload", response_model=RateCardResponse)
async def reload_rate_card(
    controller: ChargesController = Depends(get_charges_controller),
    _: SessionPrincipal = Depends(require_session),
) -> RateCardResponse:
    """Re-read the charge rate cards without restarting, after editing rates."""
    return controller.reload_rates()
