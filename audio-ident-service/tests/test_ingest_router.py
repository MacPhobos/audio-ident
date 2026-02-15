"""Integration tests for POST /api/v1/ingest.

Tests the ingest endpoint's HTTP layer: auth, validation, MIME checking,
concurrency control, and pipeline result mapping.

All heavy dependencies (ingest_file, magic, settings) are mocked.
Uses a standalone FastAPI app with only the ingest router mounted.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.auth.admin import AdminAuthError
from app.routers import ingest as ingest_module
from app.routers.ingest import router as ingest_router
from app.schemas.ingest import IngestStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal valid WAV header (44 bytes) + 1 second of silence at 16kHz mono 16-bit
_WAV_SAMPLE_RATE = 16000
_WAV_CHANNELS = 1
_WAV_BITS_PER_SAMPLE = 16
_WAV_DURATION_SAMPLES = _WAV_SAMPLE_RATE  # 1 second
_WAV_DATA_SIZE = _WAV_DURATION_SAMPLES * _WAV_CHANNELS * (_WAV_BITS_PER_SAMPLE // 8)


def _make_wav_bytes(data_size: int = _WAV_DATA_SIZE) -> bytes:
    """Generate a minimal valid WAV file with silent audio data."""
    import struct

    file_size = 36 + data_size
    byte_rate = _WAV_SAMPLE_RATE * _WAV_CHANNELS * (_WAV_BITS_PER_SAMPLE // 8)
    block_align = _WAV_CHANNELS * (_WAV_BITS_PER_SAMPLE // 8)

    header = struct.pack(
        "<4sI4s"  # RIFF header
        "4sIHHIIHH"  # fmt chunk
        "4sI",  # data chunk header
        b"RIFF",
        file_size,
        b"WAVE",
        b"fmt ",
        16,  # fmt chunk size
        1,  # PCM format
        _WAV_CHANNELS,
        _WAV_SAMPLE_RATE,
        byte_rate,
        block_align,
        _WAV_BITS_PER_SAMPLE,
        b"data",
        data_size,
    )
    return header + (b"\x00" * data_size)


# A track UUID used consistently across success/duplicate tests
_TRACK_UUID = uuid.uuid4()

# Admin key used for testing
_TEST_ADMIN_KEY = "test-admin-key-12345"


def _make_success_result() -> MagicMock:
    """Create an IngestResult mock representing a successful ingestion."""
    result = MagicMock()
    result.status = "success"
    result.track_id = _TRACK_UUID
    result.title = "Test Song"
    result.artist = "Test Artist"
    result.error = None
    return result


def _make_duplicate_result() -> MagicMock:
    """Create an IngestResult mock representing a duplicate detection."""
    result = MagicMock()
    result.status = "duplicate"
    result.track_id = _TRACK_UUID
    result.title = "Test Song"
    result.artist = "Test Artist"
    result.error = None
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_ingest_lock():
    """Ensure the ingest lock is released between tests.

    Replace the module-level lock with a fresh one so tests don't leak state.
    """
    original_lock = ingest_module._ingest_lock
    ingest_module._ingest_lock = asyncio.Lock()
    yield
    ingest_module._ingest_lock = original_lock


@pytest.fixture
def ingest_app() -> FastAPI:
    """Minimal FastAPI app with only the ingest router mounted."""
    application = FastAPI()
    application.include_router(ingest_router, prefix="/api/v1")
    # Set up app.state attributes accessed by the ingest endpoint
    application.state.clap_model = MagicMock(name="mock_clap_model")
    application.state.clap_processor = MagicMock(name="mock_clap_processor")
    application.state.qdrant = MagicMock(name="mock_qdrant_client")

    # Register the AdminAuthError handler (mirrors main.py)
    @application.exception_handler(AdminAuthError)
    async def admin_auth_error_handler(request: Request, exc: AdminAuthError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    return application


@pytest.fixture
async def client(ingest_app: FastAPI) -> AsyncClient:
    """Async HTTP client for the ingest app."""
    transport = ASGITransport(app=ingest_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def wav_bytes() -> bytes:
    """A valid WAV file as bytes."""
    return _make_wav_bytes()


# ---------------------------------------------------------------------------
# Test 1: Successful ingestion -> 201
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_success(client: AsyncClient, wav_bytes: bytes):
    """Valid upload with correct admin key -> 201 with ingested status."""
    with (
        patch("app.auth.admin.settings", MagicMock(admin_api_key=_TEST_ADMIN_KEY)),
        patch("app.routers.ingest.magic") as mock_magic,
        patch(
            "app.routers.ingest.ingest_file",
            new_callable=AsyncMock,
            return_value=_make_success_result(),
        ),
    ):
        mock_magic.from_buffer.return_value = "audio/wav"

        resp = await client.post(
            "/api/v1/ingest",
            headers={"X-Admin-Key": _TEST_ADMIN_KEY},
            files={"audio": ("test.wav", wav_bytes, "audio/wav")},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == IngestStatus.INGESTED
    assert body["track_id"] == str(_TRACK_UUID)
    assert body["title"] == "Test Song"
    assert body["artist"] == "Test Artist"


# ---------------------------------------------------------------------------
# Test 2: Duplicate detection -> 201 with duplicate status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_duplicate(client: AsyncClient, wav_bytes: bytes):
    """SHA-256 match in pipeline -> 201 with duplicate status."""
    with (
        patch("app.auth.admin.settings", MagicMock(admin_api_key=_TEST_ADMIN_KEY)),
        patch("app.routers.ingest.magic") as mock_magic,
        patch(
            "app.routers.ingest.ingest_file",
            new_callable=AsyncMock,
            return_value=_make_duplicate_result(),
        ),
    ):
        mock_magic.from_buffer.return_value = "audio/wav"

        resp = await client.post(
            "/api/v1/ingest",
            headers={"X-Admin-Key": _TEST_ADMIN_KEY},
            files={"audio": ("test.wav", wav_bytes, "audio/wav")},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == IngestStatus.DUPLICATE
    assert body["track_id"] == str(_TRACK_UUID)


# ---------------------------------------------------------------------------
# Test 3: Missing admin key -> 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_missing_admin_key(client: AsyncClient, wav_bytes: bytes):
    """No X-Admin-Key header -> 403 Forbidden."""
    with patch("app.auth.admin.settings", MagicMock(admin_api_key=_TEST_ADMIN_KEY)):
        resp = await client.post(
            "/api/v1/ingest",
            files={"audio": ("test.wav", wav_bytes, "audio/wav")},
        )

    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# Test 4: Wrong admin key -> 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_wrong_admin_key(client: AsyncClient, wav_bytes: bytes):
    """Incorrect X-Admin-Key value -> 403 Forbidden."""
    with patch("app.auth.admin.settings", MagicMock(admin_api_key=_TEST_ADMIN_KEY)):
        resp = await client.post(
            "/api/v1/ingest",
            headers={"X-Admin-Key": "wrong-key"},
            files={"audio": ("test.wav", wav_bytes, "audio/wav")},
        )

    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# Test 5: No admin key configured (empty string) -> 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_no_admin_key_configured(client: AsyncClient, wav_bytes: bytes):
    """Empty ADMIN_API_KEY in settings -> 403 for ALL requests (fail-closed)."""
    with patch("app.auth.admin.settings", MagicMock(admin_api_key="")):
        resp = await client.post(
            "/api/v1/ingest",
            headers={"X-Admin-Key": "any-key"},
            files={"audio": ("test.wav", wav_bytes, "audio/wav")},
        )

    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "AUTH_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# Test 6: Unsupported format -> 400 UNSUPPORTED_FORMAT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_unsupported_format(client: AsyncClient):
    """Non-audio file (e.g. text/plain) -> 400 UNSUPPORTED_FORMAT."""
    with (
        patch("app.auth.admin.settings", MagicMock(admin_api_key=_TEST_ADMIN_KEY)),
        patch("app.routers.ingest.magic") as mock_magic,
    ):
        mock_magic.from_buffer.return_value = "text/plain"

        resp = await client.post(
            "/api/v1/ingest",
            headers={"X-Admin-Key": _TEST_ADMIN_KEY},
            files={"audio": ("readme.txt", b"Hello world", "text/plain")},
        )

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "UNSUPPORTED_FORMAT"


# ---------------------------------------------------------------------------
# Test 7: Empty file -> 400 EMPTY_FILE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_empty_file(client: AsyncClient):
    """Zero-byte upload -> 400 EMPTY_FILE."""
    with patch("app.auth.admin.settings", MagicMock(admin_api_key=_TEST_ADMIN_KEY)):
        resp = await client.post(
            "/api/v1/ingest",
            headers={"X-Admin-Key": _TEST_ADMIN_KEY},
            files={"audio": ("empty.wav", b"", "audio/wav")},
        )

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "EMPTY_FILE"


# ---------------------------------------------------------------------------
# Test 8: File too large -> 400 FILE_TOO_LARGE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_file_too_large(client: AsyncClient):
    """File exceeding 50 MB limit -> 400 FILE_TOO_LARGE."""
    # Create content just over the 50 MB limit
    oversized = b"\x00" * (50 * 1024 * 1024 + 1)

    with patch("app.auth.admin.settings", MagicMock(admin_api_key=_TEST_ADMIN_KEY)):
        resp = await client.post(
            "/api/v1/ingest",
            headers={"X-Admin-Key": _TEST_ADMIN_KEY},
            files={"audio": ("huge.wav", oversized, "audio/wav")},
        )

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "FILE_TOO_LARGE"


# ---------------------------------------------------------------------------
# Test 9: Audio too short -> 400 AUDIO_TOO_SHORT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_audio_too_short(client: AsyncClient, wav_bytes: bytes):
    """Pipeline returns 'skipped' with 'too short' -> 400 AUDIO_TOO_SHORT."""
    short_result = MagicMock()
    short_result.status = "skipped"
    short_result.error = "Too short: 1.5s (min: 3.0s)"
    short_result.track_id = None

    with (
        patch("app.auth.admin.settings", MagicMock(admin_api_key=_TEST_ADMIN_KEY)),
        patch("app.routers.ingest.magic") as mock_magic,
        patch(
            "app.routers.ingest.ingest_file",
            new_callable=AsyncMock,
            return_value=short_result,
        ),
    ):
        mock_magic.from_buffer.return_value = "audio/wav"

        resp = await client.post(
            "/api/v1/ingest",
            headers={"X-Admin-Key": _TEST_ADMIN_KEY},
            files={"audio": ("short.wav", wav_bytes, "audio/wav")},
        )

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "AUDIO_TOO_SHORT"


# ---------------------------------------------------------------------------
# Test 10: Audio too long -> 400 AUDIO_TOO_LONG
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_audio_too_long(client: AsyncClient, wav_bytes: bytes):
    """Pipeline returns 'skipped' with 'too long' -> 400 AUDIO_TOO_LONG."""
    long_result = MagicMock()
    long_result.status = "skipped"
    long_result.error = "Too long: 2000.0s (max: 1800.0s)"
    long_result.track_id = None

    with (
        patch("app.auth.admin.settings", MagicMock(admin_api_key=_TEST_ADMIN_KEY)),
        patch("app.routers.ingest.magic") as mock_magic,
        patch(
            "app.routers.ingest.ingest_file",
            new_callable=AsyncMock,
            return_value=long_result,
        ),
    ):
        mock_magic.from_buffer.return_value = "audio/wav"

        resp = await client.post(
            "/api/v1/ingest",
            headers={"X-Admin-Key": _TEST_ADMIN_KEY},
            files={"audio": ("long.wav", wav_bytes, "audio/wav")},
        )

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "AUDIO_TOO_LONG"


# ---------------------------------------------------------------------------
# Test 11: Concurrent rejection -> 429
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_concurrent_rejection(ingest_app: FastAPI, wav_bytes: bytes):
    """Two simultaneous requests -> one succeeds (201), one rejected (429)."""
    # Use an event to hold the first request in the pipeline
    hold_event = asyncio.Event()

    async def slow_ingest(*args, **kwargs):
        """Simulate a slow ingestion that waits for the event."""
        await hold_event.wait()
        return _make_success_result()

    with (
        patch("app.auth.admin.settings", MagicMock(admin_api_key=_TEST_ADMIN_KEY)),
        patch("app.routers.ingest.magic") as mock_magic,
        patch(
            "app.routers.ingest.ingest_file",
            new_callable=AsyncMock,
            side_effect=slow_ingest,
        ),
    ):
        mock_magic.from_buffer.return_value = "audio/wav"

        transport = ASGITransport(app=ingest_app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Start first request (will be held by slow_ingest)
            task1 = asyncio.create_task(
                ac.post(
                    "/api/v1/ingest",
                    headers={"X-Admin-Key": _TEST_ADMIN_KEY},
                    files={"audio": ("test1.wav", wav_bytes, "audio/wav")},
                )
            )

            # Give the first request time to acquire the lock
            await asyncio.sleep(0.1)

            # Second request should be rejected while first holds lock
            resp2 = await ac.post(
                "/api/v1/ingest",
                headers={"X-Admin-Key": _TEST_ADMIN_KEY},
                files={"audio": ("test2.wav", wav_bytes, "audio/wav")},
            )

            # Release the first request
            hold_event.set()
            resp1 = await task1

    # One should be 201, the other 429
    assert resp1.status_code == 201
    assert resp2.status_code == 429
    assert resp2.json()["error"]["code"] == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# Test 12: Missing audio field -> 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_missing_audio_field(client: AsyncClient):
    """Request without the 'audio' file field -> 422 Unprocessable Entity."""
    with patch("app.auth.admin.settings", MagicMock(admin_api_key=_TEST_ADMIN_KEY)):
        resp = await client.post(
            "/api/v1/ingest",
            headers={"X-Admin-Key": _TEST_ADMIN_KEY},
            # No files= parameter -> missing 'audio' field
        )

    assert resp.status_code == 422
