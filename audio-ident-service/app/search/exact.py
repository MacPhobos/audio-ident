"""Exact identification search lane using Olaf acoustic fingerprinting.

Takes 16kHz mono PCM audio, queries the Olaf LMDB inverted index for
fingerprint matches, applies consensus scoring (with sub-window strategy
for short clips), and returns ranked ExactMatch results with time offsets.

Olaf is wrapped as a CLI subprocess (NOT CFFI), so there are no GIL
blocking concerns. The existing ``olaf_query()`` function in
``app.audio.fingerprint`` is already async via ``asyncio.create_subprocess_exec``.
"""

from __future__ import annotations

import logging
import statistics
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio.fingerprint import OlafMatch, olaf_query
from app.db.session import async_session_factory
from app.models.track import Track
from app.schemas.search import ExactMatch, TrackInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_ALIGNED_HASHES = 8
"""Minimum number of aligned hashes for a match to be considered valid."""

STRONG_MATCH_HASHES = 20
"""Number of aligned hashes representing a strong (1.0 confidence) match."""

SHORT_CLIP_THRESHOLD_SEC = 5.0
"""Clips at or below this duration trigger sub-window querying."""

SUB_WINDOW_DURATION_SEC = 3.5
"""Duration of each sub-window in seconds."""

SUB_WINDOW_HOP_SEC = 0.75
"""Hop between sub-window start times in seconds."""

SUB_WINDOWS = [
    (0.0, 3.5),
    (0.75, 4.25),
    (1.5, 5.0),
]
"""Pre-computed (start, stop) pairs for the 3 overlapping sub-windows."""

OFFSET_TOLERANCE_SEC = 1.0
"""Tolerance for considering two offsets as matching the same position."""

SAMPLE_RATE = 16000
"""Expected sample rate for Olaf PCM input."""

BYTES_PER_SAMPLE = 4
"""32-bit float = 4 bytes per sample."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_exact_lane(
    pcm_16k: bytes,
    max_results: int = 10,
) -> list[ExactMatch]:
    """Search by audio fingerprint using Olaf's LMDB inverted index.

    For clips <= 5 seconds, applies the overlapping sub-window strategy:
    split into 3 windows, query each independently, and use consensus
    scoring for improved reliability.

    For clips > 5 seconds, queries the full clip directly.

    Args:
        pcm_16k: Raw 16kHz mono float32 little-endian PCM data.
        max_results: Maximum number of results to return.

    Returns:
        List of ExactMatch results sorted by confidence descending.
        Empty list if no matches meet the minimum threshold.
    """
    if not pcm_16k:
        return []

    clip_duration = _pcm_duration_sec(pcm_16k)
    logger.debug("Exact lane: clip duration=%.2fs, max_results=%d", clip_duration, max_results)

    if clip_duration <= SHORT_CLIP_THRESHOLD_SEC:
        scored_matches = await _query_with_subwindows(pcm_16k, clip_duration)
    else:
        scored_matches = await _query_full_clip(pcm_16k)

    # Filter by minimum aligned hash threshold
    filtered = [m for m in scored_matches if m.aligned_hashes >= MIN_ALIGNED_HASHES]

    if not filtered:
        return []

    # Normalize confidence and sort
    for m in filtered:
        m.confidence = _normalize_confidence(m.aligned_hashes)

    filtered.sort(key=lambda m: m.confidence, reverse=True)

    # Limit results
    top_matches = filtered[:max_results]

    # Look up track metadata from PostgreSQL
    return await _enrich_with_metadata(top_matches)


# ---------------------------------------------------------------------------
# Sub-window strategy
# ---------------------------------------------------------------------------


async def _query_with_subwindows(
    pcm_16k: bytes,
    clip_duration: float,
) -> list[_ScoredCandidate]:
    """Query using overlapping sub-windows for short clips.

    Splits the clip into 3 overlapping windows, queries each independently,
    and applies consensus scoring.

    Args:
        pcm_16k: Full clip PCM data (16kHz f32le).
        clip_duration: Duration of the clip in seconds.

    Returns:
        List of scored candidates after consensus.
    """
    window_results: list[list[OlafMatch]] = []

    for start_sec, stop_sec in SUB_WINDOWS:
        # Clamp window to actual clip duration
        actual_stop = min(stop_sec, clip_duration)
        if start_sec >= actual_stop:
            window_results.append([])
            continue

        window_pcm = _extract_pcm_window(pcm_16k, start_sec, actual_stop)
        if not window_pcm:
            window_results.append([])
            continue

        matches = await olaf_query(window_pcm)
        window_results.append(matches)

    return _consensus_score(window_results)


async def _query_full_clip(pcm_16k: bytes) -> list[_ScoredCandidate]:
    """Query the full clip directly (for clips > 5 seconds).

    Args:
        pcm_16k: Full clip PCM data (16kHz f32le).

    Returns:
        List of scored candidates from the single query.
    """
    matches = await olaf_query(pcm_16k)
    return _matches_to_candidates(matches)


# ---------------------------------------------------------------------------
# Consensus scoring
# ---------------------------------------------------------------------------


class _ScoredCandidate:
    """Internal candidate representation before final ExactMatch construction.

    Mutable so we can update confidence after consensus scoring.
    """

    __slots__ = ("aligned_hashes", "confidence", "offset_seconds", "track_uuid")

    def __init__(
        self,
        track_uuid: uuid.UUID,
        aligned_hashes: int,
        offset_seconds: float | None,
        confidence: float = 0.0,
    ) -> None:
        self.track_uuid = track_uuid
        self.aligned_hashes = aligned_hashes
        self.offset_seconds = offset_seconds
        self.confidence = confidence


def _consensus_score(
    window_results: list[list[OlafMatch]],
) -> list[_ScoredCandidate]:
    """Apply consensus scoring across sub-window results.

    Logic:
      - Group matches by track UUID across all windows.
      - 2+ windows matching same track -> HIGH confidence (sum aligned hashes).
      - 1 window only -> LOW confidence (keep but halve aligned hashes).
      - Different tracks across windows with no majority -> keep strongest only.

    Offset reconciliation: use median of reconciled offsets from matching windows.

    Args:
        window_results: List of OlafMatch lists, one per sub-window.

    Returns:
        List of scored candidates after consensus.
    """
    # Collect per-track data across windows
    track_windows: dict[str, list[tuple[int, OlafMatch]]] = defaultdict(list)

    for window_idx, matches in enumerate(window_results):
        for match in matches:
            ref_path = match.reference_path.strip()
            track_windows[ref_path].append((window_idx, match))

    candidates: list[_ScoredCandidate] = []

    for ref_path, window_matches in track_windows.items():
        try:
            track_uuid = uuid.UUID(ref_path)
        except ValueError:
            logger.warning("Non-UUID reference_path from Olaf: %s", ref_path)
            continue

        unique_windows = {wm[0] for wm in window_matches}
        num_windows = len(unique_windows)

        # Compute total aligned hashes across windows
        total_hashes = sum(m.match_count for _, m in window_matches)

        # Reconcile offsets: adjust for sub-window start time, then take median
        reconciled_offsets: list[float] = []
        for _window_idx, match in window_matches:
            # The reference_start is the offset in the original track.
            # Each window queries a different slice of the clip, but the
            # reference_start already reflects the position in the indexed track.
            reconciled_offsets.append(match.reference_start)

        offset = statistics.median(reconciled_offsets) if reconciled_offsets else None

        if num_windows >= 2:
            # HIGH confidence: multiple windows agree
            candidates.append(
                _ScoredCandidate(
                    track_uuid=track_uuid,
                    aligned_hashes=total_hashes,
                    offset_seconds=offset,
                )
            )
        else:
            # LOW confidence: only one window matched
            # Halve the aligned hashes to penalize single-window matches
            penalized_hashes = max(total_hashes // 2, 1)
            candidates.append(
                _ScoredCandidate(
                    track_uuid=track_uuid,
                    aligned_hashes=penalized_hashes,
                    offset_seconds=offset,
                )
            )

    return candidates


def _matches_to_candidates(matches: list[OlafMatch]) -> list[_ScoredCandidate]:
    """Convert raw OlafMatch results to scored candidates (full-clip mode).

    Groups matches by track UUID and aggregates aligned hashes.

    Args:
        matches: Raw results from olaf_query.

    Returns:
        List of scored candidates.
    """
    track_data: dict[str, list[OlafMatch]] = defaultdict(list)
    for match in matches:
        ref_path = match.reference_path.strip()
        track_data[ref_path].append(match)

    candidates: list[_ScoredCandidate] = []
    for ref_path, track_matches in track_data.items():
        try:
            track_uuid = uuid.UUID(ref_path)
        except ValueError:
            logger.warning("Non-UUID reference_path from Olaf: %s", ref_path)
            continue

        total_hashes = sum(m.match_count for m in track_matches)
        offsets = [m.reference_start for m in track_matches]
        offset = statistics.median(offsets) if offsets else None

        candidates.append(
            _ScoredCandidate(
                track_uuid=track_uuid,
                aligned_hashes=total_hashes,
                offset_seconds=offset,
            )
        )

    return candidates


# ---------------------------------------------------------------------------
# Confidence normalization
# ---------------------------------------------------------------------------


def _normalize_confidence(aligned_hashes: int) -> float:
    """Normalize aligned hash count to a 0.0-1.0 confidence score.

    Formula: min(aligned_hashes / STRONG_MATCH_HASHES, 1.0)

    Args:
        aligned_hashes: Number of aligned fingerprint hashes.

    Returns:
        Confidence score clamped to [0.0, 1.0].
    """
    if aligned_hashes <= 0:
        return 0.0
    return min(aligned_hashes / STRONG_MATCH_HASHES, 1.0)


# ---------------------------------------------------------------------------
# PCM utilities
# ---------------------------------------------------------------------------


def _pcm_duration_sec(pcm_data: bytes) -> float:
    """Calculate the duration of f32le PCM data at 16kHz.

    Args:
        pcm_data: Raw 16kHz mono float32 little-endian PCM.

    Returns:
        Duration in seconds.
    """
    num_samples = len(pcm_data) // BYTES_PER_SAMPLE
    return num_samples / SAMPLE_RATE


def _extract_pcm_window(
    pcm_data: bytes,
    start_sec: float,
    stop_sec: float,
) -> bytes:
    """Extract a time window from f32le PCM data.

    Args:
        pcm_data: Full clip PCM data (16kHz f32le).
        start_sec: Window start time in seconds.
        stop_sec: Window end time in seconds.

    Returns:
        PCM bytes for the requested window. May be empty if out of range.
    """
    start_sample = int(start_sec * SAMPLE_RATE)
    stop_sample = int(stop_sec * SAMPLE_RATE)

    start_byte = start_sample * BYTES_PER_SAMPLE
    stop_byte = stop_sample * BYTES_PER_SAMPLE

    # Clamp to actual data size
    start_byte = max(0, min(start_byte, len(pcm_data)))
    stop_byte = max(start_byte, min(stop_byte, len(pcm_data)))

    return pcm_data[start_byte:stop_byte]


# ---------------------------------------------------------------------------
# Track metadata lookup
# ---------------------------------------------------------------------------


async def get_tracks_by_ids(
    session: AsyncSession,
    track_ids: list[uuid.UUID],
) -> dict[uuid.UUID, Track]:
    """Fetch Track records from PostgreSQL by their IDs.

    Args:
        session: Async SQLAlchemy session.
        track_ids: List of track UUIDs to look up.

    Returns:
        Dict mapping track UUID to Track ORM instance.
    """
    if not track_ids:
        return {}

    stmt = select(Track).where(Track.id.in_(track_ids))
    result = await session.execute(stmt)
    return {t.id: t for t in result.scalars().all()}


def _track_to_info(track: Track) -> TrackInfo:
    """Convert a Track ORM model to a TrackInfo schema.

    Args:
        track: SQLAlchemy Track instance.

    Returns:
        Pydantic TrackInfo schema.
    """
    return TrackInfo(
        id=track.id,
        title=track.title,
        artist=track.artist,
        album=track.album,
        duration_seconds=track.duration_seconds,
        ingested_at=track.ingested_at,
    )


async def _enrich_with_metadata(
    candidates: list[_ScoredCandidate],
) -> list[ExactMatch]:
    """Look up track metadata from PostgreSQL and build ExactMatch responses.

    Candidates whose track UUID is not found in the database are silently
    dropped (they may have been deleted between fingerprint indexing and
    this query).

    Args:
        candidates: Scored candidates with track UUIDs.

    Returns:
        List of ExactMatch responses with full track metadata.
    """
    if not candidates:
        return []

    track_ids = [c.track_uuid for c in candidates]

    async with async_session_factory() as session:
        tracks_by_id = await get_tracks_by_ids(session, track_ids)

    results: list[ExactMatch] = []
    for candidate in candidates:
        track = tracks_by_id.get(candidate.track_uuid)
        if track is None:
            logger.warning(
                "Track %s not found in database, skipping exact match result",
                candidate.track_uuid,
            )
            continue

        results.append(
            ExactMatch(
                track=_track_to_info(track),
                confidence=candidate.confidence,
                offset_seconds=candidate.offset_seconds,
                aligned_hashes=candidate.aligned_hashes,
            )
        )

    return results
