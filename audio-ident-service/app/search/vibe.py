"""Vibe search lane: CLAP embedding + Qdrant similarity search.

Takes 48kHz mono PCM audio, generates a CLAP embedding, queries Qdrant
for the top-N nearest chunks, aggregates chunk scores to track-level
results, and returns ranked VibeMatch results.

CLAP model: laion/larger_clap_music_and_speech (512-dim, cosine similarity)
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import numpy as np
from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio.embedding import generate_embedding
from app.models.track import Track
from app.schemas.search import TrackInfo, VibeMatch
from app.search.aggregation import ChunkHit, aggregate_chunk_hits
from app.settings import settings

logger = logging.getLogger(__name__)

# Semaphore to serialize CLAP inference.
# CLAP inference is CPU-bound (~0.2s). Concurrent inferences contend for
# CPU and degrade latency for all callers. A semaphore ensures only one
# inference runs at a time.
_clap_semaphore = asyncio.Semaphore(1)


async def run_vibe_lane(
    pcm_48k: bytes,
    max_results: int,
    *,
    qdrant_client: AsyncQdrantClient,
    clap_model: object,
    clap_processor: object,
    session: AsyncSession,
    exact_match_track_id: uuid.UUID | None = None,
) -> list[VibeMatch]:
    """Search by audio embedding (vibe/similarity).

    Generates a CLAP embedding from the query audio, queries Qdrant for
    nearest chunks, aggregates to track-level scores, and returns ranked
    VibeMatch results with track metadata from PostgreSQL.

    Args:
        pcm_48k: Raw 48kHz mono float32 little-endian PCM bytes.
        max_results: Maximum number of VibeMatch results to return.
        qdrant_client: Async Qdrant client (from app.state.qdrant).
        clap_model: Loaded ClapModel instance (from app.state.clap_model).
        clap_processor: Loaded ClapProcessor instance (from app.state.clap_processor).
        session: Async SQLAlchemy session for PostgreSQL metadata lookups.
        exact_match_track_id: If set, exclude this track from results.
            Prevents "you searched for X, we found X" results. Optional
            (None = no exclusion) so Phase 4b is independently testable.

    Returns:
        List of VibeMatch sorted by similarity descending, filtered by
        the configurable vibe_match_threshold.

    Raises:
        ValueError: If clap_model or clap_processor is None (not loaded).
    """
    # Validate model availability
    if clap_model is None or clap_processor is None:
        raise ValueError(
            "CLAP model not loaded. Ensure the model is loaded during app "
            "startup and available on app.state.clap_model / app.state.clap_processor."
        )

    # 1. Convert PCM bytes to numpy array (f32le -- already 32-bit float from ffmpeg)
    audio = np.frombuffer(pcm_48k, dtype=np.float32)

    if len(audio) == 0:
        logger.warning("Empty audio input for vibe search")
        return []

    # 2. Generate CLAP embedding (CPU-bound, ~0.2s)
    #    Use run_in_executor to avoid blocking the asyncio event loop.
    #    Use semaphore to prevent concurrent CPU-bound inferences from
    #    degrading latency.
    loop = asyncio.get_event_loop()
    async with _clap_semaphore:
        embedding = await loop.run_in_executor(
            None,
            generate_embedding,
            audio,
            clap_model,
            clap_processor,
        )

    embedding_list = embedding.tolist()

    # 3. Query Qdrant for nearest chunks
    chunk_hits = await _query_qdrant(qdrant_client, embedding_list)

    if not chunk_hits:
        logger.debug("No chunk hits from Qdrant for vibe search")
        return []

    # 4. Aggregate chunks to track-level scores
    track_results = aggregate_chunk_hits(
        chunk_hits=chunk_hits,
        exact_match_track_id=exact_match_track_id,
    )

    if not track_results:
        return []

    # 5. Filter by threshold
    threshold = settings.vibe_match_threshold
    filtered_results = [r for r in track_results if r.final_score >= threshold]

    if not filtered_results:
        logger.debug(
            "All vibe results below threshold %.2f (top score: %.4f)",
            threshold,
            track_results[0].final_score if track_results else 0.0,
        )
        return []

    # 6. Limit to max_results
    limited_results = filtered_results[:max_results]

    # 7. Look up track metadata from PostgreSQL
    track_ids = [r.track_id for r in limited_results]
    tracks_by_id = await _get_tracks_by_ids(session, track_ids)

    # 8. Build VibeMatch responses, maintaining aggregated ranking order
    vibe_matches: list[VibeMatch] = []
    for result in limited_results:
        track = tracks_by_id.get(result.track_id)
        if track is None:
            logger.warning(
                "Track %s found in Qdrant but not in PostgreSQL (stale index?)",
                result.track_id,
            )
            continue

        vibe_matches.append(
            VibeMatch(
                track=TrackInfo(
                    id=track.id,
                    title=track.title,
                    artist=track.artist,
                    album=track.album,
                    duration_seconds=track.duration_seconds,
                    ingested_at=track.ingested_at,
                ),
                similarity=min(result.final_score, 1.0),
                embedding_model=settings.embedding_model,
            )
        )

    return vibe_matches


async def _query_qdrant(
    client: AsyncQdrantClient,
    embedding: list[float],
) -> list[ChunkHit]:
    """Query Qdrant for nearest chunks using the given embedding vector.

    Args:
        client: Async Qdrant client.
        embedding: 512-dim embedding vector as list of floats.

    Returns:
        List of ChunkHit parsed from Qdrant results.
        Returns empty list on any Qdrant error (graceful degradation).
    """
    try:
        search_results = await client.query_points(
            collection_name=settings.qdrant_collection_name,
            query=embedding,
            limit=settings.qdrant_search_limit,
            with_payload=True,
            search_params=models.SearchParams(hnsw_ef=128),
        )
    except Exception:
        logger.exception("Qdrant query failed for vibe search")
        return []

    chunk_hits: list[ChunkHit] = []

    for point in search_results.points:
        payload = point.payload or {}
        track_id_str = payload.get("track_id")
        if track_id_str is None:
            logger.warning("Qdrant point %s missing track_id in payload", point.id)
            continue

        try:
            track_id = uuid.UUID(track_id_str)
        except (ValueError, TypeError):
            logger.warning(
                "Qdrant point %s has invalid track_id: %s",
                point.id,
                track_id_str,
            )
            continue

        chunk_hits.append(
            ChunkHit(
                track_id=track_id,
                score=point.score,
                chunk_index=int(payload.get("chunk_index", 0)),
                offset_sec=float(payload.get("offset_sec", 0.0)),
            )
        )

    return chunk_hits


async def _get_tracks_by_ids(
    session: AsyncSession,
    track_ids: list[uuid.UUID],
) -> dict[uuid.UUID, Track]:
    """Look up Track records by IDs from PostgreSQL.

    Args:
        session: Async SQLAlchemy session.
        track_ids: List of track UUIDs to look up.

    Returns:
        Dict mapping track_id to Track model instance.
    """
    if not track_ids:
        return {}

    stmt = select(Track).where(Track.id.in_(track_ids))
    result = await session.execute(stmt)
    return {t.id: t for t in result.scalars().all()}
