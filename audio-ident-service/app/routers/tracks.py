"""Track library endpoints — paginated listing, detail views, and audio streaming."""

from __future__ import annotations

import logging
import math
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio.storage import raw_audio_path
from app.db.session import get_db
from app.models.track import Track
from app.schemas.errors import ErrorDetail, ErrorResponse
from app.schemas.pagination import PaginatedResponse, PaginationMeta
from app.schemas.search import TrackInfo
from app.schemas.track import TrackDetail
from app.settings import settings

logger = logging.getLogger(__name__)

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
    page: int = Query(default=1),
    pageSize: int = Query(default=50, alias="pageSize"),  # noqa: N803
    search: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PaginatedResponse[TrackInfo]:
    """Return a paginated list of tracks, optionally filtered by title/artist search."""
    # Clamp pagination parameters per API contract
    page = max(1, page)
    page_size = max(1, min(100, pageSize))

    # Build base query
    base_query = select(Track)

    if search:
        escaped = search.replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
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
    total_pages = math.ceil(total_items / page_size) if total_items > 0 else 0
    offset = (page - 1) * page_size

    # Fetch page of tracks
    data_query = base_query.order_by(Track.ingested_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(data_query)
    tracks = result.scalars().all()

    return PaginatedResponse[TrackInfo](
        data=[_track_to_info(t) for t in tracks],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
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
                code="NOT_FOUND",
                message=f"No track found with id {track_id}",
            )
        )
        return JSONResponse(
            status_code=404,
            content=error_body.model_dump(),
        )

    return _track_to_detail(track)


# ---------------------------------------------------------------------------
# Audio streaming
# ---------------------------------------------------------------------------

AUDIO_MIME_TYPES: dict[str, str] = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "mp4": "audio/mp4",
    "m4a": "audio/mp4",
}


def _resolve_format(track: Track) -> str | None:
    """Return the audio format for a track, falling back to file extension."""
    if track.format:
        return track.format.lower().lstrip(".")

    # Fall back: try to extract extension from the stored file_path
    stored_ext = Path(track.file_path).suffix.lstrip(".")
    if stored_ext:
        return stored_ext.lower()

    return None


@router.get(
    "/tracks/{track_id}/audio",
    response_model=None,
    responses={
        200: {"content": {"audio/mpeg": {}}, "description": "Full audio file"},
        206: {"description": "Partial content (Range request)"},
        404: {"description": "Track not found or file missing", "model": ErrorResponse},
    },
)
async def get_track_audio(
    track_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> FileResponse | JSONResponse:
    """Stream the audio file for a track.

    Supports HTTP Range requests for seeking (206 Partial Content).
    Starlette's FileResponse handles Range parsing, Content-Range,
    Accept-Ranges, ETag, and Last-Modified headers automatically.
    """
    # 1. Look up track in DB
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()

    if track is None:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="NOT_FOUND",
                    message=f"No track found with id {track_id}",
                )
            ).model_dump(),
        )

    # 2. Resolve audio format
    fmt = _resolve_format(track)
    if fmt is None:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="FILE_NOT_FOUND",
                    message="Track has no format information; cannot locate audio file",
                )
            ).model_dump(),
        )

    # 3. Reconstruct file path from hash + format (not the stored file_path)
    file_path = raw_audio_path(track.file_hash_sha256, fmt)

    # 4. Defense-in-depth: validate path is within storage root
    storage_root = Path(settings.audio_storage_root).resolve()
    resolved_path = file_path.resolve()
    if not str(resolved_path).startswith(str(storage_root)):
        logger.warning(
            "Path traversal blocked: resolved=%s, storage_root=%s, track_id=%s",
            resolved_path,
            storage_root,
            track_id,
        )
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="FILE_NOT_FOUND",
                    message="Audio file not found on disk",
                )
            ).model_dump(),
        )

    # 5. Check file exists on disk
    if not resolved_path.is_file():
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="FILE_NOT_FOUND",
                    message="Audio file not found on disk",
                )
            ).model_dump(),
        )

    # 6. Determine MIME type from format
    media_type = AUDIO_MIME_TYPES.get(fmt, "application/octet-stream")

    # 7. Return FileResponse — Starlette handles Range, ETag, Content-Type
    return FileResponse(
        path=resolved_path,
        media_type=media_type,
        content_disposition_type="inline",
    )
