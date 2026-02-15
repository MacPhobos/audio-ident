"""Chunk-to-track aggregation for Qdrant search results.

Implements Top-K Average with Diversity Bonus scoring to convert
chunk-level similarity scores into track-level rankings.

Algorithm:
1. Group chunks by track_id
2. For each track: base_score = mean of top-K chunk scores
3. Diversity bonus: reward tracks matching at multiple offsets
   bonus = min(unique_offsets / 5.0, 1.0) * diversity_weight
4. Exclude exact-match track if specified
5. Sort by final_score descending
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkHit:
    """A single chunk hit from Qdrant similarity search.

    Attributes:
        track_id: UUID of the track this chunk belongs to.
        score: Cosine similarity score from Qdrant (0.0 to 1.0).
        chunk_index: Sequential chunk index within the track.
        offset_sec: Chunk start time within the track.
    """

    track_id: uuid.UUID
    score: float
    chunk_index: int
    offset_sec: float


@dataclass(frozen=True)
class TrackResult:
    """Aggregated track-level score from chunk hits.

    Attributes:
        track_id: UUID of the track.
        final_score: Combined base score + diversity bonus.
        base_score: Mean of top-K chunk scores.
        diversity_bonus: Bonus for matching at multiple offsets.
        chunk_count: Total number of matching chunks for this track.
        top_chunk_scores: The top-K scores used for base_score calculation.
    """

    track_id: uuid.UUID
    final_score: float
    base_score: float
    diversity_bonus: float
    chunk_count: int
    top_chunk_scores: list[float]


def aggregate_chunk_hits(
    chunk_hits: list[ChunkHit],
    top_k_per_track: int = 3,
    diversity_weight: float = 0.05,
    exact_match_track_id: uuid.UUID | None = None,
) -> list[TrackResult]:
    """Aggregate chunk-level Qdrant hits into track-level scores.

    Uses Top-K Average with Diversity Bonus:
    - base_score = mean of top-K chunk scores per track
    - diversity_bonus = min(unique_offsets / 5.0, 1.0) * diversity_weight
    - final_score = base_score + diversity_bonus

    Args:
        chunk_hits: Raw chunk hits from Qdrant similarity search.
        top_k_per_track: Number of top chunk scores to average (default 3).
        diversity_weight: Weight for the diversity bonus (default 0.05).
        exact_match_track_id: If set, exclude this track from results.
            Prevents "you searched for X, we found X" results. This parameter
            is optional (None = no exclusion) to keep Phase 4b independently
            testable without Phase 4a.

    Returns:
        List of TrackResult sorted by final_score descending.
    """
    if not chunk_hits:
        return []

    # 1. Group chunks by track_id
    track_chunks: dict[uuid.UUID, list[ChunkHit]] = defaultdict(list)
    for hit in chunk_hits:
        track_chunks[hit.track_id].append(hit)

    results: list[TrackResult] = []

    for track_id, chunks in track_chunks.items():
        # 4. Exclude exact-match track if specified
        if exact_match_track_id is not None and track_id == exact_match_track_id:
            logger.debug(
                "Excluding exact-match track %s from vibe results",
                track_id,
            )
            continue

        # 2. base_score = mean of top-K chunk scores
        sorted_scores = sorted((c.score for c in chunks), reverse=True)
        top_k_scores = sorted_scores[:top_k_per_track]
        base_score = sum(top_k_scores) / len(top_k_scores)

        # 3. Diversity bonus: reward tracks matching at multiple offsets
        unique_offsets = len({c.offset_sec for c in chunks})
        diversity_bonus = min(unique_offsets / 5.0, 1.0) * diversity_weight

        final_score = base_score + diversity_bonus

        results.append(
            TrackResult(
                track_id=track_id,
                final_score=final_score,
                base_score=base_score,
                diversity_bonus=diversity_bonus,
                chunk_count=len(chunks),
                top_chunk_scores=top_k_scores,
            )
        )

    # 5. Sort by final_score descending
    results.sort(key=lambda r: r.final_score, reverse=True)

    logger.debug(
        "Aggregated %d chunk hits into %d track results",
        len(chunk_hits),
        len(results),
    )

    return results
