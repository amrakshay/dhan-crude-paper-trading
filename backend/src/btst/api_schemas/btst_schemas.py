"""Request models for the BTST endpoints.

Responses are plain dicts assembled by the services, the same way the
rotation's are: every payload is a read model built for one tab, and a Pydantic
model per tab would be a second description of the same shape that could drift
from it. The camelCase is produced at the source.
"""
from typing import Optional

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    """Trigger a scan or an exit by hand.

    `placeOrders` is what makes "look at what it would do" a first-class
    action rather than a debugging trick: the scan computes and journals
    identically and places nothing, which is exactly what an unarmed scheduled
    run does. An operator inspecting a scan must not get a different record
    from the one the machine would have written.
    """

    portfolio_id: Optional[int] = Field(None, alias="portfolioId")
    strategy_key: Optional[str] = Field(None, alias="strategyKey")
    # A scan already recorded for today is refused. `force` appends a new
    # record rather than editing the old one -- the journal is append-only.
    force: bool = Field(False, alias="force")
    place_orders: bool = Field(True, alias="placeOrders")

    model_config = {"populate_by_name": True}
