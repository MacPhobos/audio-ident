"""Track library endpoints — paginated listing and detail views."""

from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.track import Track
from app.schemas.errors import ErrorDetail, ErrorResponse
from app.schemas.pagination import PaginatedResponse, PaginationMeta
from app.schemas.search import TrackInfo
from app.schemas.track import TrackDetail

router = APIRouter(tags=["tracks"])


def _track_to_info(track: Track) -> TrackInfo:
    """Map a Track ORM model to a TrackInfo schema."""
    return TrackInfo(
        id=track.id,
        title=track.title,
        artist=track.artist,
        album=track.album,
        duration_seconds=track.duration_seconds,
        ingested_at=track.ingested_at,
    )


def _track_to_detail(track: Track) -> TrackDetail:
    """Map a Track ORM model to a TrackDetail schema."""
    return TrackDetail(
        id=track.id,
        title=track.title,
        artist=track.artist,
        album=track.album,
        duration_seconds=track.duration_seconds,
        ingested_at=track.ingested_at,
        sample_rate=track.sample_rate,
        channels=track.channels,
        bitrate=track.bitrate,
        format=track.format,
        file_hash_sha256=track.file_hash_sha256,
        file_size_bytes=track.file_size_bytes,
        olaf_indexed=track.olaf_indexed,
        embedding_model=track.embedding_model,
        embedding_dim=track.embedding_dim,
        updated_at=track.updated_at,
    )


@router.get(
    "/tracks",
    response_model=PaginatedResponse[TrackInfo],
    responses={422: {"description": "Validation error"}},
)
async def list_tracks(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=100, alias="pageSize"),  # noqa: N803
    search: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PaginatedResponse[TrackInfo]:
    """Return a paginated list of tracks, optionally filtered by title/artist search."""
    # Build base query
    base_query = select(Track)

    if search:
        pattern = f"%{search}%"
        base_query = base_query.where(
            or_(
                Track.title.ilike(pattern),
                Track.artist.ilike(pattern),
            )
        )

    # Count total items
    count_query = select(func.count()).select_from(base_query.subquery())
    total_items_result = await db.execute(count_query)
    total_items: int = total_items_result.scalar_one()

    # Calculate pagination
    total_pages = math.ceil(total_items / pageSize) if total_items > 0 else 0
    offset = (page - 1) * pageSize

    # Fetch page of tracks
    data_query = base_query.order_by(Track.ingested_at.desc()).offset(offset).limit(pageSize)
    result = await db.execute(data_query)
    tracks = result.scalars().all()

    return PaginatedResponse[TrackInfo](
        data=[_track_to_info(t) for t in tracks],
        pagination=PaginationMeta(
            page=page,
            page_size=pageSize,
            total_items=total_items,
            total_pages=total_pages,
        ),
    )


@router.get(
    "/tracks/{track_id}",
    response_model=TrackDetail,
    responses={
        404: {"description": "Track not found", "model": ErrorResponse},
        422: {"description": "Validation error"},
    },
)
async def get_track(
    track_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> TrackDetail | JSONResponse:
    """Return full detail for a single track by UUID."""
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()

    if track is None:
        error_body = ErrorResponse(
            error=ErrorDetail(
                code="TRACK_NOT_FOUND",
                message=f"No track found with id {track_id}",
            )
        )
        return JSONResponse(
            status_code=404,
            content=error_body.model_dump(),
        )

    return _track_to_detail(track)
