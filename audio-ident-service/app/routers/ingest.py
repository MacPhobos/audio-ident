"""Ingest endpoint for adding audio files to the identification library.

Accepts single-file multipart uploads, validates format and duration,
and processes through the full ingestion pipeline (metadata extraction,
fingerprinting, embedding generation).

Protected by admin API key (X-Admin-Key header).
Enforces single-writer constraint (rejects concurrent requests with 429).
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

import magic
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse

from app.auth.admin import require_admin_key
from app.db.session import async_session_factory
from app.ingest.pipeline import ingest_file
from app.schemas.ingest import IngestResponse, IngestStatus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

ALLOWED_MIME_TYPES: set[str] = {
    "audio/webm",
    "video/webm",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/x-wav",
    "audio/flac",
    "audio/x-flac",
}

# WARNING: This lock is per-process. Multi-worker deployments (e.g., --workers > 1)
# will have separate locks per worker, defeating the single-writer constraint.
# The ingest endpoint REQUIRES single-worker mode for correctness.
_ingest_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Build a JSON error response matching the project convention."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
            }
        },
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=201,
    responses={
        400: {"description": "Validation error (format, size, duration)"},
        403: {"description": "Missing or invalid admin API key"},
        429: {"description": "Another ingestion is in progress"},
        503: {"description": "Backend service unavailable"},
    },
    dependencies=[Depends(require_admin_key)],
)
async def ingest_audio(
    request: Request,
    audio: UploadFile = File(  # noqa: B008
        ...,
        description="Audio file to ingest (MP3, WAV, FLAC, OGG, WebM, MP4). Max 50 MB.",
    ),
) -> IngestResponse | JSONResponse:
    """Ingest a single audio file into the identification library.

    The file is processed through the full pipeline:
    1. SHA-256 hash check (duplicate detection)
    2. Metadata extraction (title, artist, album)
    3. Dual-rate PCM decode (16kHz + 48kHz)
    4. Duration validation (3s - 30min)
    5. Chromaprint content dedup
    6. Olaf fingerprint indexing + CLAP embedding generation (parallel)
    7. PostgreSQL track record insertion

    Only one ingestion can run at a time (Olaf LMDB single-writer constraint).
    If another ingestion is in progress, returns 429.

    Requires X-Admin-Key header matching the ADMIN_API_KEY environment variable.
    """
    # 1. Read and validate upload FIRST (before lock interaction).
    #    This ensures no await points exist between the lock check and
    #    lock acquisition, preventing the TOCTOU race condition.
    content = await audio.read()

    if len(content) == 0:
        return _error_response(400, "EMPTY_FILE", "Empty file uploaded.")

    if len(content) > MAX_UPLOAD_BYTES:
        return _error_response(
            400,
            "FILE_TOO_LARGE",
            f"File too large. Maximum upload size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    # 2. Verify content type via magic bytes
    try:
        detected_type = magic.from_buffer(content, mime=True)
    except Exception:
        logger.exception("Failed to detect MIME type for uploaded file")
        return _error_response(400, "UNSUPPORTED_FORMAT", "Unable to detect file format.")

    if detected_type not in ALLOWED_MIME_TYPES:
        return _error_response(
            400,
            "UNSUPPORTED_FORMAT",
            f"Unsupported audio format: {detected_type}. "
            "Supported: MP3, WAV, FLAC, OGG, WebM, MP4.",
        )

    # 3. Write to temp file (synchronous, no await)
    suffix = Path(audio.filename or "upload").suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(content)

    # 4. Check lock and acquire atomically -- no await points between
    #    the locked() check and the async with, so no TOCTOU race.
    if _ingest_lock.locked():
        tmp_path.unlink(missing_ok=True)
        return _error_response(
            429,
            "RATE_LIMITED",
            "Another ingestion is in progress. Please try again in a moment.",
        )

    try:
        # 5. Acquire lock and run ingestion
        async with _ingest_lock:
            clap_model = getattr(request.app.state, "clap_model", None)
            clap_processor = getattr(request.app.state, "clap_processor", None)
            qdrant_client = request.app.state.qdrant

            result = await ingest_file(
                file_path=tmp_path,
                clap_model=clap_model,
                clap_processor=clap_processor,
                qdrant_client=qdrant_client,
                session_factory=async_session_factory,
            )

    finally:
        # Clean up temp file
        tmp_path.unlink(missing_ok=True)

    # 6. Map pipeline result to HTTP response
    if result.status == "error":
        error_msg = result.error or "Unknown error"
        if "too short" in error_msg.lower():
            return _error_response(400, "AUDIO_TOO_SHORT", error_msg)
        elif "too long" in error_msg.lower():
            return _error_response(400, "AUDIO_TOO_LONG", error_msg)
        elif "decode" in error_msg.lower():
            return _error_response(400, "UNSUPPORTED_FORMAT", error_msg)
        else:
            return _error_response(503, "SERVICE_UNAVAILABLE", error_msg)

    if result.status == "skipped":
        error_msg = result.error or "File skipped"
        if "too short" in error_msg.lower():
            return _error_response(400, "AUDIO_TOO_SHORT", error_msg)
        elif "too long" in error_msg.lower():
            return _error_response(400, "AUDIO_TOO_LONG", error_msg)
        else:
            return _error_response(400, "VALIDATION_ERROR", error_msg)

    # Success or duplicate -- track_id must always be set by the pipeline.
    if result.track_id is None:
        return _error_response(
            503,
            "SERVICE_UNAVAILABLE",
            "Ingestion completed but no track ID was returned.",
        )

    status = IngestStatus.DUPLICATE if result.status == "duplicate" else IngestStatus.INGESTED

    return IngestResponse(
        track_id=result.track_id,
        title=result.title or audio.filename or "Unknown",
        artist=result.artist,
        status=status,
    )
