"""Olaf acoustic fingerprint indexing and querying via CLI subprocess.

Olaf is a C-based audio fingerprinting library. This module wraps the
olaf_c command-line binary for track indexing and querying against an
LMDB-backed inverted index.

IMPORTANT: Olaf LMDB has a single-writer constraint. Do NOT run
concurrent indexing operations.

Audio must be 16kHz mono float32 PCM (f32le format).
"""

import asyncio
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.settings import settings

logger = logging.getLogger(__name__)


class OlafError(Exception):
    """Raised when Olaf operations fail."""


@dataclass
class OlafMatch:
    """A single match result from Olaf query.

    Attributes:
        match_count: Number of matching fingerprint hashes.
        query_start: Start time in the query audio (seconds).
        query_stop: Stop time in the query audio (seconds).
        reference_path: Track name / path used when indexing.
        reference_id: Internal Olaf reference ID.
        reference_start: Start time in the reference track (seconds).
        reference_stop: Stop time in the reference track (seconds).
    """

    match_count: int
    query_start: float
    query_stop: float
    reference_path: str
    reference_id: int
    reference_start: float
    reference_stop: float


def _get_olaf_bin() -> str:
    """Get the path to the olaf_c binary.

    Checks settings for a configured path, verifies it exists if absolute.
    Falls back to the configured value (which may rely on PATH resolution).

    Returns:
        Path string for the olaf_c binary.
    """
    olaf_bin = settings.olaf_bin_path
    if olaf_bin and Path(olaf_bin).is_absolute() and not Path(olaf_bin).exists():
        logger.warning(
            "Configured olaf_bin_path %s does not exist, falling back to 'olaf_c'", olaf_bin
        )
        return "olaf_c"
    return olaf_bin


def _get_olaf_env() -> dict[str, str]:
    """Build environment dict for olaf_c subprocess.

    Sets OLAF_DB to the configured LMDB path, ensuring the directory exists.

    Returns:
        Environment dict with OLAF_DB set.
    """
    db_path = settings.olaf_lmdb_path
    Path(db_path).mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["OLAF_DB"] = str(Path(db_path).resolve())
    return env


async def olaf_index_track(pcm_16k_f32le: bytes, track_id: uuid.UUID) -> bool:
    """Index a track's fingerprint hashes into Olaf's LMDB.

    Writes PCM data to a temporary file and invokes ``olaf_c store``.

    Args:
        pcm_16k_f32le: Raw 16kHz mono float32 PCM data.
        track_id: Unique track identifier (used as the track name in Olaf).

    Returns:
        True if indexing succeeded, False on failure.

    Raises:
        OlafError: If the olaf_c binary is not found or crashes.
    """
    if not pcm_16k_f32le:
        logger.warning("Empty PCM data provided for indexing track %s", track_id)
        return False

    olaf_bin = _get_olaf_bin()
    env = _get_olaf_env()
    track_name = str(track_id)

    tmp_path: str | None = None
    try:
        # Write PCM to temp file (Olaf reads from disk, not stdin)
        with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp:
            tmp.write(pcm_16k_f32le)
            tmp_path = tmp.name

        proc = await asyncio.create_subprocess_exec(
            olaf_bin,
            "store",
            tmp_path,
            track_name,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            logger.error(
                "olaf_c store failed for track %s (exit %d): %s",
                track_id,
                proc.returncode,
                err_msg,
            )
            return False

        logger.info("Successfully indexed track %s in Olaf", track_id)
        return True

    except FileNotFoundError:
        raise OlafError(
            f"olaf_c binary not found at '{olaf_bin}'. "
            "Ensure Olaf is installed and olaf_bin_path is configured."
        ) from None
    except Exception as exc:
        logger.exception("Unexpected error indexing track %s", track_id)
        raise OlafError(f"Failed to index track {track_id}: {exc}") from exc
    finally:
        if tmp_path is not None:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to clean up temp file %s", tmp_path)


async def olaf_query(pcm_16k_f32le: bytes) -> list[OlafMatch]:
    """Query the Olaf index with a PCM audio clip.

    Writes PCM data to a temporary file and invokes ``olaf_c query``.

    Args:
        pcm_16k_f32le: Raw 16kHz mono float32 PCM data (query clip).

    Returns:
        List of OlafMatch results, sorted by match_count descending.
        Empty list if no matches found.

    Raises:
        OlafError: If the olaf_c binary is not found or crashes.
    """
    if not pcm_16k_f32le:
        return []

    olaf_bin = _get_olaf_bin()
    env = _get_olaf_env()

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp:
            tmp.write(pcm_16k_f32le)
            tmp_path = tmp.name

        proc = await asyncio.create_subprocess_exec(
            olaf_bin,
            "query",
            tmp_path,
            "query",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            logger.error("olaf_c query failed (exit %d): %s", proc.returncode, err_msg)
            return []

        return _parse_olaf_output(stdout.decode(errors="replace"))

    except FileNotFoundError:
        raise OlafError(
            f"olaf_c binary not found at '{olaf_bin}'. "
            "Ensure Olaf is installed and olaf_bin_path is configured."
        ) from None
    except OlafError:
        raise
    except Exception as exc:
        logger.exception("Unexpected error during Olaf query")
        raise OlafError(f"Failed to query Olaf: {exc}") from exc
    finally:
        if tmp_path is not None:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to clean up temp file %s", tmp_path)


async def olaf_delete_track(track_id: uuid.UUID) -> bool:
    """Remove a track from the Olaf index.

    Args:
        track_id: Track identifier to remove.

    Returns:
        True if deletion succeeded, False on failure.

    Raises:
        OlafError: If the olaf_c binary is not found or crashes.
    """
    olaf_bin = _get_olaf_bin()
    env = _get_olaf_env()
    track_name = str(track_id)

    try:
        proc = await asyncio.create_subprocess_exec(
            olaf_bin,
            "del",
            track_name,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            logger.error(
                "olaf_c del failed for track %s (exit %d): %s",
                track_id,
                proc.returncode,
                err_msg,
            )
            return False

        logger.info("Successfully deleted track %s from Olaf", track_id)
        return True

    except FileNotFoundError:
        raise OlafError(
            f"olaf_c binary not found at '{olaf_bin}'. "
            "Ensure Olaf is installed and olaf_bin_path is configured."
        ) from None
    except Exception as exc:
        logger.exception("Unexpected error deleting track %s", track_id)
        raise OlafError(f"Failed to delete track {track_id}: {exc}") from exc


def _parse_olaf_output(stdout: str) -> list[OlafMatch]:
    """Parse output from olaf_c query.

    Expected format (comma-separated CSV):
        match_count, query_start, query_stop, ref_path, ref_id, ref_start, ref_stop

    Falls back to semicolon-separated parsing if comma parsing produces
    fewer than 7 fields.

    Args:
        stdout: Raw stdout string from olaf_c query.

    Returns:
        List of OlafMatch results, sorted by match_count descending.
    """
    matches: list[OlafMatch] = []

    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        match = _parse_olaf_line(line)
        if match is not None:
            matches.append(match)

    # Sort by match_count descending (strongest matches first)
    matches.sort(key=lambda m: m.match_count, reverse=True)
    return matches


def _parse_olaf_line(line: str) -> OlafMatch | None:
    """Parse a single output line from olaf_c query.

    Tries comma-separated first, then semicolon-separated as fallback.

    Args:
        line: A single line of olaf_c output.

    Returns:
        OlafMatch if parsing succeeds, None otherwise.
    """
    # Try comma-separated first
    parts = [p.strip() for p in line.split(",")]
    if len(parts) >= 7:
        return _parts_to_match(parts)

    # Fallback: semicolon-separated
    parts = [p.strip() for p in line.split(";")]
    if len(parts) >= 7:
        return _parts_to_match(parts)

    logger.debug("Skipping unparseable Olaf output line: %s", line)
    return None


def _parts_to_match(parts: list[str]) -> OlafMatch | None:
    """Convert parsed fields to an OlafMatch.

    Args:
        parts: List of at least 7 string fields.

    Returns:
        OlafMatch if conversion succeeds, None otherwise.
    """
    try:
        return OlafMatch(
            match_count=int(parts[0]),
            query_start=float(parts[1]),
            query_stop=float(parts[2]),
            reference_path=parts[3],
            reference_id=int(parts[4]),
            reference_start=float(parts[5]),
            reference_stop=float(parts[6]),
        )
    except (ValueError, IndexError):
        logger.debug("Failed to parse Olaf output fields: %s", parts)
        return None
