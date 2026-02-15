"""Raw audio file storage with hash-based directory fan-out.

Files are stored at ``{audio_storage_root}/raw/{hash[:2]}/{hash}.{ext}``
to avoid too many files in a single directory.
"""

import logging
from pathlib import Path

from app.settings import settings

logger = logging.getLogger(__name__)


def raw_audio_path(file_hash: str, extension: str) -> Path:
    """Generate the storage path for a raw audio file.

    Path structure: ``{audio_storage_root}/raw/{first_2_chars}/{hash}.{ext}``

    Args:
        file_hash: SHA-256 hex digest of the file.
        extension: File extension without leading dot (e.g. ``"mp3"``).

    Returns:
        Full path where the raw audio file should be stored.
    """
    ext = extension.lstrip(".")
    prefix = file_hash[:2]
    return Path(settings.audio_storage_root) / "raw" / prefix / f"{file_hash}.{ext}"


def ensure_storage_dirs(file_hash: str) -> Path:
    """Create the storage directory structure for a given file hash.

    Args:
        file_hash: SHA-256 hex digest of the file.

    Returns:
        Path to the ``raw/{prefix}`` directory (created if it did not exist).
    """
    prefix = file_hash[:2]
    raw_dir = Path(settings.audio_storage_root) / "raw" / prefix
    raw_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir
