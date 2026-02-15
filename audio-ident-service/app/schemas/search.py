from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SearchMode(StrEnum):
    EXACT = "exact"
    VIBE = "vibe"
    BOTH = "both"


class TrackInfo(BaseModel):
    """Minimal track metadata returned in search results."""

    id: uuid.UUID
    title: str
    artist: str | None = None
    album: str | None = None
    duration_seconds: float
    ingested_at: datetime


class ExactMatch(BaseModel):
    """Result from the fingerprint (exact identification) lane."""

    track: TrackInfo
    confidence: float = Field(ge=0.0, le=1.0)
    offset_seconds: float | None = None
    aligned_hashes: int


class VibeMatch(BaseModel):
    """Result from the embedding (vibe/similarity) lane."""

    track: TrackInfo
    similarity: float = Field(ge=0.0, le=1.0)
    embedding_model: str


class SearchResponse(BaseModel):
    """Combined response from both search lanes."""

    request_id: uuid.UUID
    query_duration_ms: float
    exact_matches: list[ExactMatch] = Field(default_factory=list)
    vibe_matches: list[VibeMatch] = Field(default_factory=list)
    mode_used: SearchMode
