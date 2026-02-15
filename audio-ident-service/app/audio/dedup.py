"""Two-phase duplicate detection: file hash and content fingerprint.

Phase 1 (file hash): Exact byte-level duplicate via SHA-256.
Phase 2 (Chromaprint): Content-level near-duplicate via acoustic fingerprint
similarity within a duration tolerance window.
"""

import asyncio
import logging
import uuid

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.track import Track

logger = logging.getLogger(__name__)


async def check_file_duplicate(
    session: AsyncSession,
    file_hash: str,
) -> uuid.UUID | None:
    """Check if a file hash already exists in the tracks table.

    Args:
        session: Async SQLAlchemy session.
        file_hash: SHA-256 hex digest of the file.

    Returns:
        Track UUID if duplicate found, ``None`` otherwise.
    """
    stmt = select(Track.id).where(Track.file_hash_sha256 == file_hash)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row


def f32le_to_s16le(pcm_f32le: bytes) -> bytes:
    """Convert f32le PCM bytes to s16le PCM bytes via numpy.

    Args:
        pcm_f32le: Raw 32-bit float little-endian PCM data.

    Returns:
        Raw 16-bit signed integer little-endian PCM data.
    """
    samples = np.frombuffer(pcm_f32le, dtype=np.float32)
    return np.clip(samples * 32767, -32768, 32767).astype(np.int16).tobytes()


async def generate_chromaprint(pcm_16k_s16le: bytes, duration: float) -> str | None:
    """Generate a Chromaprint fingerprint from 16kHz s16le PCM.

    Uses the ``fpcalc`` CLI binary from Chromaprint/pyacoustid via
    ``asyncio.create_subprocess_exec`` to avoid blocking the event loop.

    Note:
        Takes s16le format. The caller should convert f32le to s16le via
        ``f32le_to_s16le`` before calling this function.

    Args:
        pcm_16k_s16le: Raw 16-bit signed int PCM at 16kHz mono.
        duration: Duration in seconds (used for fpcalc ``-length``).

    Returns:
        Chromaprint fingerprint string, or ``None`` if fingerprinting fails.
    """
    if not pcm_16k_s16le:
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            "fpcalc",
            "-raw",
            "-rate",
            "16000",
            "-channels",
            "1",
            "-length",
            str(int(duration)),
            "-signed",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=pcm_16k_s16le),
                timeout=30,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("fpcalc timed out after 30 seconds")
            return None

        if proc.returncode != 0:
            logger.warning(
                "fpcalc exited with code %d: %s",
                proc.returncode,
                stderr_bytes.decode(errors="replace").strip(),
            )
            return None

        stdout = stdout_bytes.decode(errors="replace")
        for line in stdout.strip().splitlines():
            if line.startswith("FINGERPRINT="):
                return line.split("=", 1)[1]

        logger.warning("fpcalc output did not contain FINGERPRINT line")
        return None

    except FileNotFoundError:
        logger.warning("fpcalc binary not found; Chromaprint fingerprinting unavailable")
        return None
    except Exception:
        logger.exception("Unexpected error in generate_chromaprint")
        return None


def _fingerprint_similarity(fp1: str, fp2: str) -> float:
    """Compute similarity between two raw Chromaprint fingerprints.

    Compares fingerprints as comma-separated integer arrays using
    bitwise Hamming distance.

    Args:
        fp1: First raw fingerprint string (comma-separated ints).
        fp2: Second raw fingerprint string (comma-separated ints).

    Returns:
        Similarity score between 0.0 and 1.0.
    """
    try:
        arr1 = [int(x) for x in fp1.split(",")]
        arr2 = [int(x) for x in fp2.split(",")]
    except ValueError:
        return 0.0

    if not arr1 or not arr2:
        return 0.0

    # Compare overlapping portion
    min_len = min(len(arr1), len(arr2))
    if min_len == 0:
        return 0.0

    matching_bits = 0
    total_bits = min_len * 32  # Each int is 32 bits

    for i in range(min_len):
        xor = arr1[i] ^ arr2[i]
        # Count differing bits
        differing = bin(xor & 0xFFFFFFFF).count("1")
        matching_bits += 32 - differing

    # Penalize length difference
    max_len = max(len(arr1), len(arr2))
    length_penalty = min_len / max_len

    return (matching_bits / total_bits) * length_penalty


async def check_content_duplicate(
    session: AsyncSession,
    fingerprint: str,
    duration: float,
    threshold: float = 0.85,
) -> uuid.UUID | None:
    """Check for a content duplicate using Chromaprint similarity.

    Scans tracks within +/-10% duration, compares fingerprint similarity.

    Args:
        session: Async SQLAlchemy session.
        fingerprint: Raw Chromaprint fingerprint string.
        duration: Duration in seconds of the candidate track.
        threshold: Minimum similarity score to consider a match (0.0 - 1.0).

    Returns:
        Track UUID of the best match if similarity exceeds threshold,
        ``None`` otherwise.
    """
    duration_lower = duration * 0.9
    duration_upper = duration * 1.1

    stmt = select(Track.id, Track.chromaprint_fingerprint, Track.chromaprint_duration).where(
        Track.chromaprint_fingerprint.isnot(None),
        Track.chromaprint_duration.isnot(None),
        Track.chromaprint_duration >= duration_lower,
        Track.chromaprint_duration <= duration_upper,
    )

    result = await session.execute(stmt)
    rows = result.all()

    best_match_id: uuid.UUID | None = None
    best_similarity: float = 0.0

    for track_id, track_fp, _track_dur in rows:
        if track_fp is None:
            continue
        similarity = _fingerprint_similarity(fingerprint, track_fp)
        if similarity > best_similarity:
            best_similarity = similarity
            best_match_id = track_id

    if best_similarity >= threshold and best_match_id is not None:
        logger.info(
            "Content duplicate found: track %s with similarity %.4f",
            best_match_id,
            best_similarity,
        )
        return best_match_id

    return None
