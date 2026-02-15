from __future__ import annotations

from datetime import datetime

from app.schemas.search import TrackInfo


class TrackDetail(TrackInfo):
    """Full track detail including audio properties and indexing status."""

    sample_rate: int | None = None
    channels: int | None = None
    bitrate: int | None = None
    format: str | None = None
    file_hash_sha256: str
    file_size_bytes: int
    olaf_indexed: bool
    embedding_model: str | None = None
    embedding_dim: int | None = None
    updated_at: datetime
