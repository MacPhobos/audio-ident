"""Ingestion pipeline orchestrating all audio processing steps.

Processes audio files through: metadata extraction, dual-rate PCM decode,
duplicate detection, Olaf fingerprint indexing, and CLAP embedding generation.
"""

import asyncio
import contextlib
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audio.decode import AudioDecodeError, decode_dual_rate, pcm_duration_seconds
from app.audio.dedup import (
    check_content_duplicate,
    check_file_duplicate,
    f32le_to_s16le,
    generate_chromaprint,
)
from app.audio.embedding import EmbeddingError, generate_chunked_embeddings
from app.audio.fingerprint import olaf_delete_track, olaf_index_track
from app.audio.metadata import compute_file_hash, extract_metadata
from app.audio.qdrant_setup import (
    delete_track_embeddings,
    ensure_collection,
    upsert_track_embeddings,
)
from app.audio.storage import ensure_storage_dirs, raw_audio_path

logger = logging.getLogger(__name__)

# Audio file extensions to scan
AUDIO_EXTENSIONS: set[str] = {".mp3", ".wav", ".webm", ".ogg", ".mp4", ".m4a", ".flac"}

# Duration limits
MAX_INGESTION_DURATION: float = 1800.0  # 30 minutes
MIN_INGESTION_DURATION: float = 3.0  # 3 seconds


@dataclass
class IngestResult:
    """Result of ingesting a single file."""

    file_path: str
    track_id: uuid.UUID | None = None
    status: str = "pending"  # "success", "duplicate", "skipped", "error"
    error: str | None = None
    duration_seconds: float | None = None


@dataclass
class IngestReport:
    """Summary of a batch ingestion run."""

    total_files: int = 0
    ingested: int = 0
    duplicates: int = 0
    skipped: int = 0
    errors: int = 0
    results: list[IngestResult] = field(default_factory=list)


async def ingest_file(
    file_path: Path,
    clap_model: Any,
    clap_processor: Any,
    qdrant_client: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> IngestResult:
    """Ingest a single audio file through the full pipeline.

    Steps:
    1. Read file bytes, compute SHA-256 hash
    2. Check file hash duplicate (fast path)
    3. Save raw file to data/raw/{hash_prefix}/{hash}.{ext}
    4. Extract metadata via mutagen
    5. Decode to dual-rate PCM (16kHz + 48kHz in parallel)
    6. Run 3 parallel tasks via asyncio.gather:
       a. Chromaprint fingerprint generation + content dedup check
       b. Olaf fingerprint indexing
       c. CLAP chunked embedding generation + Qdrant upsert
    7. Insert Track record into PostgreSQL

    Args:
        file_path: Path to the audio file.
        clap_model: Pre-loaded CLAP model.
        clap_processor: Pre-loaded CLAP processor.
        qdrant_client: Qdrant client instance.
        session_factory: Async SQLAlchemy session factory.

    Returns:
        IngestResult with status and details.
    """
    result = IngestResult(file_path=str(file_path))

    try:
        # Step 1: Compute file hash
        file_hash = compute_file_hash(file_path)

        # Step 2: Check file hash duplicate
        async with session_factory() as session:
            existing_id = await check_file_duplicate(session, file_hash)
            if existing_id:
                result.status = "duplicate"
                result.track_id = existing_id
                logger.info(
                    "Skipping duplicate file: %s (hash: %s)",
                    file_path.name,
                    file_hash[:8],
                )
                return result

        # Step 3: Save raw file
        extension = file_path.suffix.lstrip(".")
        storage_path = raw_audio_path(file_hash, extension)
        ensure_storage_dirs(file_hash)
        shutil.copy2(file_path, storage_path)

        # Step 4: Extract metadata
        metadata = extract_metadata(file_path)

        # Step 5: Decode to dual-rate PCM
        file_bytes = file_path.read_bytes()
        pcm_16k, pcm_48k = await decode_dual_rate(file_bytes)

        duration = pcm_duration_seconds(pcm_16k, 16000)
        result.duration_seconds = duration

        # Duration validation
        if duration < MIN_INGESTION_DURATION:
            result.status = "skipped"
            result.error = f"Too short: {duration:.1f}s (min: {MIN_INGESTION_DURATION}s)"
            logger.warning("Skipping too-short file: %s (%.1fs)", file_path.name, duration)
            return result
        if duration > MAX_INGESTION_DURATION:
            result.status = "skipped"
            result.error = f"Too long: {duration:.1f}s (max: {MAX_INGESTION_DURATION}s)"
            logger.warning("Skipping too-long file: %s (%.1fs)", file_path.name, duration)
            return result

        # Step 6: Parallel processing
        track_id = uuid.uuid4()

        # 6a: Chromaprint + dedup
        async def chromaprint_task() -> tuple[str, uuid.UUID | None, str | None]:
            pcm_s16le = f32le_to_s16le(pcm_16k)
            fingerprint = generate_chromaprint(pcm_s16le, duration)
            if fingerprint:
                async with session_factory() as session:
                    content_dup = await check_content_duplicate(session, fingerprint, duration)
                    if content_dup:
                        return ("duplicate", content_dup, fingerprint)
            return ("ok", None, fingerprint)

        # 6b: Olaf indexing
        async def olaf_task() -> bool:
            try:
                success = await olaf_index_track(pcm_16k, track_id)
                return success
            except Exception as e:
                logger.error("Olaf indexing failed for %s: %s", file_path.name, e)
                return False

        # 6c: CLAP embedding + Qdrant
        async def embedding_task() -> tuple[int, int]:
            try:
                chunks = generate_chunked_embeddings(pcm_48k, clap_model, clap_processor)
                if chunks:
                    meta = {
                        "artist": metadata.artist or "",
                        "title": metadata.title or "",
                        "genre": "",
                    }
                    count = upsert_track_embeddings(qdrant_client, track_id, chunks, meta)
                    return len(chunks), count
                return 0, 0
            except (EmbeddingError, Exception) as e:
                logger.error("Embedding failed for %s: %s", file_path.name, e)
                return 0, 0

        # Run all 3 in parallel
        chromaprint_result, olaf_success, embedding_result = await asyncio.gather(
            chromaprint_task(), olaf_task(), embedding_task()
        )

        # Check content duplicate from chromaprint
        chroma_status, content_dup_id, fingerprint = chromaprint_result
        if chroma_status == "duplicate" and content_dup_id:
            result.status = "duplicate"
            result.track_id = content_dup_id
            # Clean up: remove from Olaf and Qdrant since this is a dup
            # (Best effort - don't fail if cleanup fails)
            if olaf_success:
                with contextlib.suppress(Exception):
                    await olaf_delete_track(track_id)
            if embedding_result[0] > 0:
                with contextlib.suppress(Exception):
                    delete_track_embeddings(qdrant_client, track_id)
            return result

        embedding_count, _ = embedding_result

        # Step 7: Insert Track record
        async with session_factory() as session:
            from app.models.track import Track

            track = Track(
                id=track_id,
                title=metadata.title or file_path.stem,
                artist=metadata.artist,
                album=metadata.album,
                duration_seconds=duration,
                sample_rate=metadata.sample_rate,
                channels=metadata.channels,
                bitrate=metadata.bitrate,
                format=metadata.format,
                file_hash_sha256=file_hash,
                file_size_bytes=metadata.file_size_bytes,
                file_path=str(storage_path),
                chromaprint_fingerprint=fingerprint,
                chromaprint_duration=duration if fingerprint else None,
                olaf_indexed=olaf_success,
                embedding_model=("clap-htsat-large" if embedding_count > 0 else None),
                embedding_dim=512 if embedding_count > 0 else None,
            )
            session.add(track)
            await session.commit()

        result.status = "success"
        result.track_id = track_id
        logger.info(
            "Ingested: %s -> %s (olaf=%s, embeddings=%d)",
            file_path.name,
            track_id,
            olaf_success,
            embedding_count,
        )
        return result

    except AudioDecodeError as e:
        result.status = "error"
        result.error = f"Decode error: {e}"
        logger.error("Decode error for %s: %s", file_path.name, e)
        return result
    except Exception as e:
        result.status = "error"
        result.error = f"Unexpected error: {e}"
        logger.exception("Unexpected error ingesting %s", file_path.name)
        return result


async def ingest_directory(
    directory: Path,
    clap_model: Any,
    clap_processor: Any,
    qdrant_client: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> IngestReport:
    """Ingest all audio files from a directory.

    Processes files sequentially (one at a time) to manage memory and
    respect Olaf's single-writer constraint.

    Args:
        directory: Path to directory containing audio files.
        clap_model: Pre-loaded CLAP model.
        clap_processor: Pre-loaded CLAP processor.
        qdrant_client: Qdrant client instance.
        session_factory: Async SQLAlchemy session factory.

    Returns:
        IngestReport with counts and per-file results.
    """
    report = IngestReport()

    # Scan for audio files (recursive)
    audio_files = sorted(
        f for f in directory.rglob("*") if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    )

    report.total_files = len(audio_files)

    if not audio_files:
        logger.warning("No audio files found in %s", directory)
        return report

    logger.info("Found %d audio files in %s", len(audio_files), directory)

    # Ensure Qdrant collection exists
    ensure_collection(qdrant_client)

    # Process sequentially (Olaf LMDB is single-writer)
    for i, file_path in enumerate(audio_files, 1):
        logger.info("[%d/%d] Ingesting: %s", i, len(audio_files), file_path.name)

        file_result = await ingest_file(
            file_path, clap_model, clap_processor, qdrant_client, session_factory
        )
        report.results.append(file_result)

        if file_result.status == "success":
            report.ingested += 1
        elif file_result.status == "duplicate":
            report.duplicates += 1
        elif file_result.status == "skipped":
            report.skipped += 1
        elif file_result.status == "error":
            report.errors += 1

    logger.info(
        "Ingestion complete: %d ingested, %d duplicates, %d skipped, %d errors " "(of %d total)",
        report.ingested,
        report.duplicates,
        report.skipped,
        report.errors,
        report.total_files,
    )

    return report
