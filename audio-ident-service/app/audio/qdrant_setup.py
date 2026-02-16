"""Qdrant collection management for CLAP audio embeddings.

Handles lazy collection creation with the correct schema:
512-dim vectors, cosine distance, HNSW with scalar quantization.
"""

import logging
import uuid

from qdrant_client import AsyncQdrantClient, models

from app.audio.embedding import AudioChunk
from app.settings import settings

logger = logging.getLogger(__name__)

BATCH_SIZE: int = 100  # Upsert batch size to avoid oversized requests


def get_qdrant_client() -> AsyncQdrantClient:
    """Create and return an AsyncQdrantClient using application settings.

    Reads ``QDRANT_URL`` and ``QDRANT_API_KEY`` from the global settings
    object.  This is intended for use outside the FastAPI lifespan (e.g.
    the batch-ingestion CLI) where ``app.state.qdrant`` is not available.
    """
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key if settings.qdrant_api_key else None,
    )


async def ensure_collection(client: AsyncQdrantClient) -> None:
    """Create the audio embeddings collection if it doesn't exist.

    Schema:
        - 512-dim vectors, cosine distance
        - HNSW: m=16, ef_construct=200
        - Scalar quantization: int8, quantile=0.99, always_ram=True
        - Payload indexes on track_id (keyword) and genre (keyword)
    """
    collection_name = settings.qdrant_collection_name

    # Check if collection exists
    collections_response = await client.get_collections()
    if any(c.name == collection_name for c in collections_response.collections):
        return

    # Create collection with full schema
    await client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=settings.embedding_dim,
            distance=models.Distance.COSINE,
        ),
        hnsw_config=models.HnswConfigDiff(m=16, ef_construct=200),
        quantization_config=models.ScalarQuantization(
            scalar=models.ScalarQuantizationConfig(
                type=models.ScalarType.INT8,
                quantile=0.99,
                always_ram=True,
            )
        ),
    )

    # Create payload indexes for filtered search
    await client.create_payload_index(
        collection_name=collection_name,
        field_name="track_id",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )
    await client.create_payload_index(
        collection_name=collection_name,
        field_name="genre",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )

    logger.info("Created Qdrant collection '%s' with INT8 quantization", collection_name)


async def upsert_track_embeddings(
    client: AsyncQdrantClient,
    track_id: uuid.UUID,
    chunks: list[AudioChunk],
    metadata: dict[str, str] | None = None,
) -> int:
    """Upsert all chunk embeddings for a track to Qdrant.

    Args:
        client: Qdrant async client.
        track_id: Track UUID.
        chunks: List of AudioChunk with embeddings.
        metadata: Optional dict with artist, title, genre.

    Returns:
        Number of points upserted.

    Payload per point:
        track_id: str (UUID string)
        offset_sec: float
        chunk_index: int
        duration_sec: float
        artist: str (from metadata)
        title: str (from metadata)
        genre: str (from metadata, if available)
    """
    if not chunks:
        return 0

    collection_name = settings.qdrant_collection_name

    # Ensure collection exists
    await ensure_collection(client)

    # Build all points
    points: list[models.PointStruct] = []
    for chunk in chunks:
        payload: dict[str, str | float | int] = {
            "track_id": str(track_id),
            "offset_sec": chunk.offset_sec,
            "chunk_index": chunk.chunk_index,
            "duration_sec": chunk.duration_sec,
        }

        # Add metadata fields if provided
        if metadata:
            if "artist" in metadata:
                payload["artist"] = metadata["artist"]
            if "title" in metadata:
                payload["title"] = metadata["title"]
            if "genre" in metadata:
                payload["genre"] = metadata["genre"]

        points.append(
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector=chunk.embedding,
                payload=payload,
            )
        )

    # Upsert in batches
    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i : i + BATCH_SIZE]
        await client.upsert(
            collection_name=collection_name,
            points=batch,
        )

    logger.info(
        "Upserted %d embeddings for track %s",
        len(points),
        track_id,
    )

    return len(points)


async def delete_track_embeddings(
    client: AsyncQdrantClient,
    track_id: uuid.UUID,
) -> None:
    """Delete all embeddings for a track from Qdrant.

    Args:
        client: Qdrant async client.
        track_id: Track UUID to remove all chunks for.
    """
    await client.delete(
        collection_name=settings.qdrant_collection_name,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="track_id",
                        match=models.MatchValue(value=str(track_id)),
                    )
                ]
            )
        ),
    )
    logger.info("Deleted embeddings for track %s", track_id)
