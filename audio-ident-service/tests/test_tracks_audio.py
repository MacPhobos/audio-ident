"""Integration tests for GET /api/v1/tracks/{track_id}/audio.

Uses a standalone FastAPI app with SQLite (via aiosqlite) to avoid requiring
real PostgreSQL/Qdrant infrastructure during testing.  A minimal valid MP3
fixture file is created in a temporary directory for each test session.
"""

from __future__ import annotations

import struct
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models.track import Track

# ---------------------------------------------------------------------------
# In-memory SQLite engine for tests
# ---------------------------------------------------------------------------

_test_engine = create_async_engine("sqlite+aiosqlite://", echo=False)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)


@event.listens_for(_test_engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable foreign keys for SQLite (off by default)."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# ---------------------------------------------------------------------------
# Minimal MP3 frame generator
# ---------------------------------------------------------------------------


def _make_minimal_mp3() -> bytes:
    """Create a minimal valid MP3 file (a single MPEG Audio Layer 3 frame).

    The frame header is: 0xFFE3_9004 (sync word, MPEG1 Layer3, 128kbps, 44100Hz, mono).
    A valid MP3 frame at 128kbps / 44100Hz is 417 bytes (header + padding + body).
    We fill the body with zeros (silence).
    """
    # MPEG1, Layer 3, 128kbps, 44100Hz, mono, no padding
    header = struct.pack(">I", 0xFFE39004)
    # Frame size = 144 * bitrate / sample_rate + padding
    # 144 * 128000 / 44100 = 417 bytes (truncated), minus 4 byte header = 413 body
    body = b"\x00" * 413
    return header + body


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _setup_tables():
    """Create tables before each test and drop them after."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _override_get_db():
    """Dependency override that yields a test SQLite session."""
    async with _test_session_factory() as session:
        yield session


@pytest.fixture
def audio_storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Provide a temporary audio storage directory and patch settings to use it."""
    monkeypatch.setattr("app.settings.settings.audio_storage_root", str(tmp_path))
    # Also patch the settings import used in the storage module
    monkeypatch.setattr("app.audio.storage.settings.audio_storage_root", str(tmp_path))
    return tmp_path


@pytest.fixture
def tracks_app():
    """Minimal FastAPI app with only the tracks router and SQLite DB override."""
    from fastapi import FastAPI

    from app.db.session import get_db
    from app.routers import tracks

    application = FastAPI()
    application.include_router(tracks.router, prefix="/api/v1")
    application.dependency_overrides[get_db] = _override_get_db
    return application


@pytest.fixture
async def client(tracks_app) -> AsyncClient:
    transport = ASGITransport(app=tracks_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def seed_track_with_file(audio_storage_dir: Path) -> Track:
    """Insert a track into the test database and create the corresponding MP3 file on disk."""
    file_hash = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
    fmt = "mp3"

    # Create the file in the expected storage location
    prefix = file_hash[:2]
    storage_dir = audio_storage_dir / "raw" / prefix
    storage_dir.mkdir(parents=True, exist_ok=True)
    audio_file = storage_dir / f"{file_hash}.{fmt}"
    audio_file.write_bytes(_make_minimal_mp3())

    now = datetime.now(UTC)
    track = Track(
        id=uuid.uuid4(),
        title="Test Track",
        artist="Test Artist",
        album="Test Album",
        duration_seconds=10.0,
        file_hash_sha256=file_hash,
        file_size_bytes=len(_make_minimal_mp3()),
        file_path=str(audio_file),
        format=fmt,
        olaf_indexed=False,
        ingested_at=now,
        updated_at=now,
    )

    async with _test_session_factory() as session:
        session.add(track)
        await session.commit()

    return track


@pytest.fixture
async def seed_track_wav(audio_storage_dir: Path) -> Track:
    """Insert a WAV track to test Content-Type mapping."""
    file_hash = "b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3"
    fmt = "wav"

    prefix = file_hash[:2]
    storage_dir = audio_storage_dir / "raw" / prefix
    storage_dir.mkdir(parents=True, exist_ok=True)
    audio_file = storage_dir / f"{file_hash}.{fmt}"
    # Minimal WAV header (44 bytes) + silence
    audio_file.write_bytes(b"RIFF" + b"\x00" * 40 + b"\x00" * 100)

    now = datetime.now(UTC)
    track = Track(
        id=uuid.uuid4(),
        title="WAV Track",
        artist="WAV Artist",
        album=None,
        duration_seconds=5.0,
        file_hash_sha256=file_hash,
        file_size_bytes=144,
        file_path=str(audio_file),
        format=fmt,
        olaf_indexed=False,
        ingested_at=now,
        updated_at=now,
    )

    async with _test_session_factory() as session:
        session.add(track)
        await session.commit()

    return track


@pytest.fixture
async def seed_track_no_file(audio_storage_dir: Path) -> Track:
    """Insert a track into the DB without creating the file on disk."""
    file_hash = "c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4"
    fmt = "mp3"
    now = datetime.now(UTC)
    track = Track(
        id=uuid.uuid4(),
        title="Missing File Track",
        artist="Ghost Artist",
        album=None,
        duration_seconds=30.0,
        file_hash_sha256=file_hash,
        file_size_bytes=500_000,
        file_path=f"./data/raw/{file_hash[:2]}/{file_hash}.{fmt}",
        format=fmt,
        olaf_indexed=False,
        ingested_at=now,
        updated_at=now,
    )

    async with _test_session_factory() as session:
        session.add(track)
        await session.commit()

    return track


@pytest.fixture
async def seed_track_null_format(audio_storage_dir: Path) -> Track:
    """Insert a track with format=NULL and a file on disk."""
    file_hash = "d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5"
    ext = "mp3"

    # Create the file on disk
    prefix = file_hash[:2]
    storage_dir = audio_storage_dir / "raw" / prefix
    storage_dir.mkdir(parents=True, exist_ok=True)
    audio_file = storage_dir / f"{file_hash}.{ext}"
    audio_file.write_bytes(_make_minimal_mp3())

    now = datetime.now(UTC)
    track = Track(
        id=uuid.uuid4(),
        title="Null Format Track",
        artist=None,
        album=None,
        duration_seconds=15.0,
        file_hash_sha256=file_hash,
        file_size_bytes=len(_make_minimal_mp3()),
        file_path=str(audio_file),
        format=None,  # NULL format
        olaf_indexed=False,
        ingested_at=now,
        updated_at=now,
    )

    async with _test_session_factory() as session:
        session.add(track)
        await session.commit()

    return track


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetTrackAudio:
    """Tests for the GET /api/v1/tracks/{track_id}/audio endpoint."""

    async def test_audio_full_response(self, client: AsyncClient, seed_track_with_file: Track):
        """GET without Range header returns 200 OK with full file content."""
        track = seed_track_with_file
        resp = await client.get(f"/api/v1/tracks/{track.id}/audio")
        assert resp.status_code == 200
        assert len(resp.content) > 0

    async def test_audio_content_type_mp3(self, client: AsyncClient, seed_track_with_file: Track):
        """Content-Type is audio/mpeg for MP3 tracks."""
        track = seed_track_with_file
        resp = await client.get(f"/api/v1/tracks/{track.id}/audio")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"

    async def test_audio_content_type_wav(self, client: AsyncClient, seed_track_wav: Track):
        """Content-Type is audio/wav for WAV tracks."""
        track = seed_track_wav
        resp = await client.get(f"/api/v1/tracks/{track.id}/audio")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/wav"

    async def test_audio_accept_ranges_header(
        self, client: AsyncClient, seed_track_with_file: Track
    ):
        """Accept-Ranges: bytes header is present."""
        track = seed_track_with_file
        resp = await client.get(f"/api/v1/tracks/{track.id}/audio")
        assert resp.status_code == 200
        assert resp.headers.get("accept-ranges") == "bytes"

    async def test_audio_content_length_header(
        self, client: AsyncClient, seed_track_with_file: Track
    ):
        """Content-Length header is present and matches file size."""
        track = seed_track_with_file
        resp = await client.get(f"/api/v1/tracks/{track.id}/audio")
        assert resp.status_code == 200
        content_length = int(resp.headers["content-length"])
        assert content_length == len(_make_minimal_mp3())

    async def test_audio_range_request_partial_content(
        self, client: AsyncClient, seed_track_with_file: Track
    ):
        """GET with Range: bytes=0-1023 returns 206 Partial Content."""
        track = seed_track_with_file
        resp = await client.get(
            f"/api/v1/tracks/{track.id}/audio",
            headers={"Range": "bytes=0-99"},
        )
        assert resp.status_code == 206
        assert "content-range" in resp.headers
        assert resp.headers["content-range"].startswith("bytes 0-99/")
        assert len(resp.content) == 100

    async def test_audio_range_request_open_ended(
        self, client: AsyncClient, seed_track_with_file: Track
    ):
        """GET with Range: bytes=10- returns 206 with content from offset 10 to EOF."""
        track = seed_track_with_file
        total_size = len(_make_minimal_mp3())
        resp = await client.get(
            f"/api/v1/tracks/{track.id}/audio",
            headers={"Range": "bytes=10-"},
        )
        assert resp.status_code == 206
        assert len(resp.content) == total_size - 10
        expected_range = f"bytes 10-{total_size - 1}/{total_size}"
        assert resp.headers["content-range"] == expected_range

    async def test_audio_track_not_found(self, client: AsyncClient):
        """GET with nonexistent track_id returns 404 with NOT_FOUND code."""
        random_id = uuid.uuid4()
        resp = await client.get(f"/api/v1/tracks/{random_id}/audio")
        assert resp.status_code == 404

        body = resp.json()
        assert body["error"]["code"] == "NOT_FOUND"
        assert str(random_id) in body["error"]["message"]

    async def test_audio_file_missing_from_disk(
        self, client: AsyncClient, seed_track_no_file: Track
    ):
        """GET for track with missing file on disk returns 404 with FILE_NOT_FOUND."""
        track = seed_track_no_file
        resp = await client.get(f"/api/v1/tracks/{track.id}/audio")
        assert resp.status_code == 404

        body = resp.json()
        assert body["error"]["code"] == "FILE_NOT_FOUND"

    async def test_audio_null_format_fallback(
        self, client: AsyncClient, seed_track_null_format: Track
    ):
        """Track with NULL format falls back to file extension from file_path."""
        track = seed_track_null_format
        resp = await client.get(f"/api/v1/tracks/{track.id}/audio")
        # The _resolve_format function should detect 'mp3' from the file_path
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"

    async def test_audio_invalid_uuid(self, client: AsyncClient):
        """GET with invalid UUID returns 422."""
        resp = await client.get("/api/v1/tracks/not-a-uuid/audio")
        assert resp.status_code == 422

    async def test_audio_etag_header_present(
        self, client: AsyncClient, seed_track_with_file: Track
    ):
        """ETag header is present for caching support."""
        track = seed_track_with_file
        resp = await client.get(f"/api/v1/tracks/{track.id}/audio")
        assert resp.status_code == 200
        assert "etag" in resp.headers

    async def test_audio_last_modified_header_present(
        self, client: AsyncClient, seed_track_with_file: Track
    ):
        """Last-Modified header is present."""
        track = seed_track_with_file
        resp = await client.get(f"/api/v1/tracks/{track.id}/audio")
        assert resp.status_code == 200
        assert "last-modified" in resp.headers
