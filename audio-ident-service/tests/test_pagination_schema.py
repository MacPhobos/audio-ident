"""Unit tests for pagination schema camelCase serialization."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.pagination import PaginatedResponse, PaginationMeta, TrackListParams


class TestPaginationMeta:
    """Tests for PaginationMeta camelCase alias serialization."""

    def test_pagination_serializes_to_contract_shape(self):
        meta = PaginationMeta(page=1, page_size=50, total_items=142, total_pages=3)
        serialized = meta.model_dump(by_alias=True)
        assert serialized == {
            "page": 1,
            "pageSize": 50,
            "totalItems": 142,
            "totalPages": 3,
        }

    def test_pagination_accepts_snake_case_fields(self):
        meta = PaginationMeta(page=2, page_size=25, total_items=0, total_pages=0)
        assert meta.page == 2
        assert meta.page_size == 25
        assert meta.total_items == 0
        assert meta.total_pages == 0

    def test_pagination_accepts_camel_case_aliases(self):
        meta = PaginationMeta.model_validate(
            {"page": 1, "pageSize": 10, "totalItems": 50, "totalPages": 5}
        )
        assert meta.page_size == 10
        assert meta.total_items == 50
        assert meta.total_pages == 5

    def test_pagination_page_size_max_validation(self):
        with pytest.raises(ValidationError):
            PaginationMeta(page=1, page_size=101, total_items=0, total_pages=0)

    def test_pagination_page_size_min_validation(self):
        with pytest.raises(ValidationError):
            PaginationMeta(page=1, page_size=0, total_items=0, total_pages=0)

    def test_pagination_total_items_non_negative(self):
        with pytest.raises(ValidationError):
            PaginationMeta(page=1, page_size=50, total_items=-1, total_pages=0)


class TestPaginatedResponse:
    """Tests for PaginatedResponse generic container."""

    def test_paginated_response_structure(self):
        meta = PaginationMeta(page=1, page_size=10, total_items=2, total_pages=1)
        response = PaginatedResponse[str](data=["a", "b"], pagination=meta)
        dumped = response.model_dump(by_alias=True)

        assert "data" in dumped
        assert "pagination" in dumped
        assert dumped["data"] == ["a", "b"]
        assert dumped["pagination"]["pageSize"] == 10


class TestTrackListParams:
    """Tests for TrackListParams query-param parsing."""

    def test_defaults(self):
        params = TrackListParams()
        assert params.page == 1
        assert params.page_size == 50
        assert params.search is None

    def test_accepts_camel_case_alias(self):
        params = TrackListParams.model_validate({"pageSize": 25})
        assert params.page_size == 25

    def test_page_size_max_validation(self):
        with pytest.raises(ValidationError):
            TrackListParams.model_validate({"pageSize": 200})

    def test_page_min_validation(self):
        with pytest.raises(ValidationError):
            TrackListParams(page=0)
