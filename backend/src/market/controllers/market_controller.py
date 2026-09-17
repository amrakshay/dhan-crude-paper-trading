"""Market data orchestration: turns service results and domain exceptions into
HTTP responses. Services never import FastAPI (backend/CLAUDE.md section 1)."""
from fastapi import HTTPException

from src.logging_config import get_logger
from src.market.api_schemas.market_schemas import (
    CandleResponse,
    TimeframeOption,
    TimeframesResponse,
)
from src.market.services.candle_service import (
    CandleError,
    FeatureDisabled,
    CandlesUnavailable,
    DEFAULT_TIMEFRAME,
    UnknownTimeframe,
    get_candle_service,
)

logger = get_logger("market.controller")


class MarketController:
    def __init__(self) -> None:
        self.candles = get_candle_service()

    def get_timeframes(self) -> TimeframesResponse:
        return TimeframesResponse(
            default=DEFAULT_TIMEFRAME,
            timeframes=[TimeframeOption(**option) for option in self.candles.timeframes()],
        )

    async def get_candles(self, security_id: str, timeframe: str) -> CandleResponse:
        try:
            payload = await self.candles.get_candles(security_id, timeframe)
        except UnknownTimeframe as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except FeatureDisabled as error:
            # 409, not 404 or 502: the request is well formed and the upstream
            # is fine -- this is simply switched off.
            raise HTTPException(status_code=409, detail=str(error)) from error
        except CandlesUnavailable as error:
            # 503: the request is fine, the data source is not configured.
            raise HTTPException(status_code=503, detail=str(error)) from error
        except CandleError as error:
            logger.warning("Candle request failed for %s %s: %s", security_id, timeframe, error)
            raise HTTPException(status_code=502, detail=str(error)) from error
        return CandleResponse(**payload)


_market_controller = None


def get_market_controller() -> MarketController:
    global _market_controller
    if _market_controller is None:
        _market_controller = MarketController()
    return _market_controller
