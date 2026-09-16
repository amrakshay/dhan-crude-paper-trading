"""Standard paginated response envelope."""
from typing import Any, List

from pydantic import BaseModel, Field


class Pageable(BaseModel):
    content: List[Any] = Field(description="The list of content")
    totalPages: int = Field(description="Total number of pages")
    totalElements: int = Field(description="Total number of elements")
    last: bool = Field(description="Indicates if this is the last page")
    size: int = Field(description="Size of the page")
    number: int = Field(description="Current page number")
    sort: List[str] = Field(description="Sorting criteria", default=[])
    numberOfElements: int = Field(description="Number of elements in the current page")
    first: bool = Field(description="Indicates if this is the first page")
    empty: bool = Field(description="Indicates if the current page is empty")


def create_pageable_response(
    content: list,
    total_elements: int,
    page_number: int,
    size: int,
    sort: List[str],
) -> Pageable:
    return Pageable(
        content=content,
        totalPages=((total_elements + size - 1) // size) if size else 0,
        totalElements=total_elements,
        last=(page_number + 1) * size >= total_elements,
        size=size,
        number=page_number,
        sort=sort,
        numberOfElements=len(content),
        first=page_number == 0,
        empty=len(content) == 0,
    )
