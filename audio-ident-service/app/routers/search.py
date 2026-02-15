"""Search endpoint for audio identification.

Accepts multipart audio uploads, validates format and duration,
decodes to dual-rate PCM, and orchestrates parallel search lanes.
"""

from __future__ import annotations

import logging
import shutil

import magic
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from app.audio.decode import AudioDecodeError, decode_dual_rate, pcm_duration_seconds
from app.schemas.search import SearchMode, SearchResponse
from app.search.orchestrator import SearchTimeoutError, SearchUnavailableError, orchestrate_search

logger = logging.getLogger(__name__)

router = APIRouter(tags=["search"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
MIN_QUERY_DURATION = 3.0  # seconds

ALLOWED_MIME_TYPES: dict[str, str] = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "mp4",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------


def _detect_audio_format(content: bytes) -> str | None:
    """Detect audio container format from magic bytes.

    Returns the ffmpeg format hint string, or None if unsupported.
    """
    if not content:
        return None
    detected = magic.from_buffer(content, mime=True)
    return ALLOWED_MIME_TYPES.get(detected)


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


async def _validate_upload(audio: UploadFile) -> bytes | JSONResponse:
    """Read and validate an uploaded audio file.

    Checks:
    - Non-zero byte content
    - Size within MAX_UPLOAD_BYTES
    - MIME type detected via magic bytes is in ALLOWED_MIME_TYPES

    Returns:
        Raw audio bytes on success, or JSONResponse on validation failure.
    """
    content = await audio.read()

    if len(content) == 0:
        return _error_response(
            400,
            "FILE_TOO_LARGE",
            "Empty file uploaded. Please provide an audio file.",
        )

    if len(content) > MAX_UPLOAD_BYTES:
        return _error_response(
            400,
            "FILE_TOO_LARGE",
            "Max upload size is 10 MB.",
        )

    # Verify content type via magic bytes (not Content-Type header)
    detected_type = magic.from_buffer(content, mime=True)
    if detected_type not in ALLOWED_MIME_TYPES:
        return _error_response(
            400,
            "UNSUPPORTED_FORMAT",
            f"Unsupported audio format: {detected_type}",
        )

    return content


def check_ffmpeg_available() -> bool:
    """Check if ffmpeg is available on the system PATH."""
    return shutil.which("ffmpeg") is not None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/search",
    response_model=SearchResponse,
    responses={
        400: {"description": "Validation error (format, size, duration)"},
        503: {"description": "Search service unavailable (both lanes failed)"},
        504: {"description": "Search timed out (both lanes timed out)"},
    },
)
async def search_audio(
    request: Request,
    audio: UploadFile = File(  # noqa: B008
        ...,
        description="Audio file (WebM/Opus, MP3, MP4/AAC, WAV). Max 10 MB.",
    ),
    mode: SearchMode = Form(default=SearchMode.BOTH),  # noqa: B008
    max_results: int = Form(default=10, ge=1, le=50),
) -> SearchResponse | JSONResponse:
    """Search for audio matches using fingerprint (exact) and/or
    embedding (vibe) similarity.

    Accepts multipart/form-data with:
    - ``audio``: The audio file to search for
    - ``mode``: Search mode (``"exact"``, ``"vibe"``, or ``"both"``)
    - ``max_results``: Max results per lane (1-50, default 10)
    """
    # 1. Validate upload (size, content type, magic bytes)
    result = await _validate_upload(audio)
    if isinstance(result, JSONResponse):
        return result
    content: bytes = result

    # 2. Decode to dual-rate PCM
    try:
        pcm_16k, pcm_48k = await decode_dual_rate(content)
    except AudioDecodeError as exc:
        logger.warning("Audio decode failed: %s", exc)
        return _error_response(
            400,
            "UNSUPPORTED_FORMAT",
            "Unable to decode audio file. Please try a different format.",
        )

    # 3. Validate duration (minimum 3 seconds)
    duration = pcm_duration_seconds(pcm_16k, sample_rate=16000)
    if duration < MIN_QUERY_DURATION:
        return _error_response(
            400,
            "AUDIO_TOO_SHORT",
            f"Audio too short: {duration:.1f}s (minimum {MIN_QUERY_DURATION:.0f}s).",
        )

    # 4. Orchestrate search (parallel lanes with timeouts)
    try:
        response = await orchestrate_search(
            pcm_16k=pcm_16k,
            pcm_48k=pcm_48k,
            mode=mode,
            max_results=max_results,
            qdrant_client=request.app.state.qdrant,
            clap_model=getattr(request.app.state, "clap_model", None),
            clap_processor=getattr(request.app.state, "clap_processor", None),
        )
    except SearchUnavailableError:
        return _error_response(
            503,
            "SEARCH_UNAVAILABLE",
            "Search service temporarily unavailable. Please retry.",
        )
    except SearchTimeoutError:
        return _error_response(
            504,
            "SEARCH_TIMEOUT",
            "Search timed out. Please try with a shorter clip.",
        )

    return response
