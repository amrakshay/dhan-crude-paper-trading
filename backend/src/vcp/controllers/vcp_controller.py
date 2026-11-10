"""Orchestration for the VCP endpoints: service results and errors into HTTP."""
from typing import Any, Dict, Optional

from fastapi import HTTPException

from src.vcp.services.vcp_service import VcpService, VcpServiceError


class VcpController:
    def __init__(self, session):
        self.session = session

    async def overview(self, portfolio_id: Optional[int]) -> Dict[str, Any]:
        try:
            return await VcpService(self.session).overview(portfolio_id)
        except VcpServiceError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
