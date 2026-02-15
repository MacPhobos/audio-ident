from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class IngestResponse(BaseModel):
    """Response for a single-file ingest operation."""

    track_id: uuid.UUID
    title: str
    artist: str | None = None
    status: str  # "ingested", "duplicate", "error"


class IngestError(BaseModel):
    """Describes a single file that failed during batch ingestion."""

    file: str
    error: str


class IngestReport(BaseModel):
    """Summary report for batch (directory) ingestion."""

    total: int
    ingested: int = 0
    duplicates: int = 0
    errors: list[IngestError] = Field(default_factory=list)
