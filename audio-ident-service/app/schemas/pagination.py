from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


def _to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


class PaginationMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    page: int
    page_size: int = Field(ge=1, le=100)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class PaginatedResponse(BaseModel, Generic[T]):
    data: list[T]
    pagination: PaginationMeta


class TrackListParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100, alias="pageSize")
    search: str | None = None
