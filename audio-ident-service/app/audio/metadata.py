"""Audio metadata extraction using mutagen.

Extracts title, artist, album, and technical metadata from audio files.
Supports MP3 (ID3), OGG/WebM (Vorbis), and MP4/M4A tag formats.
"""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import mutagen
from mutagen.mp4 import MP4

logger = logging.getLogger(__name__)

# Tag key mappings per format family
_ID3_TAG_MAP: dict[str, str] = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
}

_VORBIS_TAG_MAP: dict[str, str] = {
    "title": "title",
    "artist": "artist",
    "album": "album",
}

_MP4_TAG_MAP: dict[str, str] = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album": "\xa9alb",
}


@dataclass
class AudioMetadata:
    """Container for extracted audio metadata."""

    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration_seconds: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bitrate: int | None = None
    format: str | None = None
    file_hash_sha256: str = ""
    file_size_bytes: int = 0


def _get_first_text(tags: dict | mutagen.Tags | None, key: str) -> str | None:
    """Safely extract a text tag value, handling list-valued tags."""
    if tags is None:
        return None
    value = tags.get(key)
    if value is None:
        return None
    # mutagen often wraps values in list-like objects
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value)


def _extract_tags_id3(tags: mutagen.Tags) -> dict[str, str | None]:
    """Extract metadata from ID3 tags (MP3)."""
    result: dict[str, str | None] = {}
    for field_name, tag_key in _ID3_TAG_MAP.items():
        tag = tags.get(tag_key)
        if tag is not None:
            # ID3 text frames have a .text list attribute
            texts = getattr(tag, "text", None)
            if texts:
                result[field_name] = str(texts[0])
            else:
                result[field_name] = str(tag)
        else:
            result[field_name] = None
    return result


def _extract_tags_vorbis(tags: mutagen.Tags) -> dict[str, str | None]:
    """Extract metadata from Vorbis comments (OGG/WebM/FLAC)."""
    result: dict[str, str | None] = {}
    for field_name, tag_key in _VORBIS_TAG_MAP.items():
        result[field_name] = _get_first_text(tags, tag_key)
    return result


def _extract_tags_mp4(tags: mutagen.Tags) -> dict[str, str | None]:
    """Extract metadata from MP4 atoms (M4A/AAC)."""
    result: dict[str, str | None] = {}
    for field_name, tag_key in _MP4_TAG_MAP.items():
        result[field_name] = _get_first_text(tags, tag_key)
    return result


def extract_metadata(file_path: Path) -> AudioMetadata:
    """Extract metadata from an audio file using mutagen.

    Handles MP3 (ID3: TIT2/TPE1/TALB), WebM/OGG (Vorbis: title/artist/album),
    and MP4 (\\xa9nam/\\xa9ART/\\xa9alb) tag formats.

    Args:
        file_path: Path to the audio file.

    Returns:
        AudioMetadata with available fields populated.
    """
    meta = AudioMetadata()

    file_path = Path(file_path)
    meta.file_size_bytes = file_path.stat().st_size
    meta.file_hash_sha256 = compute_file_hash(file_path)

    try:
        audio_file = mutagen.File(str(file_path))
    except Exception:
        logger.warning("mutagen could not parse file: %s", file_path)
        return meta

    if audio_file is None:
        logger.warning("mutagen returned None for file: %s", file_path)
        return meta

    # Extract technical info from mutagen.FileType.info
    info = audio_file.info
    if info is not None:
        meta.duration_seconds = getattr(info, "length", None)
        meta.sample_rate = getattr(info, "sample_rate", None)
        meta.channels = getattr(info, "channels", None)
        bitrate = getattr(info, "bitrate", None)
        if bitrate is not None:
            meta.bitrate = int(bitrate)

    # Determine format from file extension
    suffix = file_path.suffix.lower().lstrip(".")
    meta.format = suffix if suffix else None

    # Extract tags
    tags = audio_file.tags
    tag_data: dict[str, str | None] = {}

    if tags is not None:
        if isinstance(audio_file, MP4):
            tag_data = _extract_tags_mp4(tags)
        elif hasattr(tags, "getall"):
            # ID3 tags (MP3)
            tag_data = _extract_tags_id3(tags)
        else:
            # Vorbis-style comments (OGG, FLAC, WebM)
            tag_data = _extract_tags_vorbis(tags)

    meta.title = tag_data.get("title")
    meta.artist = tag_data.get("artist")
    meta.album = tag_data.get("album")

    return meta


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of file contents.

    Args:
        file_path: Path to the file.

    Returns:
        Lowercase hex-encoded SHA-256 digest.
    """
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()
