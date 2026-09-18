"""Swing Momentum request payloads.

The responses are plain dictionaries assembled by `swing_service`: their shape
is the journal's own columns plus a few derived fields, and a Pydantic model
per read would be a second place to keep in step with the table for no gain.
Requests ARE modelled, because a request that lets software spend money should
not accept a field it did not mean to.
"""
from pydantic import BaseModel, ConfigDict, Field


class RunRequest(BaseModel):
    """Trigger one run, in one portfolio."""

    portfolio_id: int = Field(alias="portfolioId")
    # Re-decide a session that is already recorded. The journal is append-only,
    # so a forced run APPENDS a new record rather than editing the old one --
    # both stay, and the pair is the record of the reconsideration.
    force: bool = False

    model_config = ConfigDict(populate_by_name=True)
