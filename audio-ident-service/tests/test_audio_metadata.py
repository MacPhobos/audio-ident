"""Tests for app.audio.metadata module."""

import math
import struct
import wave
from pathlib import Path

import pytest

from app.audio.metadata import AudioMetadata, compute_file_hash, extract_metadata


def _make_wav_file(
    path: Path,
    duration_seconds: float = 1.0,
    sample_rate: int = 44100,
) -> None:
    """Write a minimal WAV file to disk."""
    num_frames = int(sample_rate * duration_seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)

        frames = bytearray()
        for i in range(num_frames):
            sample = int(16000 * math.sin(2 * math.pi * 440 * i / sample_rate))
            frames.extend(struct.pack("<h", sample))

        wf.writeframes(bytes(frames))


@pytest.fixture
def wav_file(tmp_path: Path) -> Path:
    """Create a temporary WAV file and return its path."""
    audio_path = tmp_path / "test.wav"
    _make_wav_file(audio_path, duration_seconds=1.0)
    return audio_path


@pytest.fixture
def empty_file(tmp_path: Path) -> Path:
    """Create an empty file."""
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    return p


class TestExtractMetadata:
    def test_wav_metadata(self, wav_file: Path) -> None:
        meta = extract_metadata(wav_file)
        assert isinstance(meta, AudioMetadata)
        assert meta.file_size_bytes > 0
        assert meta.file_hash_sha256 != ""
        assert len(meta.file_hash_sha256) == 64
        assert meta.format == "wav"

    def test_wav_duration(self, wav_file: Path) -> None:
        meta = extract_metadata(wav_file)
        # WAV file is ~1 second, allow tolerance
        if meta.duration_seconds is not None:
            assert 0.9 < meta.duration_seconds < 1.1

    def test_wav_sample_rate(self, wav_file: Path) -> None:
        meta = extract_metadata(wav_file)
        assert meta.sample_rate == 44100

    def test_wav_channels(self, wav_file: Path) -> None:
        meta = extract_metadata(wav_file)
        assert meta.channels == 1

    def test_missing_tags_returns_none(self, wav_file: Path) -> None:
        """WAV files typically have no ID3/Vorbis tags."""
        meta = extract_metadata(wav_file)
        # WAV does not carry tags, so title/artist/album should be None
        assert meta.title is None
        assert meta.artist is None
        assert meta.album is None

    def test_nonexistent_file_raises(self, tmp_path: Path) -> None:
        fake_path = tmp_path / "nonexistent.mp3"
        with pytest.raises(FileNotFoundError):
            extract_metadata(fake_path)

    def test_unrecognized_format(self, tmp_path: Path) -> None:
        """A non-audio file should return metadata with file info but no tags."""
        p = tmp_path / "data.xyz"
        p.write_bytes(b"not audio content at all" * 100)
        meta = extract_metadata(p)
        assert meta.file_size_bytes > 0
        assert meta.file_hash_sha256 != ""
        assert meta.title is None


class TestComputeFileHash:
    def test_hash_is_consistent(self, wav_file: Path) -> None:
        hash1 = compute_file_hash(wav_file)
        hash2 = compute_file_hash(wav_file)
        assert hash1 == hash2

    def test_hash_is_64_hex_chars(self, wav_file: Path) -> None:
        h = compute_file_hash(wav_file)
        assert len(h) == 64
        # Verify it's valid hex
        int(h, 16)

    def test_different_files_different_hashes(self, tmp_path: Path) -> None:
        file1 = tmp_path / "a.bin"
        file2 = tmp_path / "b.bin"
        file1.write_bytes(b"content A")
        file2.write_bytes(b"content B")

        h1 = compute_file_hash(file1)
        h2 = compute_file_hash(file2)
        assert h1 != h2

    def test_known_hash(self, tmp_path: Path) -> None:
        """Verify SHA-256 against a known value."""
        import hashlib

        content = b"hello world"
        p = tmp_path / "known.bin"
        p.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        result = compute_file_hash(p)
        assert result == expected
