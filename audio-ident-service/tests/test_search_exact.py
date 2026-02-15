"""Tests for the exact identification search lane (app.search.exact)."""

from __future__ import annotations

import struct
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.audio.fingerprint import OlafMatch
from app.search.exact import (
    BYTES_PER_SAMPLE,
    MIN_ALIGNED_HASHES,
    SAMPLE_RATE,
    STRONG_MATCH_HASHES,
    _consensus_score,
    _extract_pcm_window,
    _matches_to_candidates,
    _normalize_confidence,
    _pcm_duration_sec,
    run_exact_lane,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TRACK_UUID_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TRACK_UUID_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
TRACK_UUID_C = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")

FIXED_DATETIME = datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)


def _make_pcm(duration_sec: float, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Create silent f32le PCM data of the given duration."""
    num_samples = int(duration_sec * sample_rate)
    return struct.pack(f"<{num_samples}f", *([0.0] * num_samples))


def _make_olaf_match(
    match_count: int = 25,
    reference_path: str = str(TRACK_UUID_A),
    reference_start: float = 10.0,
    reference_stop: float = 15.0,
    query_start: float = 0.0,
    query_stop: float = 5.0,
    reference_id: int = 1001,
) -> OlafMatch:
    """Create a test OlafMatch with sensible defaults."""
    return OlafMatch(
        match_count=match_count,
        query_start=query_start,
        query_stop=query_stop,
        reference_path=reference_path,
        reference_id=reference_id,
        reference_start=reference_start,
        reference_stop=reference_stop,
    )


def _make_mock_track(
    track_id: uuid.UUID = TRACK_UUID_A,
    title: str = "Test Track",
    artist: str | None = "Test Artist",
    album: str | None = "Test Album",
    duration_seconds: float = 180.0,
) -> MagicMock:
    """Create a mock Track ORM object."""
    track = MagicMock()
    track.id = track_id
    track.title = title
    track.artist = artist
    track.album = album
    track.duration_seconds = duration_seconds
    track.ingested_at = FIXED_DATETIME
    return track


# ---------------------------------------------------------------------------
# PCM utility tests
# ---------------------------------------------------------------------------


class TestPcmDuration:
    def test_duration_of_1_second(self) -> None:
        pcm = _make_pcm(1.0)
        assert _pcm_duration_sec(pcm) == pytest.approx(1.0)

    def test_duration_of_5_seconds(self) -> None:
        pcm = _make_pcm(5.0)
        assert _pcm_duration_sec(pcm) == pytest.approx(5.0)

    def test_duration_of_10_seconds(self) -> None:
        pcm = _make_pcm(10.0)
        assert _pcm_duration_sec(pcm) == pytest.approx(10.0)

    def test_empty_pcm(self) -> None:
        assert _pcm_duration_sec(b"") == 0.0


class TestExtractPcmWindow:
    def test_extract_first_half(self) -> None:
        pcm = _make_pcm(4.0)
        window = _extract_pcm_window(pcm, 0.0, 2.0)
        expected_samples = int(2.0 * SAMPLE_RATE)
        assert len(window) == expected_samples * BYTES_PER_SAMPLE

    def test_extract_middle(self) -> None:
        pcm = _make_pcm(5.0)
        window = _extract_pcm_window(pcm, 1.0, 3.0)
        expected_samples = int(2.0 * SAMPLE_RATE)
        assert len(window) == expected_samples * BYTES_PER_SAMPLE

    def test_extract_clamped_to_end(self) -> None:
        pcm = _make_pcm(3.0)
        window = _extract_pcm_window(pcm, 2.0, 5.0)
        # Should get only 1 second (from 2.0 to 3.0)
        expected_samples = int(1.0 * SAMPLE_RATE)
        assert len(window) == expected_samples * BYTES_PER_SAMPLE

    def test_extract_beyond_data_returns_empty(self) -> None:
        pcm = _make_pcm(2.0)
        window = _extract_pcm_window(pcm, 3.0, 5.0)
        assert len(window) == 0

    def test_extract_empty_pcm(self) -> None:
        window = _extract_pcm_window(b"", 0.0, 1.0)
        assert len(window) == 0


# ---------------------------------------------------------------------------
# Confidence normalization tests
# ---------------------------------------------------------------------------


class TestNormalizeConfidence:
    def test_zero_hashes(self) -> None:
        assert _normalize_confidence(0) == 0.0

    def test_negative_hashes(self) -> None:
        assert _normalize_confidence(-5) == 0.0

    def test_below_strong_threshold(self) -> None:
        # 10 / 20 = 0.5
        assert _normalize_confidence(10) == pytest.approx(0.5)

    def test_at_strong_threshold(self) -> None:
        assert _normalize_confidence(STRONG_MATCH_HASHES) == pytest.approx(1.0)

    def test_above_strong_threshold_capped(self) -> None:
        assert _normalize_confidence(40) == pytest.approx(1.0)

    def test_minimum_viable(self) -> None:
        # MIN_ALIGNED_HASHES (8) / 20 = 0.4
        assert _normalize_confidence(MIN_ALIGNED_HASHES) == pytest.approx(0.4)

    def test_just_below_min(self) -> None:
        # 7 / 20 = 0.35
        assert _normalize_confidence(7) == pytest.approx(0.35)

    def test_confidence_always_between_0_and_1(self) -> None:
        for h in range(0, 100):
            c = _normalize_confidence(h)
            assert 0.0 <= c <= 1.0


# ---------------------------------------------------------------------------
# Matches to candidates (full-clip mode)
# ---------------------------------------------------------------------------


class TestMatchesToCandidates:
    def test_single_match(self) -> None:
        matches = [_make_olaf_match(match_count=25)]
        candidates = _matches_to_candidates(matches)
        assert len(candidates) == 1
        assert candidates[0].track_uuid == TRACK_UUID_A
        assert candidates[0].aligned_hashes == 25

    def test_multiple_matches_same_track_aggregated(self) -> None:
        matches = [
            _make_olaf_match(match_count=15, reference_start=10.0),
            _make_olaf_match(match_count=10, reference_start=10.5),
        ]
        candidates = _matches_to_candidates(matches)
        assert len(candidates) == 1
        assert candidates[0].aligned_hashes == 25  # 15 + 10

    def test_multiple_tracks(self) -> None:
        matches = [
            _make_olaf_match(match_count=20, reference_path=str(TRACK_UUID_A)),
            _make_olaf_match(match_count=15, reference_path=str(TRACK_UUID_B)),
        ]
        candidates = _matches_to_candidates(matches)
        assert len(candidates) == 2
        uuids = {c.track_uuid for c in candidates}
        assert uuids == {TRACK_UUID_A, TRACK_UUID_B}

    def test_non_uuid_reference_path_skipped(self) -> None:
        matches = [
            _make_olaf_match(match_count=20, reference_path="not-a-uuid"),
        ]
        candidates = _matches_to_candidates(matches)
        assert len(candidates) == 0

    def test_empty_matches(self) -> None:
        assert _matches_to_candidates([]) == []

    def test_offset_is_median(self) -> None:
        matches = [
            _make_olaf_match(match_count=10, reference_start=8.0),
            _make_olaf_match(match_count=10, reference_start=12.0),
            _make_olaf_match(match_count=10, reference_start=10.0),
        ]
        candidates = _matches_to_candidates(matches)
        assert len(candidates) == 1
        assert candidates[0].offset_seconds == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Consensus scoring (sub-window mode)
# ---------------------------------------------------------------------------


class TestConsensusScore:
    def test_two_windows_same_track_high_confidence(self) -> None:
        """2+ windows matching same track -> sum hashes (no penalty)."""
        window_results: list[list[OlafMatch]] = [
            [_make_olaf_match(match_count=12, reference_path=str(TRACK_UUID_A))],
            [_make_olaf_match(match_count=10, reference_path=str(TRACK_UUID_A))],
            [],
        ]
        candidates = _consensus_score(window_results)
        assert len(candidates) == 1
        assert candidates[0].track_uuid == TRACK_UUID_A
        # Sum of hashes from both windows: 12 + 10 = 22
        assert candidates[0].aligned_hashes == 22

    def test_three_windows_same_track(self) -> None:
        """All 3 windows match same track -> full hash sum."""
        window_results = [
            [_make_olaf_match(match_count=10, reference_path=str(TRACK_UUID_A))],
            [_make_olaf_match(match_count=8, reference_path=str(TRACK_UUID_A))],
            [_make_olaf_match(match_count=12, reference_path=str(TRACK_UUID_A))],
        ]
        candidates = _consensus_score(window_results)
        assert len(candidates) == 1
        assert candidates[0].aligned_hashes == 30  # 10 + 8 + 12

    def test_single_window_match_penalized(self) -> None:
        """1 window only -> halved aligned hashes (penalty)."""
        window_results: list[list[OlafMatch]] = [
            [_make_olaf_match(match_count=20, reference_path=str(TRACK_UUID_A))],
            [],
            [],
        ]
        candidates = _consensus_score(window_results)
        assert len(candidates) == 1
        assert candidates[0].track_uuid == TRACK_UUID_A
        # Penalized: 20 // 2 = 10
        assert candidates[0].aligned_hashes == 10

    def test_different_tracks_across_windows(self) -> None:
        """Different tracks in different windows -> both kept as separate candidates."""
        window_results: list[list[OlafMatch]] = [
            [_make_olaf_match(match_count=15, reference_path=str(TRACK_UUID_A))],
            [_make_olaf_match(match_count=12, reference_path=str(TRACK_UUID_B))],
            [],
        ]
        candidates = _consensus_score(window_results)
        assert len(candidates) == 2
        # Both are single-window matches -> penalized
        uuids = {c.track_uuid for c in candidates}
        assert uuids == {TRACK_UUID_A, TRACK_UUID_B}

    def test_empty_all_windows(self) -> None:
        window_results: list[list[OlafMatch]] = [[], [], []]
        candidates = _consensus_score(window_results)
        assert candidates == []

    def test_offset_reconciliation_median(self) -> None:
        """Offset should be median of reference_start values from matching windows."""
        window_results = [
            [_make_olaf_match(match_count=10, reference_start=9.0)],
            [_make_olaf_match(match_count=10, reference_start=11.0)],
            [_make_olaf_match(match_count=10, reference_start=10.0)],
        ]
        candidates = _consensus_score(window_results)
        assert len(candidates) == 1
        # median of [9.0, 11.0, 10.0] = 10.0
        assert candidates[0].offset_seconds == pytest.approx(10.0)

    def test_single_window_penalty_minimum_1(self) -> None:
        """Penalized hashes should be at least 1."""
        window_results: list[list[OlafMatch]] = [
            [_make_olaf_match(match_count=1, reference_path=str(TRACK_UUID_A))],
            [],
            [],
        ]
        candidates = _consensus_score(window_results)
        assert len(candidates) == 1
        # 1 // 2 = 0, but max(0, 1) = 1
        assert candidates[0].aligned_hashes == 1


# ---------------------------------------------------------------------------
# run_exact_lane integration tests (with mocks)
# ---------------------------------------------------------------------------


class TestRunExactLaneKnownMatch:
    """Test: Known match -- Olaf returns matches above threshold."""

    async def test_returns_correct_match(self) -> None:
        pcm = _make_pcm(10.0)  # >5s, so full-clip mode
        mock_track = _make_mock_track(track_id=TRACK_UUID_A)

        olaf_matches = [
            _make_olaf_match(
                match_count=25,
                reference_path=str(TRACK_UUID_A),
                reference_start=30.0,
            ),
        ]

        with (
            patch("app.search.exact.olaf_query", new_callable=AsyncMock, return_value=olaf_matches),
            patch("app.search.exact.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_track]
            mock_session.execute.return_value = mock_result
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await run_exact_lane(pcm, max_results=5)

        assert len(results) == 1
        assert results[0].track.id == TRACK_UUID_A
        assert results[0].confidence == pytest.approx(1.0)  # 25/20 capped to 1.0
        assert results[0].aligned_hashes == 25
        assert results[0].offset_seconds == pytest.approx(30.0)


class TestRunExactLaneKnownNonMatch:
    """Test: Known non-match -- Olaf returns empty."""

    async def test_returns_empty_when_no_matches(self) -> None:
        pcm = _make_pcm(10.0)

        with patch("app.search.exact.olaf_query", new_callable=AsyncMock, return_value=[]):
            results = await run_exact_lane(pcm, max_results=5)

        assert results == []


class TestRunExactLaneShortClipSubwindow:
    """Test: Short clip (5s) sub-window consensus."""

    async def test_subwindow_consensus_two_windows_agree(self) -> None:
        pcm = _make_pcm(5.0)  # Exactly 5s -> sub-window mode
        mock_track = _make_mock_track(track_id=TRACK_UUID_A)

        async def mock_olaf_query(pcm_data: bytes) -> list[OlafMatch]:
            """Return matches for first two windows, empty for third."""
            data_len = len(pcm_data)
            # All non-empty windows return matches for same track
            if data_len > 0:
                return [
                    _make_olaf_match(
                        match_count=12,
                        reference_path=str(TRACK_UUID_A),
                        reference_start=10.0,
                    ),
                ]
            return []

        # Since sub-windowing calls olaf_query 3 times, we use side_effect
        call_count = 0

        async def side_effect_query(pcm_data: bytes) -> list[OlafMatch]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return [
                    _make_olaf_match(
                        match_count=12,
                        reference_path=str(TRACK_UUID_A),
                        reference_start=10.0,
                    ),
                ]
            return []

        with (
            patch("app.search.exact.olaf_query", side_effect=side_effect_query),
            patch("app.search.exact.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_track]
            mock_session.execute.return_value = mock_result
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await run_exact_lane(pcm, max_results=5)

        assert len(results) == 1
        # 2 windows agreed -> no penalty, total hashes = 12 + 12 = 24
        assert results[0].aligned_hashes == 24
        assert results[0].confidence == pytest.approx(1.0)  # 24/20 capped to 1.0


class TestRunExactLaneMultipleMatchesSorting:
    """Test: Multiple matches with different confidence, verify sorting."""

    async def test_sorted_by_confidence_descending(self) -> None:
        pcm = _make_pcm(10.0)
        mock_track_a = _make_mock_track(track_id=TRACK_UUID_A, title="Track A")
        mock_track_b = _make_mock_track(track_id=TRACK_UUID_B, title="Track B")
        mock_track_c = _make_mock_track(track_id=TRACK_UUID_C, title="Track C")

        olaf_matches = [
            _make_olaf_match(
                match_count=10,
                reference_path=str(TRACK_UUID_A),
                reference_start=5.0,
            ),
            _make_olaf_match(
                match_count=25,
                reference_path=str(TRACK_UUID_B),
                reference_start=15.0,
            ),
            _make_olaf_match(
                match_count=15,
                reference_path=str(TRACK_UUID_C),
                reference_start=25.0,
            ),
        ]

        with (
            patch("app.search.exact.olaf_query", new_callable=AsyncMock, return_value=olaf_matches),
            patch("app.search.exact.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [
                mock_track_a,
                mock_track_b,
                mock_track_c,
            ]
            mock_session.execute.return_value = mock_result
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await run_exact_lane(pcm, max_results=10)

        assert len(results) == 3
        # Sorted by confidence: B(25->1.0), C(15->0.75), A(10->0.5)
        assert results[0].track.title == "Track B"
        assert results[0].confidence == pytest.approx(1.0)
        assert results[1].track.title == "Track C"
        assert results[1].confidence == pytest.approx(0.75)
        assert results[2].track.title == "Track A"
        assert results[2].confidence == pytest.approx(0.5)


class TestRunExactLaneOffsetAccuracy:
    """Test: Offset accuracy -- returned offset within tolerance of true offset."""

    async def test_offset_within_tolerance(self) -> None:
        pcm = _make_pcm(10.0)
        true_offset = 42.5
        mock_track = _make_mock_track(track_id=TRACK_UUID_A)

        olaf_matches = [
            _make_olaf_match(
                match_count=20,
                reference_path=str(TRACK_UUID_A),
                reference_start=true_offset,
            ),
        ]

        with (
            patch("app.search.exact.olaf_query", new_callable=AsyncMock, return_value=olaf_matches),
            patch("app.search.exact.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_track]
            mock_session.execute.return_value = mock_result
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await run_exact_lane(pcm, max_results=5)

        assert len(results) == 1
        assert results[0].offset_seconds is not None
        assert abs(results[0].offset_seconds - true_offset) < 0.5


class TestRunExactLaneBelowThreshold:
    """Test: Below threshold -- matches with < MIN_ALIGNED_HASHES are discarded."""

    async def test_below_threshold_discarded(self) -> None:
        pcm = _make_pcm(10.0)

        olaf_matches = [
            _make_olaf_match(
                match_count=5,  # Below MIN_ALIGNED_HASHES (8)
                reference_path=str(TRACK_UUID_A),
            ),
            _make_olaf_match(
                match_count=3,  # Also below threshold
                reference_path=str(TRACK_UUID_B),
            ),
        ]

        with patch(
            "app.search.exact.olaf_query", new_callable=AsyncMock, return_value=olaf_matches
        ):
            results = await run_exact_lane(pcm, max_results=5)

        assert results == []

    async def test_mix_above_and_below_threshold(self) -> None:
        """Only matches above threshold are returned."""
        pcm = _make_pcm(10.0)
        mock_track = _make_mock_track(track_id=TRACK_UUID_B)

        olaf_matches = [
            _make_olaf_match(
                match_count=5,  # Below threshold
                reference_path=str(TRACK_UUID_A),
            ),
            _make_olaf_match(
                match_count=20,  # Above threshold
                reference_path=str(TRACK_UUID_B),
            ),
        ]

        with (
            patch("app.search.exact.olaf_query", new_callable=AsyncMock, return_value=olaf_matches),
            patch("app.search.exact.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_track]
            mock_session.execute.return_value = mock_result
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await run_exact_lane(pcm, max_results=5)

        assert len(results) == 1
        assert results[0].track.id == TRACK_UUID_B


class TestRunExactLaneEmptyIndex:
    """Test: Empty LMDB index -- Olaf returns empty, no crash."""

    async def test_empty_index_returns_empty(self) -> None:
        pcm = _make_pcm(10.0)

        with patch("app.search.exact.olaf_query", new_callable=AsyncMock, return_value=[]):
            results = await run_exact_lane(pcm, max_results=5)

        assert results == []

    async def test_empty_pcm_returns_empty(self) -> None:
        results = await run_exact_lane(b"", max_results=5)
        assert results == []


class TestRunExactLaneVeryShortClip:
    """Test: Very short clip (<3s) -- degrades gracefully."""

    async def test_very_short_clip_still_queries(self) -> None:
        """A 2s clip is below 5s threshold, so sub-windows are used.
        Most sub-windows will be clamped or empty, but it should not crash."""
        pcm = _make_pcm(2.0)

        with patch(
            "app.search.exact.olaf_query", new_callable=AsyncMock, return_value=[]
        ) as mock_query:
            results = await run_exact_lane(pcm, max_results=5)

        assert results == []
        # olaf_query should have been called for windows that overlap with the 2s clip
        # Window 1: 0.0-3.5 -> clamped to 0.0-2.0 -> valid
        # Window 2: 0.75-4.25 -> clamped to 0.75-2.0 -> valid
        # Window 3: 1.5-5.0 -> clamped to 1.5-2.0 -> valid but short
        assert mock_query.call_count == 3

    async def test_1_second_clip(self) -> None:
        """A 1s clip has very limited sub-windows, should not crash."""
        pcm = _make_pcm(1.0)

        with patch("app.search.exact.olaf_query", new_callable=AsyncMock, return_value=[]):
            results = await run_exact_lane(pcm, max_results=5)

        assert results == []

    async def test_very_short_clip_with_match(self) -> None:
        """Even a very short clip can produce a match if Olaf finds enough hashes."""
        pcm = _make_pcm(2.0)
        mock_track = _make_mock_track(track_id=TRACK_UUID_A)

        async def side_effect_query(pcm_data: bytes) -> list[OlafMatch]:
            if len(pcm_data) > 0:
                return [
                    _make_olaf_match(
                        match_count=10,
                        reference_path=str(TRACK_UUID_A),
                        reference_start=5.0,
                    ),
                ]
            return []

        with (
            patch("app.search.exact.olaf_query", side_effect=side_effect_query),
            patch("app.search.exact.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_track]
            mock_session.execute.return_value = mock_result
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await run_exact_lane(pcm, max_results=5)

        # 3 windows all match -> sum = 30, no penalty (3 unique windows)
        assert len(results) == 1
        assert results[0].aligned_hashes == 30
        assert results[0].confidence == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Additional edge case tests
# ---------------------------------------------------------------------------


class TestRunExactLaneMaxResults:
    """Test: max_results parameter limits output."""

    async def test_max_results_limits_output(self) -> None:
        pcm = _make_pcm(10.0)
        mock_tracks = [
            _make_mock_track(track_id=TRACK_UUID_A, title="A"),
            _make_mock_track(track_id=TRACK_UUID_B, title="B"),
            _make_mock_track(track_id=TRACK_UUID_C, title="C"),
        ]

        olaf_matches = [
            _make_olaf_match(match_count=25, reference_path=str(TRACK_UUID_A)),
            _make_olaf_match(match_count=20, reference_path=str(TRACK_UUID_B)),
            _make_olaf_match(match_count=15, reference_path=str(TRACK_UUID_C)),
        ]

        with (
            patch("app.search.exact.olaf_query", new_callable=AsyncMock, return_value=olaf_matches),
            patch("app.search.exact.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = mock_tracks
            mock_session.execute.return_value = mock_result
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await run_exact_lane(pcm, max_results=2)

        assert len(results) == 2
        # Top 2 by confidence
        assert results[0].track.title == "A"
        assert results[1].track.title == "B"


class TestRunExactLaneTrackNotInDatabase:
    """Test: Track found by Olaf but not in PostgreSQL (deleted after indexing)."""

    async def test_missing_track_skipped(self) -> None:
        pcm = _make_pcm(10.0)

        olaf_matches = [
            _make_olaf_match(match_count=25, reference_path=str(TRACK_UUID_A)),
        ]

        with (
            patch("app.search.exact.olaf_query", new_callable=AsyncMock, return_value=olaf_matches),
            patch("app.search.exact.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            # Track not in database
            mock_result.scalars.return_value.all.return_value = []
            mock_session.execute.return_value = mock_result
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await run_exact_lane(pcm, max_results=5)

        assert results == []


class TestRunExactLaneSubwindowSingleWindowOnly:
    """Test: Sub-window mode where only 1 window matches (LOW confidence)."""

    async def test_single_window_match_is_penalized(self) -> None:
        pcm = _make_pcm(5.0)
        mock_track = _make_mock_track(track_id=TRACK_UUID_A)

        call_count = 0

        async def side_effect_query(pcm_data: bytes) -> list[OlafMatch]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [
                    _make_olaf_match(
                        match_count=20,
                        reference_path=str(TRACK_UUID_A),
                        reference_start=10.0,
                    ),
                ]
            return []

        with (
            patch("app.search.exact.olaf_query", side_effect=side_effect_query),
            patch("app.search.exact.async_session_factory") as mock_session_factory,
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_track]
            mock_session.execute.return_value = mock_result
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await run_exact_lane(pcm, max_results=5)

        assert len(results) == 1
        # Penalized: 20 // 2 = 10
        assert results[0].aligned_hashes == 10
        assert results[0].confidence == pytest.approx(0.5)  # 10 / 20


class TestRunExactLaneFullClipVsSubwindow:
    """Test: Clips >5s use full-clip mode, clips <=5s use sub-window mode."""

    async def test_6s_clip_uses_full_clip_mode(self) -> None:
        pcm = _make_pcm(6.0)

        with patch(
            "app.search.exact.olaf_query", new_callable=AsyncMock, return_value=[]
        ) as mock_query:
            await run_exact_lane(pcm, max_results=5)

        # Full-clip mode: olaf_query called exactly once
        assert mock_query.call_count == 1

    async def test_5s_clip_uses_subwindow_mode(self) -> None:
        pcm = _make_pcm(5.0)

        with patch(
            "app.search.exact.olaf_query", new_callable=AsyncMock, return_value=[]
        ) as mock_query:
            await run_exact_lane(pcm, max_results=5)

        # Sub-window mode: olaf_query called 3 times (once per window)
        assert mock_query.call_count == 3

    async def test_4s_clip_uses_subwindow_mode(self) -> None:
        pcm = _make_pcm(4.0)

        with patch(
            "app.search.exact.olaf_query", new_callable=AsyncMock, return_value=[]
        ) as mock_query:
            await run_exact_lane(pcm, max_results=5)

        assert mock_query.call_count == 3
