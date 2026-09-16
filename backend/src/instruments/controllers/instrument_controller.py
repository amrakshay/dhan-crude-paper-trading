"""Instrument orchestration."""
from datetime import date
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src import config_utils
from src.core.time_utils import as_utc_aware
from src.instruments.api_schemas.instrument_schemas import (
    ChainResponse,
    ChainRowResponse,
    ExpiryListResponse,
    InstrumentResponse,
    InstrumentStatusResponse,
    RefreshResponse,
)
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.instruments.services.chain_service import ChainService
from src.instruments.services.instrument_master_service import (
    InstrumentMasterError,
    InstrumentMasterService,
)
from src.logging_config import get_logger

logger = get_logger("instruments.controller")


class InstrumentController:
    def __init__(self, session: AsyncSession):
        self.repository = InstrumentRepository(session)
        self.master_service = InstrumentMasterService(self.repository)
        self.chain_service = ChainService(self.repository)

    async def refresh(self, force: bool = False) -> RefreshResponse:
        try:
            result = await self.master_service.refresh(force=force)
        except InstrumentMasterError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Instrument master refresh failed")
            raise HTTPException(
                status_code=502, detail=f"Instrument master refresh failed: {exc}"
            ) from exc

        # Commit before resyncing. The feed manager opens its own session, so
        # it cannot see rows this request has not committed yet -- without this
        # the resync reads an empty universe and subscribes to nothing.
        await self.repository.session.commit()

        # Security IDs change every expiry, so the live subscription is stale
        # the moment the master changes. Re-centre it immediately rather than
        # waiting for the next drift check.
        try:
            from src.market.services.feed_manager import get_feed_manager

            await get_feed_manager().resync()
        except Exception:
            logger.exception("Feed resync after instrument refresh failed")

        payload = result.as_dict()
        return RefreshResponse(
            downloaded=payload["downloaded"],
            rowsScanned=payload["rowsScanned"],
            rowsMatched=payload["rowsMatched"],
            inserted=payload["inserted"],
            updated=payload["updated"],
            unchanged=payload["unchanged"],
            deactivated=payload["deactivated"],
            expiries=payload["expiries"],
            durationSeconds=payload["durationSeconds"],
            warnings=payload["warnings"],
        )

    async def list_expiries(self) -> ExpiryListResponse:
        option_expiries = await self.chain_service.list_option_expiries()
        futures_expiries = await self.chain_service.list_futures_expiries()

        mapping = {}
        for expiry in option_expiries:
            future = await self.chain_service.resolve_underlying_future(expiry)
            mapping[expiry.isoformat()] = future.security_id if future else None

        return ExpiryListResponse(
            optionExpiries=option_expiries,
            futuresExpiries=futures_expiries,
            underlyingFutureByExpiry=mapping,
        )

    async def get_chain(self, expiry: Optional[date] = None) -> ChainResponse:
        if expiry is None:
            expiries = await self.chain_service.list_option_expiries()
            if not expiries:
                raise HTTPException(
                    status_code=404,
                    detail="No option expiries available. Refresh the instrument master.",
                )
            expiry = expiries[0]

        rows = await self.chain_service.get_chain(expiry)
        if not rows:
            raise HTTPException(
                status_code=404, detail=f"No contracts found for expiry {expiry}"
            )

        future = await self.chain_service.resolve_underlying_future(expiry)
        strike_step = self.chain_service.infer_strike_step([row.strike_price for row in rows])

        return ChainResponse(
            expiry=expiry,
            underlyingSymbol=config_utils.get_property_value("underlying.symbol", "CRUDEOIL"),
            underlyingFuture=InstrumentResponse.model_validate(future) if future else None,
            strikeStep=strike_step,
            rows=[
                ChainRowResponse(
                    strikePrice=row.strike_price,
                    call=InstrumentResponse.model_validate(row.call) if row.call else None,
                    put=InstrumentResponse.model_validate(row.put) if row.put else None,
                )
                for row in rows
            ],
        )

    async def get_near_future(self) -> InstrumentResponse:
        future = await self.chain_service.get_near_future()
        if future is None:
            raise HTTPException(
                status_code=404,
                detail="No futures contract available. Refresh the instrument master.",
            )
        return InstrumentResponse.model_validate(future)

    async def get_status(self) -> InstrumentStatusResponse:
        count = await self.repository.count_all()
        last_refreshed = await self.repository.last_refreshed_at()
        near_future = await self.chain_service.get_near_future()
        return InstrumentStatusResponse(
            instrumentCount=count,
            lastRefreshedAt=(
                as_utc_aware(last_refreshed).isoformat() if last_refreshed else None
            ),
            cacheFresh=self.master_service.is_cache_fresh(),
            optionExpiries=await self.chain_service.list_option_expiries(),
            nearFuture=(
                InstrumentResponse.model_validate(near_future) if near_future else None
            ),
        )
