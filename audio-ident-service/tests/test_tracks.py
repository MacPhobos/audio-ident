"""Integration tests for GET /api/v1/tracks and GET /api/v1/tracks/{track_id}.

Uses a standalone FastAPI app with SQLite (via aiosqlite) to avoid requiring
real PostgreSQL/Qdrant infrastructure during testing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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
async def seed_tracks() -> list[Track]:
    """Insert 3 tracks into the test database and return them."""
    now = datetime.now(UTC)
    tracks = [
        Track(
            id=uuid.uuid4(),
            title="Alpha Song",
            artist="Artist One",
            album="Album A",
            duration_seconds=200.0,
            file_hash_sha256="a" * 64,
            file_size_bytes=1_000_000,
            file_path="/data/alpha.mp3",
            olaf_indexed=True,
            ingested_at=now,
            updated_at=now,
        ),
        Track(
            id=uuid.uuid4(),
            title="Beta Track",
            artist="Artist Two",
            album="Album B",
            duration_seconds=180.0,
            file_hash_sha256="b" * 64,
            file_size_bytes=2_000_000,
            file_path="/data/beta.mp3",
            olaf_indexed=False,
            ingested_at=now,
            updated_at=now,
        ),
        Track(
            id=uuid.uuid4(),
            title="Gamma Melody",
            artist="Artist One",
            album="Album C",
            duration_seconds=240.0,
            file_hash_sha256="c" * 64,
            file_size_bytes=3_000_000,
            file_path="/data/gamma.mp3",
            olaf_indexed=True,
            ingested_at=now,
            updated_at=now,
        ),
    ]

    async with _test_session_factory() as session:
        session.add_all(tracks)
        await session.commit()

    return tracks


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/tracks
# ---------------------------------------------------------------------------


class TestListTracks:
    """Tests for the paginated track listing endpoint."""

    async def test_list_tracks_returns_paginated_response(
        self, client: AsyncClient, seed_tracks: list[Track]
    ):
        resp = await client.get("/api/v1/tracks")
        assert resp.status_code == 200

        body = resp.json()
        assert "data" in body
        assert "pagination" in body
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 3

    async def test_list_tracks_pagination_meta_uses_camel_case(
        self, client: AsyncClient, seed_tracks: list[Track]
    ):
        resp = await client.get("/api/v1/tracks")
        assert resp.status_code == 200

        pagination = resp.json()["pagination"]
        assert "pageSize" in pagination
        assert "totalItems" in pagination
        assert "totalPages" in pagination
        assert pagination["totalItems"] == 3
        assert pagination["totalPages"] == 1
        assert pagination["page"] == 1
        assert pagination["pageSize"] == 50

    async def test_list_tracks_empty_database(self, client: AsyncClient):
        resp = await client.get("/api/v1/tracks")
        assert resp.status_code == 200

        body = resp.json()
        assert body["data"] == []
        assert body["pagination"]["totalItems"] == 0
        assert body["pagination"]["totalPages"] == 0

    async def test_list_tracks_with_search_filter(
        self, client: AsyncClient, seed_tracks: list[Track]
    ):
        resp = await client.get("/api/v1/tracks", params={"search": "Alpha"})
        assert resp.status_code == 200

        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["title"] == "Alpha Song"

    async def test_list_tracks_search_by_artist(
        self, client: AsyncClient, seed_tracks: list[Track]
    ):
        resp = await client.get("/api/v1/tracks", params={"search": "Artist One"})
        assert resp.status_code == 200

        body = resp.json()
        assert len(body["data"]) == 2

    async def test_list_tracks_search_case_insensitive(
        self, client: AsyncClient, seed_tracks: list[Track]
    ):
        resp = await client.get("/api/v1/tracks", params={"search": "alpha"})
        assert resp.status_code == 200

        body = resp.json()
        assert len(body["data"]) == 1

    async def test_list_tracks_page_beyond_total(
        self, client: AsyncClient, seed_tracks: list[Track]
    ):
        resp = await client.get("/api/v1/tracks", params={"page": 999})
        assert resp.status_code == 200

        body = resp.json()
        assert body["data"] == []
        assert body["pagination"]["totalItems"] == 3
        assert body["pagination"]["page"] == 999

    async def test_list_tracks_custom_page_size(
        self, client: AsyncClient, seed_tracks: list[Track]
    ):
        resp = await client.get("/api/v1/tracks", params={"pageSize": 2})
        assert resp.status_code == 200

        body = resp.json()
        assert len(body["data"]) == 2
        assert body["pagination"]["pageSize"] == 2
        assert body["pagination"]["totalPages"] == 2

    async def test_list_tracks_page_size_too_large_clamped(
        self, client: AsyncClient, seed_tracks: list[Track]
    ):
        """pageSize > 100 is clamped to 100 per API contract (not rejected)."""
        resp = await client.get("/api/v1/tracks", params={"pageSize": 200})
        assert resp.status_code == 200

        pagination = resp.json()["pagination"]
        assert pagination["pageSize"] == 100

    async def test_list_tracks_page_size_zero_clamped(
        self, client: AsyncClient, seed_tracks: list[Track]
    ):
        """pageSize < 1 is clamped to 1 per API contract (not rejected)."""
        resp = await client.get("/api/v1/tracks", params={"pageSize": 0})
        assert resp.status_code == 200

        pagination = resp.json()["pagination"]
        assert pagination["pageSize"] == 1

    async def test_list_tracks_page_zero_clamped(
        self, client: AsyncClient, seed_tracks: list[Track]
    ):
        """page < 1 is clamped to 1 per API contract (not rejected)."""
        resp = await client.get("/api/v1/tracks", params={"page": 0})
        assert resp.status_code == 200

        pagination = resp.json()["pagination"]
        assert pagination["page"] == 1

    async def test_list_tracks_data_shape(self, client: AsyncClient, seed_tracks: list[Track]):
        """Verify each track in the data list has the expected TrackInfo fields."""
        resp = await client.get("/api/v1/tracks", params={"pageSize": 1})
        assert resp.status_code == 200

        track_data = resp.json()["data"][0]
        assert "id" in track_data
        assert "title" in track_data
        assert "artist" in track_data
        assert "album" in track_data
        assert "duration_seconds" in track_data
        assert "ingested_at" in track_data


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/tracks/{track_id}
# ---------------------------------------------------------------------------


class TestGetTrackDetail:
    """Tests for the track detail endpoint."""

    async def test_get_track_detail_success(self, client: AsyncClient, seed_tracks: list[Track]):
        track = seed_tracks[0]
        resp = await client.get(f"/api/v1/tracks/{track.id}")
        assert resp.status_code == 200

        body = resp.json()
        assert body["id"] == str(track.id)
        assert body["title"] == track.title
        assert body["artist"] == track.artist
        assert body["file_hash_sha256"] == track.file_hash_sha256
        assert body["file_size_bytes"] == track.file_size_bytes
        assert body["olaf_indexed"] == track.olaf_indexed
        assert "updated_at" in body

    async def test_get_track_detail_not_found(self, client: AsyncClient):
        random_id = uuid.uuid4()
        resp = await client.get(f"/api/v1/tracks/{random_id}")
        assert resp.status_code == 404

        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "NOT_FOUND"
        assert str(random_id) in body["error"]["message"]

    async def test_get_track_detail_invalid_uuid(self, client: AsyncClient):
        resp = await client.get("/api/v1/tracks/not-a-uuid")
        assert resp.status_code == 422

    async def test_get_track_detail_includes_audio_properties(
        self, client: AsyncClient, seed_tracks: list[Track]
    ):
        """Verify the detail response includes audio property fields."""
        track = seed_tracks[0]
        resp = await client.get(f"/api/v1/tracks/{track.id}")
        assert resp.status_code == 200

        body = resp.json()
        # These fields exist on TrackDetail but not on TrackInfo
        assert "sample_rate" in body
        assert "channels" in body
        assert "bitrate" in body
        assert "format" in body
        assert "embedding_model" in body
        assert "embedding_dim" in body
