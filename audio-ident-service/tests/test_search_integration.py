"""Integration tests for the POST /api/v1/search endpoint.

Tests cover:
- Upload MP3 -> get results (both mode)
- Upload WebM -> get results (both mode)
- Upload with mode=exact -> only exact lane runs
- Upload with mode=vibe -> only vibe lane runs
- Upload invalid format -> 400
- Upload too-large file -> 400
- Upload too-short audio -> 400
- Zero-byte upload -> 400
- One lane fails -> 200 with partial results
- Both lanes fail -> 503
- Both lanes timeout -> 504
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from app.schemas.search import ExactMatch, SearchMode, TrackInfo, VibeMatch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pcm_bytes(duration_sec: float, sample_rate: int = 16000) -> bytes:
    """Create PCM float32 bytes of given duration."""
    num_samples = int(duration_sec * sample_rate)
    t = np.linspace(0, duration_sec, num_samples, dtype=np.float32)
    audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    return audio.tobytes()


def _make_track_info(track_id: uuid.UUID | None = None) -> TrackInfo:
    """Create a TrackInfo for test assertions."""
    return TrackInfo(
        id=track_id or uuid.uuid4(),
        title="Test Track",
        artist="Test Artist",
        album="Test Album",
        duration_seconds=180.0,
        ingested_at=datetime.now(UTC),
    )


def _make_exact_match(track_id: uuid.UUID | None = None) -> ExactMatch:
    """Create an ExactMatch for test assertions."""
    return ExactMatch(
        track=_make_track_info(track_id),
        confidence=0.95,
        offset_seconds=5.0,
        aligned_hashes=25,
    )


def _make_vibe_match(track_id: uuid.UUID | None = None) -> VibeMatch:
    """Create a VibeMatch for test assertions."""
    return VibeMatch(
        track=_make_track_info(track_id),
        similarity=0.85,
        embedding_model="clap-htsat-large",
    )


# A minimal valid MP3 file header (MPEG audio frame).
# python-magic identifies this as audio/mpeg.
_FAKE_MP3_HEADER = (
    b"\xff\xfb\x90\x00" + b"\x00" * 1148  # MPEG1 Layer 3 frame header + padding
)

# A minimal WebM header (EBML + Segment).
# python-magic identifies this as video/webm or audio/webm.
_FAKE_WEBM_HEADER = (
    b"\x1a\x45\xdf\xa3"  # EBML header
    + b"\x01\x00\x00\x00\x00\x00\x00\x1f"  # EBML size
    + b"\x42\x86\x81\x01"  # EBMLVersion
    + b"\x42\xf7\x81\x01"  # EBMLReadVersion
    + b"\x42\xf2\x81\x04"  # EBMLMaxIDLength
    + b"\x42\xf3\x81\x08"  # EBMLMaxSizeLength
    + b"\x42\x82\x84webm"  # DocType = "webm"
    + b"\x42\x87\x81\x04"  # DocTypeVersion
    + b"\x42\x85\x81\x02"  # DocTypeReadVersion
    + b"\x18\x53\x80\x67"  # Segment
    + b"\x01\x00\x00\x00\x00\x00\x00\x00"  # Segment size (unknown)
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def search_app():
    """Create a FastAPI test app without a lifespan (no Postgres/Qdrant/CLAP).

    Sets mock app.state attributes directly on the application instance so
    the search endpoint can access ``request.app.state.qdrant`` etc.

    httpx's ASGITransport does NOT run ASGI lifespan events by default, so
    we skip the lifespan entirely and set state imperatively.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    from app.routers import health, search, version
    from app.settings import settings

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(health.router)
    application.include_router(version.router, prefix="/api/v1")
    application.include_router(search.router, prefix="/api/v1")

    # Set mock state attributes that the search endpoint reads from
    # request.app.state.  These are normally set in the real lifespan handler.
    application.state.qdrant = MagicMock()
    application.state.clap_model = MagicMock()
    application.state.clap_processor = MagicMock()

    return application


@pytest.fixture
async def search_client(search_app):
    """Async HTTP client for testing search endpoints."""
    transport = ASGITransport(app=search_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Test: Successful searches
# ---------------------------------------------------------------------------


class TestSearchSuccess:
    """Tests for successful search requests returning 200."""

    async def test_search_both_mode_mp3(self, search_client: AsyncClient):
        """Upload MP3 in both mode -> get results from both lanes."""
        mock_exact = [_make_exact_match()]
        mock_vibe = [_make_vibe_match()]
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            mock_magic.from_buffer.return_value = "audio/mpeg"
            mock_decode.return_value = (pcm_16k, pcm_48k)

            from app.schemas.search import SearchResponse

            mock_orch.return_value = SearchResponse(
                request_id=uuid.uuid4(),
                query_duration_ms=150.0,
                exact_matches=mock_exact,
                vibe_matches=mock_vibe,
                mode_used=SearchMode.BOTH,
            )

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.mp3", _FAKE_MP3_HEADER, "audio/mpeg")},
                data={"mode": "both", "max_results": "5"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "request_id" in data
        assert "query_duration_ms" in data
        assert data["mode_used"] == "both"
        assert isinstance(data["exact_matches"], list)
        assert isinstance(data["vibe_matches"], list)
        assert len(data["exact_matches"]) == 1
        assert len(data["vibe_matches"]) == 1

    async def test_search_both_mode_webm(self, search_client: AsyncClient):
        """Upload WebM in both mode -> get results."""
        mock_exact = [_make_exact_match()]
        mock_vibe = [_make_vibe_match()]
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            # WebM may be detected as video/webm or audio/webm.
            # We mock it to return audio/webm.
            mock_magic.from_buffer.return_value = "audio/webm"
            mock_decode.return_value = (pcm_16k, pcm_48k)

            from app.schemas.search import SearchResponse

            mock_orch.return_value = SearchResponse(
                request_id=uuid.uuid4(),
                query_duration_ms=120.0,
                exact_matches=mock_exact,
                vibe_matches=mock_vibe,
                mode_used=SearchMode.BOTH,
            )

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.webm", _FAKE_WEBM_HEADER, "audio/webm")},
                data={"mode": "both", "max_results": "5"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["mode_used"] == "both"
        assert len(data["exact_matches"]) == 1
        assert len(data["vibe_matches"]) == 1

    async def test_search_exact_only(self, search_client: AsyncClient):
        """Upload with mode=exact -> only exact_matches populated."""
        mock_exact = [_make_exact_match()]
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            mock_magic.from_buffer.return_value = "audio/mpeg"
            mock_decode.return_value = (pcm_16k, pcm_48k)

            from app.schemas.search import SearchResponse

            mock_orch.return_value = SearchResponse(
                request_id=uuid.uuid4(),
                query_duration_ms=80.0,
                exact_matches=mock_exact,
                vibe_matches=[],
                mode_used=SearchMode.EXACT,
            )

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.mp3", _FAKE_MP3_HEADER, "audio/mpeg")},
                data={"mode": "exact", "max_results": "10"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["mode_used"] == "exact"
        assert len(data["exact_matches"]) == 1
        assert len(data["vibe_matches"]) == 0

    async def test_search_vibe_only(self, search_client: AsyncClient):
        """Upload with mode=vibe -> only vibe_matches populated."""
        mock_vibe = [_make_vibe_match()]
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            mock_magic.from_buffer.return_value = "audio/mpeg"
            mock_decode.return_value = (pcm_16k, pcm_48k)

            from app.schemas.search import SearchResponse

            mock_orch.return_value = SearchResponse(
                request_id=uuid.uuid4(),
                query_duration_ms=200.0,
                exact_matches=[],
                vibe_matches=mock_vibe,
                mode_used=SearchMode.VIBE,
            )

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.mp3", _FAKE_MP3_HEADER, "audio/mpeg")},
                data={"mode": "vibe", "max_results": "10"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["mode_used"] == "vibe"
        assert len(data["exact_matches"]) == 0
        assert len(data["vibe_matches"]) == 1


# ---------------------------------------------------------------------------
# Test: Validation errors (400)
# ---------------------------------------------------------------------------


class TestSearchValidation:
    """Tests for upload validation returning 400 errors."""

    async def test_invalid_format(self, search_client: AsyncClient):
        """Upload a non-audio file -> 400 UNSUPPORTED_FORMAT."""
        with patch("app.routers.search.magic") as mock_magic:
            mock_magic.from_buffer.return_value = "text/plain"

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.txt", b"not audio content here", "text/plain")},
                data={"mode": "both"},
            )

        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "UNSUPPORTED_FORMAT"

    async def test_file_too_large(self, search_client: AsyncClient):
        """Upload a file exceeding 10 MB -> 400 FILE_TOO_LARGE."""
        large_content = b"\x00" * (10 * 1024 * 1024 + 1)  # 10 MB + 1 byte

        with patch("app.routers.search.magic") as mock_magic:
            mock_magic.from_buffer.return_value = "audio/mpeg"

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("large.mp3", large_content, "audio/mpeg")},
                data={"mode": "both"},
            )

        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "FILE_TOO_LARGE"

    async def test_audio_too_short(self, search_client: AsyncClient):
        """Upload audio shorter than 3 seconds -> 400 AUDIO_TOO_SHORT."""
        pcm_16k = _make_pcm_bytes(1.0, sample_rate=16000)  # 1 second
        pcm_48k = _make_pcm_bytes(1.0, sample_rate=48000)

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=1.0),
        ):
            mock_magic.from_buffer.return_value = "audio/mpeg"
            mock_decode.return_value = (pcm_16k, pcm_48k)

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("short.mp3", _FAKE_MP3_HEADER, "audio/mpeg")},
                data={"mode": "both"},
            )

        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "AUDIO_TOO_SHORT"

    async def test_zero_byte_upload(self, search_client: AsyncClient):
        """Upload an empty file -> 400 EMPTY_FILE."""
        resp = await search_client.post(
            "/api/v1/search",
            files={"audio": ("empty.mp3", b"", "audio/mpeg")},
            data={"mode": "both"},
        )

        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "EMPTY_FILE"

    async def test_decode_failure(self, search_client: AsyncClient):
        """Upload audio that fails to decode -> 400 UNSUPPORTED_FORMAT."""
        from app.audio.decode import AudioDecodeError

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
        ):
            mock_magic.from_buffer.return_value = "audio/mpeg"
            mock_decode.side_effect = AudioDecodeError("ffmpeg failed")

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("bad.mp3", _FAKE_MP3_HEADER, "audio/mpeg")},
                data={"mode": "both"},
            )

        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "UNSUPPORTED_FORMAT"


# ---------------------------------------------------------------------------
# Test: Partial failures (200) and total failures (503/504)
# ---------------------------------------------------------------------------


class TestSearchLaneFailures:
    """Tests for lane failure scenarios."""

    async def test_one_lane_fails_returns_partial(self, search_client: AsyncClient):
        """One lane fails -> 200 with partial results (empty array for failed lane)."""
        mock_exact = [_make_exact_match()]
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            mock_magic.from_buffer.return_value = "audio/mpeg"
            mock_decode.return_value = (pcm_16k, pcm_48k)

            from app.schemas.search import SearchResponse

            # Vibe lane failed, exact lane succeeded
            mock_orch.return_value = SearchResponse(
                request_id=uuid.uuid4(),
                query_duration_ms=100.0,
                exact_matches=mock_exact,
                vibe_matches=[],  # Vibe lane failed
                mode_used=SearchMode.BOTH,
            )

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.mp3", _FAKE_MP3_HEADER, "audio/mpeg")},
                data={"mode": "both"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["exact_matches"]) == 1
        assert len(data["vibe_matches"]) == 0

    async def test_both_lanes_fail_returns_503(self, search_client: AsyncClient):
        """Both lanes fail -> 503 SERVICE_UNAVAILABLE."""
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        from app.search.orchestrator import SearchUnavailableError

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            mock_magic.from_buffer.return_value = "audio/mpeg"
            mock_decode.return_value = (pcm_16k, pcm_48k)
            mock_orch.side_effect = SearchUnavailableError("Both lanes failed")

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.mp3", _FAKE_MP3_HEADER, "audio/mpeg")},
                data={"mode": "both"},
            )

        assert resp.status_code == 503
        data = resp.json()
        assert data["error"]["code"] == "SERVICE_UNAVAILABLE"

    async def test_both_lanes_timeout_returns_504(self, search_client: AsyncClient):
        """Both lanes timeout -> 504 SEARCH_TIMEOUT."""
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        from app.search.orchestrator import SearchTimeoutError

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            mock_magic.from_buffer.return_value = "audio/mpeg"
            mock_decode.return_value = (pcm_16k, pcm_48k)
            mock_orch.side_effect = SearchTimeoutError("Both lanes timed out")

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.mp3", _FAKE_MP3_HEADER, "audio/mpeg")},
                data={"mode": "both"},
            )

        assert resp.status_code == 504
        data = resp.json()
        assert data["error"]["code"] == "SEARCH_TIMEOUT"


# ---------------------------------------------------------------------------
# Test: Orchestrator unit tests (parallel execution)
# ---------------------------------------------------------------------------


class TestOrchestrator:
    """Unit tests for the orchestrator module itself."""

    async def test_orchestrate_exact_only(self):
        """mode=exact runs only the exact lane."""
        mock_exact = [_make_exact_match()]

        with patch(
            "app.search.orchestrator.run_exact_lane", new_callable=AsyncMock
        ) as mock_exact_lane:
            mock_exact_lane.return_value = mock_exact

            from app.search.orchestrator import orchestrate_search

            result = await orchestrate_search(
                pcm_16k=b"fake_pcm",
                pcm_48k=b"fake_pcm",
                mode=SearchMode.EXACT,
                max_results=10,
                qdrant_client=MagicMock(),
                clap_model=MagicMock(),
                clap_processor=MagicMock(),
            )

        assert result.mode_used == SearchMode.EXACT
        assert len(result.exact_matches) == 1
        assert len(result.vibe_matches) == 0
        assert result.query_duration_ms > 0
        mock_exact_lane.assert_awaited_once()

    async def test_orchestrate_vibe_only(self):
        """mode=vibe runs only the vibe lane."""
        mock_vibe = [_make_vibe_match()]

        with (
            patch(
                "app.search.orchestrator.run_vibe_lane", new_callable=AsyncMock
            ) as mock_vibe_lane,
            patch("app.search.orchestrator.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_vibe_lane.return_value = mock_vibe

            from app.search.orchestrator import orchestrate_search

            result = await orchestrate_search(
                pcm_16k=b"fake_pcm",
                pcm_48k=b"fake_pcm",
                mode=SearchMode.VIBE,
                max_results=10,
                qdrant_client=MagicMock(),
                clap_model=MagicMock(),
                clap_processor=MagicMock(),
            )

        assert result.mode_used == SearchMode.VIBE
        assert len(result.exact_matches) == 0
        assert len(result.vibe_matches) == 1

    async def test_orchestrate_both_parallel(self):
        """mode=both runs both lanes in parallel."""
        mock_exact = [_make_exact_match()]
        mock_vibe = [_make_vibe_match()]

        with (
            patch(
                "app.search.orchestrator.run_exact_lane", new_callable=AsyncMock
            ) as mock_exact_lane,
            patch(
                "app.search.orchestrator.run_vibe_lane", new_callable=AsyncMock
            ) as mock_vibe_lane,
            patch("app.search.orchestrator.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_exact_lane.return_value = mock_exact
            mock_vibe_lane.return_value = mock_vibe

            from app.search.orchestrator import orchestrate_search

            result = await orchestrate_search(
                pcm_16k=b"fake_pcm",
                pcm_48k=b"fake_pcm",
                mode=SearchMode.BOTH,
                max_results=10,
                qdrant_client=MagicMock(),
                clap_model=MagicMock(),
                clap_processor=MagicMock(),
            )

        assert result.mode_used == SearchMode.BOTH
        assert len(result.exact_matches) == 1
        assert len(result.vibe_matches) == 1
        mock_exact_lane.assert_awaited_once()
        mock_vibe_lane.assert_awaited_once()

    async def test_orchestrate_both_one_lane_fails(self):
        """mode=both, one lane raises exception -> partial results (200)."""
        mock_exact = [_make_exact_match()]

        with (
            patch(
                "app.search.orchestrator.run_exact_lane", new_callable=AsyncMock
            ) as mock_exact_lane,
            patch(
                "app.search.orchestrator.run_vibe_lane", new_callable=AsyncMock
            ) as mock_vibe_lane,
            patch("app.search.orchestrator.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_exact_lane.return_value = mock_exact
            mock_vibe_lane.side_effect = ValueError("CLAP model error")

            from app.search.orchestrator import orchestrate_search

            result = await orchestrate_search(
                pcm_16k=b"fake_pcm",
                pcm_48k=b"fake_pcm",
                mode=SearchMode.BOTH,
                max_results=10,
                qdrant_client=MagicMock(),
                clap_model=MagicMock(),
                clap_processor=MagicMock(),
            )

        # Partial results: exact succeeded, vibe failed
        assert len(result.exact_matches) == 1
        assert len(result.vibe_matches) == 0

    async def test_orchestrate_both_both_fail_raises_unavailable(self):
        """mode=both, both lanes raise non-timeout exceptions -> SearchUnavailableError."""
        from app.search.orchestrator import SearchUnavailableError, orchestrate_search

        with (
            patch(
                "app.search.orchestrator.run_exact_lane", new_callable=AsyncMock
            ) as mock_exact_lane,
            patch(
                "app.search.orchestrator.run_vibe_lane", new_callable=AsyncMock
            ) as mock_vibe_lane,
            patch("app.search.orchestrator.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_exact_lane.side_effect = RuntimeError("Olaf crashed")
            mock_vibe_lane.side_effect = ValueError("CLAP failed")

            with pytest.raises(SearchUnavailableError):
                await orchestrate_search(
                    pcm_16k=b"fake_pcm",
                    pcm_48k=b"fake_pcm",
                    mode=SearchMode.BOTH,
                    max_results=10,
                    qdrant_client=MagicMock(),
                    clap_model=MagicMock(),
                    clap_processor=MagicMock(),
                )

    async def test_orchestrate_both_both_timeout_raises_timeout(self):
        """mode=both, both lanes timeout -> SearchTimeoutError."""
        from app.search.orchestrator import SearchTimeoutError, orchestrate_search

        async def slow_exact(*args, **kwargs):
            await asyncio.sleep(10)
            return []

        async def slow_vibe(*args, **kwargs):
            await asyncio.sleep(10)
            return []

        with (
            patch("app.search.orchestrator.run_exact_lane", side_effect=slow_exact),
            patch("app.search.orchestrator.run_vibe_lane", side_effect=slow_vibe),
            patch("app.search.orchestrator.async_session_factory") as mock_session_factory,
            patch("app.search.orchestrator.EXACT_TIMEOUT_SECONDS", 0.1),
            patch("app.search.orchestrator.VIBE_TIMEOUT_SECONDS", 0.1),
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(SearchTimeoutError):
                await orchestrate_search(
                    pcm_16k=b"fake_pcm",
                    pcm_48k=b"fake_pcm",
                    mode=SearchMode.BOTH,
                    max_results=10,
                    qdrant_client=MagicMock(),
                    clap_model=MagicMock(),
                    clap_processor=MagicMock(),
                )

    async def test_orchestrate_exact_timeout_raises_search_timeout(self):
        """mode=exact, exact lane times out -> SearchTimeoutError."""
        from app.search.orchestrator import SearchTimeoutError, orchestrate_search

        async def slow_exact(*args, **kwargs):
            await asyncio.sleep(10)
            return []

        with (
            patch("app.search.orchestrator.run_exact_lane", side_effect=slow_exact),
            patch("app.search.orchestrator.EXACT_TIMEOUT_SECONDS", 0.1),
            pytest.raises(SearchTimeoutError, match="Exact search lane timed out"),
        ):
            await orchestrate_search(
                pcm_16k=b"fake_pcm",
                pcm_48k=b"fake_pcm",
                mode=SearchMode.EXACT,
                max_results=10,
                qdrant_client=MagicMock(),
                clap_model=MagicMock(),
                clap_processor=MagicMock(),
            )

    async def test_orchestrate_vibe_error_raises_unavailable(self):
        """mode=vibe, vibe lane throws -> SearchUnavailableError."""
        from app.search.orchestrator import SearchUnavailableError, orchestrate_search

        with (
            patch(
                "app.search.orchestrator.run_vibe_lane", new_callable=AsyncMock
            ) as mock_vibe_lane,
            patch("app.search.orchestrator.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_vibe_lane.side_effect = ValueError("CLAP model error")

            with pytest.raises(SearchUnavailableError, match="Vibe search lane failed"):
                await orchestrate_search(
                    pcm_16k=b"fake_pcm",
                    pcm_48k=b"fake_pcm",
                    mode=SearchMode.VIBE,
                    max_results=10,
                    qdrant_client=MagicMock(),
                    clap_model=MagicMock(),
                    clap_processor=MagicMock(),
                )


# ---------------------------------------------------------------------------
# Test: Single-lane failures at the HTTP level (504 / 503)
# ---------------------------------------------------------------------------


class TestSingleLaneHTTPErrors:
    """Tests that single-lane failures produce correct HTTP status codes."""

    async def test_exact_lane_timeout_returns_504(self, search_client: AsyncClient):
        """mode=exact, exact lane times out -> 504 SEARCH_TIMEOUT."""
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        from app.search.orchestrator import SearchTimeoutError

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            mock_magic.from_buffer.return_value = "audio/mpeg"
            mock_decode.return_value = (pcm_16k, pcm_48k)
            mock_orch.side_effect = SearchTimeoutError("Exact search lane timed out")

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.mp3", _FAKE_MP3_HEADER, "audio/mpeg")},
                data={"mode": "exact"},
            )

        assert resp.status_code == 504
        data = resp.json()
        assert data["error"]["code"] == "SEARCH_TIMEOUT"

    async def test_vibe_lane_error_returns_503(self, search_client: AsyncClient):
        """mode=vibe, vibe lane throws -> 503 SERVICE_UNAVAILABLE."""
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        from app.search.orchestrator import SearchUnavailableError

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            mock_magic.from_buffer.return_value = "audio/mpeg"
            mock_decode.return_value = (pcm_16k, pcm_48k)
            mock_orch.side_effect = SearchUnavailableError("Vibe search lane failed")

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.mp3", _FAKE_MP3_HEADER, "audio/mpeg")},
                data={"mode": "vibe"},
            )

        assert resp.status_code == 503
        data = resp.json()
        assert data["error"]["code"] == "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Test: MIME type detection (video/webm, FLAC)
# ---------------------------------------------------------------------------


class TestMimeTypeDetection:
    """Tests for MIME type handling of video/webm and FLAC."""

    async def test_video_webm_accepted(self, search_client: AsyncClient):
        """python-magic returning video/webm for WebM should be accepted."""
        mock_exact = [_make_exact_match()]
        mock_vibe = [_make_vibe_match()]
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            # Simulate real python-magic behavior: returns video/webm for WebM containers
            mock_magic.from_buffer.return_value = "video/webm"
            mock_decode.return_value = (pcm_16k, pcm_48k)

            from app.schemas.search import SearchResponse

            mock_orch.return_value = SearchResponse(
                request_id=uuid.uuid4(),
                query_duration_ms=120.0,
                exact_matches=mock_exact,
                vibe_matches=mock_vibe,
                mode_used=SearchMode.BOTH,
            )

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.webm", _FAKE_WEBM_HEADER, "video/webm")},
                data={"mode": "both"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["mode_used"] == "both"

    async def test_audio_flac_accepted(self, search_client: AsyncClient):
        """audio/flac MIME type should be accepted."""
        mock_exact = [_make_exact_match()]
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            mock_magic.from_buffer.return_value = "audio/flac"
            mock_decode.return_value = (pcm_16k, pcm_48k)

            from app.schemas.search import SearchResponse

            mock_orch.return_value = SearchResponse(
                request_id=uuid.uuid4(),
                query_duration_ms=80.0,
                exact_matches=mock_exact,
                vibe_matches=[],
                mode_used=SearchMode.EXACT,
            )

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.flac", b"\x66\x4c\x61\x43" + b"\x00" * 100, "audio/flac")},
                data={"mode": "exact"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["mode_used"] == "exact"

    async def test_audio_x_flac_accepted(self, search_client: AsyncClient):
        """audio/x-flac MIME type should also be accepted."""
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            mock_magic.from_buffer.return_value = "audio/x-flac"
            mock_decode.return_value = (pcm_16k, pcm_48k)

            from app.schemas.search import SearchResponse

            mock_orch.return_value = SearchResponse(
                request_id=uuid.uuid4(),
                query_duration_ms=80.0,
                exact_matches=[],
                vibe_matches=[],
                mode_used=SearchMode.EXACT,
            )

            resp = await search_client.post(
                "/api/v1/search",
                files={"audio": ("test.flac", b"\x66\x4c\x61\x43" + b"\x00" * 100, "audio/x-flac")},
                data={"mode": "exact"},
            )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Test: CLAP not loaded + vibe mode
# ---------------------------------------------------------------------------


class TestCLAPNotLoaded:
    """Tests for graceful degradation when CLAP model is not available."""

    @pytest.fixture
    def search_app_no_clap(self):
        """Create a FastAPI test app without CLAP model (simulates startup failure)."""
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware

        from app.routers import health, search, version
        from app.settings import settings

        application = FastAPI(
            title=settings.app_name,
            version=settings.app_version,
        )
        application.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        application.include_router(health.router)
        application.include_router(version.router, prefix="/api/v1")
        application.include_router(search.router, prefix="/api/v1")

        # CLAP model not loaded (None)
        application.state.qdrant = MagicMock()
        application.state.clap_model = None
        application.state.clap_processor = None

        return application

    @pytest.fixture
    async def client_no_clap(self, search_app_no_clap):
        """Async HTTP client for testing with no CLAP model."""
        transport = ASGITransport(app=search_app_no_clap)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    async def test_vibe_mode_without_clap_returns_503(self, client_no_clap: AsyncClient):
        """mode=vibe with CLAP not loaded -> 503 SERVICE_UNAVAILABLE."""
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
        ):
            mock_magic.from_buffer.return_value = "audio/mpeg"
            mock_decode.return_value = (pcm_16k, pcm_48k)

            resp = await client_no_clap.post(
                "/api/v1/search",
                files={"audio": ("test.mp3", _FAKE_MP3_HEADER, "audio/mpeg")},
                data={"mode": "vibe"},
            )

        assert resp.status_code == 503
        data = resp.json()
        assert data["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert "Embedding model" in data["error"]["message"]

    async def test_both_mode_without_clap_downgrades_to_exact(self, client_no_clap: AsyncClient):
        """mode=both with CLAP not loaded -> silently downgrade to mode=exact."""
        mock_exact = [_make_exact_match()]
        pcm_16k = _make_pcm_bytes(5.0, sample_rate=16000)
        pcm_48k = _make_pcm_bytes(5.0, sample_rate=48000)

        with (
            patch("app.routers.search.magic") as mock_magic,
            patch("app.routers.search.decode_dual_rate", new_callable=AsyncMock) as mock_decode,
            patch("app.routers.search.pcm_duration_seconds", return_value=5.0),
            patch("app.routers.search.orchestrate_search", new_callable=AsyncMock) as mock_orch,
        ):
            mock_magic.from_buffer.return_value = "audio/mpeg"
            mock_decode.return_value = (pcm_16k, pcm_48k)

            from app.schemas.search import SearchResponse

            mock_orch.return_value = SearchResponse(
                request_id=uuid.uuid4(),
                query_duration_ms=80.0,
                exact_matches=mock_exact,
                vibe_matches=[],
                mode_used=SearchMode.EXACT,
            )

            resp = await client_no_clap.post(
                "/api/v1/search",
                files={"audio": ("test.mp3", _FAKE_MP3_HEADER, "audio/mpeg")},
                data={"mode": "both"},
            )

        assert resp.status_code == 200
        # Orchestrator was called with mode=exact (downgraded from both)
        mock_orch.assert_awaited_once()
        call_kwargs = mock_orch.call_args
        assert call_kwargs.kwargs["mode"] == SearchMode.EXACT
