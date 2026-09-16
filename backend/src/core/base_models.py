"""Shared Pydantic bases. API payloads use camelCase aliases."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class BaseView(BaseModel):
    id: Optional[int] = Field(None, description="The unique identifier of the resource")
    created_at: Optional[datetime] = Field(
        None, description="Creation time", alias="createTime"
    )
    updated_at: Optional[datetime] = Field(
        None, description="Last update time", alias="updateTime"
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("created_at", "updated_at")
    def serialize_timestamp(self, value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value else None


class BaseAPIFilter(BaseModel):
    id: Optional[int] = Field(None, description="The unique identifier of the resource")
    created_at_from: Optional[datetime] = Field(
        None, description="Filter creation time from", alias="createTimeFrom"
    )
    created_at_to: Optional[datetime] = Field(
        None, description="Filter creation time to", alias="createTimeTo"
    )
    updated_at_from: Optional[datetime] = Field(
        None, description="Filter update time from", alias="updateTimeFrom"
    )
    updated_at_to: Optional[datetime] = Field(
        None, description="Filter update time to", alias="updateTimeTo"
    )
    exact_match: Optional[bool] = Field(
        False, description="Enable exact string matching", alias="exactMatch"
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
