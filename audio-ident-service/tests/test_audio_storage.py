"""Tests for app.audio.storage module."""

from pathlib import Path
from unittest.mock import patch

from app.audio.storage import ensure_storage_dirs, raw_audio_path


class TestRawAudioPath:
    @patch("app.audio.storage.settings")
    def test_path_structure(self, mock_settings: object) -> None:
        """Generated path should follow the fan-out pattern."""
        mock_settings.audio_storage_root = "/data"  # type: ignore[attr-defined]

        result = raw_audio_path("abcdef1234567890", "mp3")
        expected = Path("/data/raw/ab/abcdef1234567890.mp3")
        assert result == expected

    @patch("app.audio.storage.settings")
    def test_strips_leading_dot(self, mock_settings: object) -> None:
        """Extension with leading dot should be normalized."""
        mock_settings.audio_storage_root = "/data"  # type: ignore[attr-defined]

        result = raw_audio_path("abcdef1234567890", ".wav")
        expected = Path("/data/raw/ab/abcdef1234567890.wav")
        assert result == expected

    @patch("app.audio.storage.settings")
    def test_uses_first_two_chars_of_hash(self, mock_settings: object) -> None:
        """Fan-out directory should use first 2 chars of the hash."""
        mock_settings.audio_storage_root = "/data"  # type: ignore[attr-defined]

        result = raw_audio_path("ff0011223344", "ogg")
        assert result.parent.name == "ff"

    @patch("app.audio.storage.settings")
    def test_different_hashes_different_dirs(self, mock_settings: object) -> None:
        mock_settings.audio_storage_root = "/data"  # type: ignore[attr-defined]

        path_a = raw_audio_path("aa1111111111", "mp3")
        path_b = raw_audio_path("bb2222222222", "mp3")
        assert path_a.parent != path_b.parent

    @patch("app.audio.storage.settings")
    def test_same_prefix_same_dir(self, mock_settings: object) -> None:
        mock_settings.audio_storage_root = "/data"  # type: ignore[attr-defined]

        path_a = raw_audio_path("aabbcc111111", "mp3")
        path_b = raw_audio_path("aabbcc222222", "mp3")
        assert path_a.parent == path_b.parent


class TestEnsureStorageDirs:
    @patch("app.audio.storage.settings")
    def test_creates_directory(self, mock_settings: object, tmp_path: Path) -> None:
        """Should create the nested directory structure."""
        mock_settings.audio_storage_root = str(tmp_path)  # type: ignore[attr-defined]

        result = ensure_storage_dirs("abcdef1234567890")
        expected = tmp_path / "raw" / "ab"
        assert result == expected
        assert expected.exists()
        assert expected.is_dir()

    @patch("app.audio.storage.settings")
    def test_idempotent(self, mock_settings: object, tmp_path: Path) -> None:
        """Calling twice should not raise."""
        mock_settings.audio_storage_root = str(tmp_path)  # type: ignore[attr-defined]

        ensure_storage_dirs("abcdef1234567890")
        ensure_storage_dirs("abcdef1234567890")

        expected = tmp_path / "raw" / "ab"
        assert expected.exists()

    @patch("app.audio.storage.settings")
    def test_returns_correct_path(self, mock_settings: object, tmp_path: Path) -> None:
        mock_settings.audio_storage_root = str(tmp_path)  # type: ignore[attr-defined]

        result = ensure_storage_dirs("ff9988776655")
        expected = tmp_path / "raw" / "ff"
        assert result == expected
