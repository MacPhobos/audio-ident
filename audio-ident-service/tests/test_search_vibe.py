"""Tests for vibe search lane and chunk-to-track aggregation.

Tests cover:
- Aggregation correctness (Top-K average + diversity bonus math)
- Exact-match exclusion
- Empty results (silence/no matches)
- Qdrant connection error handling (graceful empty result)
- Embedding model not loaded
- Below-threshold filtering
- Empty collection
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.search.aggregation import ChunkHit, aggregate_chunk_hits
from app.search.vibe import _get_tracks_by_ids, _query_qdrant, run_vibe_lane

# ──────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────


def _make_chunk_hit(
    track_id: uuid.UUID | None = None,
    score: float = 0.8,
    chunk_index: int = 0,
    offset_sec: float = 0.0,
) -> ChunkHit:
    """Create a ChunkHit with sensible defaults."""
    return ChunkHit(
        track_id=track_id or uuid.uuid4(),
        score=score,
        chunk_index=chunk_index,
        offset_sec=offset_sec,
    )


def _make_pcm_bytes(duration_sec: float, sample_rate: int = 48000) -> bytes:
    """Create PCM float32 bytes of given duration at 48kHz."""
    num_samples = int(duration_sec * sample_rate)
    t = np.linspace(0, duration_sec, num_samples, dtype=np.float32)
    audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    return audio.tobytes()


def _make_mock_track(track_id: uuid.UUID) -> MagicMock:
    """Create a mock Track model with standard attributes."""
    track = MagicMock()
    track.id = track_id
    track.title = f"Track {track_id.hex[:8]}"
    track.artist = "Test Artist"
    track.album = "Test Album"
    track.duration_seconds = 180.0
    track.ingested_at = datetime(2025, 1, 1, tzinfo=UTC)
    return track


def _make_mock_qdrant_point(
    track_id: uuid.UUID,
    score: float = 0.85,
    chunk_index: int = 0,
    offset_sec: float = 0.0,
) -> MagicMock:
    """Create a mock Qdrant ScoredPoint."""
    point = MagicMock()
    point.id = str(uuid.uuid4())
    point.score = score
    point.payload = {
        "track_id": str(track_id),
        "chunk_index": chunk_index,
        "offset_sec": offset_sec,
        "duration_sec": 10.0,
    }
    return point


# ──────────────────────────────────────────────
# aggregate_chunk_hits tests
# ──────────────────────────────────────────────


class TestAggregateChunkHits:
    def test_single_track_single_chunk(self) -> None:
        """Single track with one chunk uses that score as base."""
        tid = uuid.uuid4()
        hits = [_make_chunk_hit(track_id=tid, score=0.9)]

        results = aggregate_chunk_hits(hits)

        assert len(results) == 1
        assert results[0].track_id == tid
        assert results[0].base_score == pytest.approx(0.9)
        assert results[0].chunk_count == 1

    def test_top_k_average_correct(self) -> None:
        """Base score is mean of top-K chunk scores, not all chunks."""
        tid = uuid.uuid4()
        hits = [
            _make_chunk_hit(track_id=tid, score=0.9, chunk_index=0, offset_sec=0.0),
            _make_chunk_hit(track_id=tid, score=0.85, chunk_index=1, offset_sec=5.0),
            _make_chunk_hit(track_id=tid, score=0.7, chunk_index=2, offset_sec=10.0),
            _make_chunk_hit(track_id=tid, score=0.5, chunk_index=3, offset_sec=15.0),
            _make_chunk_hit(track_id=tid, score=0.3, chunk_index=4, offset_sec=20.0),
        ]

        # top_k_per_track=3 by default, so mean of [0.9, 0.85, 0.7]
        results = aggregate_chunk_hits(hits)

        expected_base = (0.9 + 0.85 + 0.7) / 3
        assert len(results) == 1
        assert results[0].base_score == pytest.approx(expected_base)
        assert results[0].chunk_count == 5
        assert len(results[0].top_chunk_scores) == 3

    def test_diversity_bonus_calculation(self) -> None:
        """Diversity bonus = min(unique_offsets / 5.0, 1.0) * weight."""
        tid = uuid.uuid4()
        # 3 unique offsets
        hits = [
            _make_chunk_hit(track_id=tid, score=0.8, offset_sec=0.0),
            _make_chunk_hit(track_id=tid, score=0.8, offset_sec=5.0),
            _make_chunk_hit(track_id=tid, score=0.8, offset_sec=10.0),
        ]

        results = aggregate_chunk_hits(hits, diversity_weight=0.10)

        expected_bonus = min(3 / 5.0, 1.0) * 0.10  # 0.6 * 0.10 = 0.06
        assert results[0].diversity_bonus == pytest.approx(expected_bonus)

    def test_diversity_bonus_capped_at_weight(self) -> None:
        """Diversity bonus is capped when unique_offsets >= 5."""
        tid = uuid.uuid4()
        hits = [_make_chunk_hit(track_id=tid, score=0.8, offset_sec=float(i * 5)) for i in range(7)]

        results = aggregate_chunk_hits(hits, diversity_weight=0.05)

        # min(7/5.0, 1.0) * 0.05 = 1.0 * 0.05 = 0.05
        assert results[0].diversity_bonus == pytest.approx(0.05)

    def test_final_score_is_base_plus_bonus(self) -> None:
        """Final score = base_score + diversity_bonus."""
        tid = uuid.uuid4()
        hits = [
            _make_chunk_hit(track_id=tid, score=0.9, offset_sec=0.0),
            _make_chunk_hit(track_id=tid, score=0.85, offset_sec=5.0),
            _make_chunk_hit(track_id=tid, score=0.80, offset_sec=10.0),
        ]

        results = aggregate_chunk_hits(hits, top_k_per_track=3, diversity_weight=0.05)

        expected_base = (0.9 + 0.85 + 0.80) / 3
        expected_bonus = min(3 / 5.0, 1.0) * 0.05
        expected_final = expected_base + expected_bonus

        assert results[0].final_score == pytest.approx(expected_final)

    def test_multiple_tracks_sorted_by_score(self) -> None:
        """Multiple tracks are sorted by final_score descending."""
        tid_a = uuid.uuid4()
        tid_b = uuid.uuid4()

        hits = [
            _make_chunk_hit(track_id=tid_a, score=0.7, offset_sec=0.0),
            _make_chunk_hit(track_id=tid_b, score=0.9, offset_sec=0.0),
        ]

        results = aggregate_chunk_hits(hits)

        assert len(results) == 2
        assert results[0].track_id == tid_b  # Higher score first
        assert results[1].track_id == tid_a

    def test_exact_match_exclusion(self) -> None:
        """Exact-match track is excluded when exact_match_track_id is set."""
        tid_exact = uuid.uuid4()
        tid_other = uuid.uuid4()

        hits = [
            _make_chunk_hit(track_id=tid_exact, score=0.95, offset_sec=0.0),
            _make_chunk_hit(track_id=tid_other, score=0.8, offset_sec=0.0),
        ]

        results = aggregate_chunk_hits(hits, exact_match_track_id=tid_exact)

        assert len(results) == 1
        assert results[0].track_id == tid_other

    def test_exact_match_exclusion_none_does_not_exclude(self) -> None:
        """No exclusion when exact_match_track_id is None."""
        tid = uuid.uuid4()
        hits = [_make_chunk_hit(track_id=tid, score=0.9)]

        results = aggregate_chunk_hits(hits, exact_match_track_id=None)

        assert len(results) == 1

    def test_empty_input_returns_empty(self) -> None:
        """Empty chunk_hits returns empty list."""
        results = aggregate_chunk_hits([])
        assert results == []

    def test_fewer_chunks_than_top_k(self) -> None:
        """When track has fewer chunks than top_k, average all of them."""
        tid = uuid.uuid4()
        hits = [
            _make_chunk_hit(track_id=tid, score=0.9, offset_sec=0.0),
            _make_chunk_hit(track_id=tid, score=0.7, offset_sec=5.0),
        ]

        results = aggregate_chunk_hits(hits, top_k_per_track=5)

        # Only 2 chunks available, average both
        expected_base = (0.9 + 0.7) / 2
        assert results[0].base_score == pytest.approx(expected_base)
        assert len(results[0].top_chunk_scores) == 2

    def test_all_chunks_from_same_track(self) -> None:
        """All chunks from one track produces single result."""
        tid = uuid.uuid4()
        hits = [
            _make_chunk_hit(track_id=tid, score=0.8 + i * 0.01, offset_sec=float(i * 5))
            for i in range(10)
        ]

        results = aggregate_chunk_hits(hits)

        assert len(results) == 1
        assert results[0].chunk_count == 10

    def test_custom_top_k_per_track(self) -> None:
        """Custom top_k_per_track=1 uses only the best chunk."""
        tid = uuid.uuid4()
        hits = [
            _make_chunk_hit(track_id=tid, score=0.9, offset_sec=0.0),
            _make_chunk_hit(track_id=tid, score=0.3, offset_sec=5.0),
        ]

        results = aggregate_chunk_hits(hits, top_k_per_track=1)

        assert results[0].base_score == pytest.approx(0.9)


# ──────────────────────────────────────────────
# _query_qdrant tests
# ──────────────────────────────────────────────


class TestQueryQdrant:
    @pytest.mark.asyncio
    async def test_parses_qdrant_results_correctly(self) -> None:
        """Parses Qdrant ScoredPoints into ChunkHit objects."""
        tid = uuid.uuid4()
        mock_client = AsyncMock()

        mock_result = MagicMock()
        mock_result.points = [
            _make_mock_qdrant_point(tid, score=0.85, chunk_index=0, offset_sec=0.0),
            _make_mock_qdrant_point(tid, score=0.80, chunk_index=1, offset_sec=5.0),
        ]
        mock_client.query_points.return_value = mock_result

        with patch("app.search.vibe.settings") as mock_settings:
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50

            hits = await _query_qdrant(mock_client, [0.1] * 512)

        assert len(hits) == 2
        assert hits[0].track_id == tid
        assert hits[0].score == 0.85
        assert hits[0].chunk_index == 0
        assert hits[0].offset_sec == 0.0

    @pytest.mark.asyncio
    async def test_qdrant_connection_error_returns_empty(self) -> None:
        """Qdrant connection error returns empty list (graceful degradation)."""
        mock_client = AsyncMock()
        mock_client.query_points.side_effect = Exception("Connection refused")

        with patch("app.search.vibe.settings") as mock_settings:
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50

            hits = await _query_qdrant(mock_client, [0.1] * 512)

        assert hits == []

    @pytest.mark.asyncio
    async def test_empty_collection_returns_empty(self) -> None:
        """Empty Qdrant collection returns empty list."""
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.points = []
        mock_client.query_points.return_value = mock_result

        with patch("app.search.vibe.settings") as mock_settings:
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50

            hits = await _query_qdrant(mock_client, [0.1] * 512)

        assert hits == []

    @pytest.mark.asyncio
    async def test_skips_points_with_missing_track_id(self) -> None:
        """Points with missing track_id in payload are skipped."""
        mock_client = AsyncMock()

        bad_point = MagicMock()
        bad_point.id = "bad-point"
        bad_point.score = 0.9
        bad_point.payload = {"chunk_index": 0}  # Missing track_id

        good_tid = uuid.uuid4()
        good_point = _make_mock_qdrant_point(good_tid, score=0.8)

        mock_result = MagicMock()
        mock_result.points = [bad_point, good_point]
        mock_client.query_points.return_value = mock_result

        with patch("app.search.vibe.settings") as mock_settings:
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50

            hits = await _query_qdrant(mock_client, [0.1] * 512)

        assert len(hits) == 1
        assert hits[0].track_id == good_tid

    @pytest.mark.asyncio
    async def test_skips_points_with_invalid_uuid(self) -> None:
        """Points with invalid UUID in track_id payload are skipped."""
        mock_client = AsyncMock()

        bad_point = MagicMock()
        bad_point.id = "bad-point"
        bad_point.score = 0.9
        bad_point.payload = {"track_id": "not-a-uuid", "chunk_index": 0}

        mock_result = MagicMock()
        mock_result.points = [bad_point]
        mock_client.query_points.return_value = mock_result

        with patch("app.search.vibe.settings") as mock_settings:
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50

            hits = await _query_qdrant(mock_client, [0.1] * 512)

        assert hits == []


# ──────────────────────────────────────────────
# _get_tracks_by_ids tests
# ──────────────────────────────────────────────


class TestGetTracksByIds:
    @pytest.mark.asyncio
    async def test_empty_ids_returns_empty_dict(self) -> None:
        """Empty track_ids list returns empty dict without querying."""
        session = AsyncMock()
        result = await _get_tracks_by_ids(session, [])
        assert result == {}
        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_tracks_by_id(self) -> None:
        """Returns dict mapping track_id to Track model."""
        tid1 = uuid.uuid4()
        tid2 = uuid.uuid4()

        track1 = _make_mock_track(tid1)
        track2 = _make_mock_track(tid2)

        session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [track1, track2]
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        result = await _get_tracks_by_ids(session, [tid1, tid2])

        assert len(result) == 2
        assert result[tid1] is track1
        assert result[tid2] is track2


# ──────────────────────────────────────────────
# run_vibe_lane tests
# ──────────────────────────────────────────────


class TestRunVibeLane:
    @pytest.mark.asyncio
    async def test_returns_ranked_vibe_matches(self) -> None:
        """Returns ranked VibeMatch results with correct track metadata."""
        tid1 = uuid.uuid4()
        tid2 = uuid.uuid4()

        pcm = _make_pcm_bytes(5.0)

        # Mock CLAP model inference
        mock_model = MagicMock()
        mock_processor = MagicMock()

        # Mock Qdrant client
        mock_qdrant = AsyncMock()
        mock_result = MagicMock()
        mock_result.points = [
            _make_mock_qdrant_point(tid1, score=0.90, chunk_index=0, offset_sec=0.0),
            _make_mock_qdrant_point(tid1, score=0.85, chunk_index=1, offset_sec=5.0),
            _make_mock_qdrant_point(tid1, score=0.80, chunk_index=2, offset_sec=10.0),
            _make_mock_qdrant_point(tid2, score=0.75, chunk_index=0, offset_sec=0.0),
            _make_mock_qdrant_point(tid2, score=0.70, chunk_index=1, offset_sec=5.0),
        ]
        mock_qdrant.query_points.return_value = mock_result

        # Mock DB session
        track1 = _make_mock_track(tid1)
        track2 = _make_mock_track(tid2)
        mock_session = AsyncMock()
        mock_db_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [track1, track2]
        mock_db_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_db_result

        with (
            patch(
                "app.search.vibe.generate_embedding",
                return_value=np.random.randn(512).astype(np.float32),
            ),
            patch("app.search.vibe.settings") as mock_settings,
        ):
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50
            mock_settings.vibe_match_threshold = 0.60
            mock_settings.embedding_model = "clap-htsat-large"

            results = await run_vibe_lane(
                pcm_48k=pcm,
                max_results=10,
                qdrant_client=mock_qdrant,
                clap_model=mock_model,
                clap_processor=mock_processor,
                session=mock_session,
            )

        assert len(results) == 2
        # First result should be tid1 (higher scores)
        assert results[0].track.id == tid1
        assert results[1].track.id == tid2
        assert results[0].similarity >= results[1].similarity
        assert results[0].embedding_model == "clap-htsat-large"

    @pytest.mark.asyncio
    async def test_embedding_model_not_loaded_raises(self) -> None:
        """Raises ValueError when CLAP model is not loaded."""
        pcm = _make_pcm_bytes(5.0)

        with pytest.raises(ValueError, match="CLAP model not loaded"):
            await run_vibe_lane(
                pcm_48k=pcm,
                max_results=10,
                qdrant_client=AsyncMock(),
                clap_model=None,
                clap_processor=MagicMock(),
                session=AsyncMock(),
            )

    @pytest.mark.asyncio
    async def test_embedding_processor_not_loaded_raises(self) -> None:
        """Raises ValueError when CLAP processor is not loaded."""
        pcm = _make_pcm_bytes(5.0)

        with pytest.raises(ValueError, match="CLAP model not loaded"):
            await run_vibe_lane(
                pcm_48k=pcm,
                max_results=10,
                qdrant_client=AsyncMock(),
                clap_model=MagicMock(),
                clap_processor=None,
                session=AsyncMock(),
            )

    @pytest.mark.asyncio
    async def test_empty_audio_returns_empty(self) -> None:
        """Empty PCM audio returns empty results."""
        results = await run_vibe_lane(
            pcm_48k=b"",
            max_results=10,
            qdrant_client=AsyncMock(),
            clap_model=MagicMock(),
            clap_processor=MagicMock(),
            session=AsyncMock(),
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_qdrant_failure_returns_empty(self) -> None:
        """Qdrant connection failure returns empty results gracefully."""
        pcm = _make_pcm_bytes(5.0)

        mock_qdrant = AsyncMock()
        mock_qdrant.query_points.side_effect = Exception("Connection refused")

        with (
            patch(
                "app.search.vibe.generate_embedding",
                return_value=np.random.randn(512).astype(np.float32),
            ),
            patch("app.search.vibe.settings") as mock_settings,
        ):
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50
            mock_settings.vibe_match_threshold = 0.60

            results = await run_vibe_lane(
                pcm_48k=pcm,
                max_results=10,
                qdrant_client=mock_qdrant,
                clap_model=MagicMock(),
                clap_processor=MagicMock(),
                session=AsyncMock(),
            )

        assert results == []

    @pytest.mark.asyncio
    async def test_below_threshold_filtering(self) -> None:
        """Results below vibe_match_threshold are filtered out."""
        tid = uuid.uuid4()
        pcm = _make_pcm_bytes(5.0)

        mock_qdrant = AsyncMock()
        mock_result = MagicMock()
        # All scores are low
        mock_result.points = [
            _make_mock_qdrant_point(tid, score=0.30, chunk_index=0, offset_sec=0.0),
            _make_mock_qdrant_point(tid, score=0.25, chunk_index=1, offset_sec=5.0),
        ]
        mock_qdrant.query_points.return_value = mock_result

        with (
            patch(
                "app.search.vibe.generate_embedding",
                return_value=np.random.randn(512).astype(np.float32),
            ),
            patch("app.search.vibe.settings") as mock_settings,
        ):
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50
            mock_settings.vibe_match_threshold = 0.60
            mock_settings.embedding_model = "clap-htsat-large"

            results = await run_vibe_lane(
                pcm_48k=pcm,
                max_results=10,
                qdrant_client=mock_qdrant,
                clap_model=MagicMock(),
                clap_processor=MagicMock(),
                session=AsyncMock(),
            )

        assert results == []

    @pytest.mark.asyncio
    async def test_exact_match_exclusion_in_vibe_lane(self) -> None:
        """Exact-match track is excluded from vibe results."""
        tid_exact = uuid.uuid4()
        tid_other = uuid.uuid4()
        pcm = _make_pcm_bytes(5.0)

        mock_qdrant = AsyncMock()
        mock_result = MagicMock()
        mock_result.points = [
            _make_mock_qdrant_point(tid_exact, score=0.95, chunk_index=0, offset_sec=0.0),
            _make_mock_qdrant_point(tid_other, score=0.80, chunk_index=0, offset_sec=0.0),
            _make_mock_qdrant_point(tid_other, score=0.75, chunk_index=1, offset_sec=5.0),
        ]
        mock_qdrant.query_points.return_value = mock_result

        track_other = _make_mock_track(tid_other)
        mock_session = AsyncMock()
        mock_db_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [track_other]
        mock_db_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_db_result

        with (
            patch(
                "app.search.vibe.generate_embedding",
                return_value=np.random.randn(512).astype(np.float32),
            ),
            patch("app.search.vibe.settings") as mock_settings,
        ):
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50
            mock_settings.vibe_match_threshold = 0.60
            mock_settings.embedding_model = "clap-htsat-large"

            results = await run_vibe_lane(
                pcm_48k=pcm,
                max_results=10,
                qdrant_client=mock_qdrant,
                clap_model=MagicMock(),
                clap_processor=MagicMock(),
                session=mock_session,
                exact_match_track_id=tid_exact,
            )

        assert len(results) == 1
        assert results[0].track.id == tid_other

    @pytest.mark.asyncio
    async def test_max_results_limits_output(self) -> None:
        """max_results parameter limits the number of returned matches."""
        tids = [uuid.uuid4() for _ in range(5)]
        pcm = _make_pcm_bytes(5.0)

        mock_qdrant = AsyncMock()
        mock_result = MagicMock()
        mock_result.points = [
            _make_mock_qdrant_point(tid, score=0.80 + i * 0.01) for i, tid in enumerate(tids)
        ]
        mock_qdrant.query_points.return_value = mock_result

        tracks = {tid: _make_mock_track(tid) for tid in tids}
        mock_session = AsyncMock()
        mock_db_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = list(tracks.values())
        mock_db_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_db_result

        with (
            patch(
                "app.search.vibe.generate_embedding",
                return_value=np.random.randn(512).astype(np.float32),
            ),
            patch("app.search.vibe.settings") as mock_settings,
        ):
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50
            mock_settings.vibe_match_threshold = 0.60
            mock_settings.embedding_model = "clap-htsat-large"

            results = await run_vibe_lane(
                pcm_48k=pcm,
                max_results=2,
                qdrant_client=mock_qdrant,
                clap_model=MagicMock(),
                clap_processor=MagicMock(),
                session=mock_session,
            )

        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_stale_qdrant_track_missing_from_postgres(self) -> None:
        """Track in Qdrant but missing from PostgreSQL is skipped gracefully."""
        tid_stale = uuid.uuid4()
        tid_valid = uuid.uuid4()
        pcm = _make_pcm_bytes(5.0)

        mock_qdrant = AsyncMock()
        mock_result = MagicMock()
        mock_result.points = [
            _make_mock_qdrant_point(tid_stale, score=0.90),
            _make_mock_qdrant_point(tid_valid, score=0.85),
        ]
        mock_qdrant.query_points.return_value = mock_result

        # Only tid_valid exists in PostgreSQL
        track_valid = _make_mock_track(tid_valid)
        mock_session = AsyncMock()
        mock_db_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [track_valid]
        mock_db_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_db_result

        with (
            patch(
                "app.search.vibe.generate_embedding",
                return_value=np.random.randn(512).astype(np.float32),
            ),
            patch("app.search.vibe.settings") as mock_settings,
        ):
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50
            mock_settings.vibe_match_threshold = 0.60
            mock_settings.embedding_model = "clap-htsat-large"

            results = await run_vibe_lane(
                pcm_48k=pcm,
                max_results=10,
                qdrant_client=mock_qdrant,
                clap_model=MagicMock(),
                clap_processor=MagicMock(),
                session=mock_session,
            )

        assert len(results) == 1
        assert results[0].track.id == tid_valid

    @pytest.mark.asyncio
    async def test_similarity_capped_at_one(self) -> None:
        """VibeMatch.similarity is capped at 1.0 even with diversity bonus."""
        tid = uuid.uuid4()
        pcm = _make_pcm_bytes(5.0)

        mock_qdrant = AsyncMock()
        mock_result = MagicMock()
        # Very high scores that with diversity bonus might exceed 1.0
        mock_result.points = [
            _make_mock_qdrant_point(tid, score=0.99, chunk_index=i, offset_sec=float(i * 5))
            for i in range(6)
        ]
        mock_qdrant.query_points.return_value = mock_result

        track = _make_mock_track(tid)
        mock_session = AsyncMock()
        mock_db_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [track]
        mock_db_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_db_result

        with (
            patch(
                "app.search.vibe.generate_embedding",
                return_value=np.random.randn(512).astype(np.float32),
            ),
            patch("app.search.vibe.settings") as mock_settings,
        ):
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50
            mock_settings.vibe_match_threshold = 0.60
            mock_settings.embedding_model = "clap-htsat-large"

            results = await run_vibe_lane(
                pcm_48k=pcm,
                max_results=10,
                qdrant_client=mock_qdrant,
                clap_model=MagicMock(),
                clap_processor=MagicMock(),
                session=mock_session,
            )

        assert len(results) == 1
        assert results[0].similarity <= 1.0

    @pytest.mark.asyncio
    async def test_empty_qdrant_collection_returns_empty(self) -> None:
        """Empty Qdrant collection returns empty results."""
        pcm = _make_pcm_bytes(5.0)

        mock_qdrant = AsyncMock()
        mock_result = MagicMock()
        mock_result.points = []
        mock_qdrant.query_points.return_value = mock_result

        with (
            patch(
                "app.search.vibe.generate_embedding",
                return_value=np.random.randn(512).astype(np.float32),
            ),
            patch("app.search.vibe.settings") as mock_settings,
        ):
            mock_settings.qdrant_collection_name = "audio_embeddings"
            mock_settings.qdrant_search_limit = 50
            mock_settings.vibe_match_threshold = 0.60

            results = await run_vibe_lane(
                pcm_48k=pcm,
                max_results=10,
                qdrant_client=mock_qdrant,
                clap_model=MagicMock(),
                clap_processor=MagicMock(),
                session=AsyncMock(),
            )

        assert results == []
