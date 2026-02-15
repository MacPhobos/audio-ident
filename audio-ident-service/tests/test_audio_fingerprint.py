"""Tests for Olaf fingerprint indexing and querying (app.audio.fingerprint)."""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.audio.fingerprint import (
    OlafError,
    _parse_olaf_line,
    _parse_olaf_output,
    _parts_to_match,
    olaf_delete_track,
    olaf_index_track,
    olaf_query,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TRACK_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def fake_pcm() -> bytes:
    """Minimal fake PCM data (not real audio, just non-empty bytes)."""
    return b"\x00" * 1024


@pytest.fixture
def sample_olaf_csv() -> str:
    """Sample comma-separated output from olaf_c query."""
    return (
        "42, 0.5, 3.2, 12345678-1234-5678-1234-567812345678, 1001, 10.0, 12.7\n"
        "15, 1.0, 2.5, aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee, 1002, 5.0, 6.5\n"
    )


# ---------------------------------------------------------------------------
# _parse_olaf_output tests
# ---------------------------------------------------------------------------


class TestParseOlafOutput:
    def test_parse_comma_separated_output(self, sample_olaf_csv: str) -> None:
        matches = _parse_olaf_output(sample_olaf_csv)
        assert len(matches) == 2
        # Should be sorted by match_count descending
        assert matches[0].match_count == 42
        assert matches[1].match_count == 15

    def test_parse_fields_correctly(self) -> None:
        line = "42, 0.5, 3.2, my-track, 1001, 10.0, 12.7\n"
        matches = _parse_olaf_output(line)
        assert len(matches) == 1
        m = matches[0]
        assert m.match_count == 42
        assert m.query_start == pytest.approx(0.5)
        assert m.query_stop == pytest.approx(3.2)
        assert m.reference_path == "my-track"
        assert m.reference_id == 1001
        assert m.reference_start == pytest.approx(10.0)
        assert m.reference_stop == pytest.approx(12.7)

    def test_parse_empty_output(self) -> None:
        assert _parse_olaf_output("") == []

    def test_parse_whitespace_only(self) -> None:
        assert _parse_olaf_output("   \n  \n") == []

    def test_parse_malformed_lines_skipped(self) -> None:
        output = "not,enough,fields\n42, 0.5, 3.2, track, 1001, 10.0, 12.7\n"
        matches = _parse_olaf_output(output)
        assert len(matches) == 1
        assert matches[0].match_count == 42

    def test_parse_semicolon_fallback(self) -> None:
        line = "42; 0.5; 3.2; my-track; 1001; 10.0; 12.7"
        matches = _parse_olaf_output(line)
        assert len(matches) == 1
        assert matches[0].match_count == 42
        assert matches[0].reference_path == "my-track"

    def test_parse_non_numeric_values_skipped(self) -> None:
        line = "abc, 0.5, 3.2, track, 1001, 10.0, 12.7"
        matches = _parse_olaf_output(line)
        assert len(matches) == 0

    def test_parse_results_sorted_descending(self) -> None:
        output = (
            "5, 0.0, 1.0, track-a, 1, 0.0, 1.0\n"
            "99, 0.0, 1.0, track-b, 2, 0.0, 1.0\n"
            "20, 0.0, 1.0, track-c, 3, 0.0, 1.0\n"
        )
        matches = _parse_olaf_output(output)
        assert [m.match_count for m in matches] == [99, 20, 5]


class TestParseOlafLine:
    def test_valid_comma_line(self) -> None:
        result = _parse_olaf_line("10, 0.1, 0.5, track, 1, 2.0, 3.0")
        assert result is not None
        assert result.match_count == 10

    def test_valid_semicolon_line(self) -> None:
        result = _parse_olaf_line("10; 0.1; 0.5; track; 1; 2.0; 3.0")
        assert result is not None
        assert result.match_count == 10

    def test_too_few_fields_returns_none(self) -> None:
        assert _parse_olaf_line("10, 0.1, 0.5") is None

    def test_empty_line_returns_none(self) -> None:
        assert _parse_olaf_line("") is None


class TestPartsToMatch:
    def test_valid_parts(self) -> None:
        parts = ["42", "0.5", "3.2", "my-track", "1001", "10.0", "12.7"]
        result = _parts_to_match(parts)
        assert result is not None
        assert result.match_count == 42

    def test_invalid_int_field(self) -> None:
        parts = ["not-int", "0.5", "3.2", "track", "1001", "10.0", "12.7"]
        assert _parts_to_match(parts) is None

    def test_invalid_float_field(self) -> None:
        parts = ["42", "not-float", "3.2", "track", "1001", "10.0", "12.7"]
        assert _parts_to_match(parts) is None


# ---------------------------------------------------------------------------
# olaf_index_track tests
# ---------------------------------------------------------------------------


class TestOlafIndexTrack:
    async def test_index_success(self, fake_pcm: bytes) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"OK\n", b"")
        mock_proc.returncode = 0

        with (
            patch("app.audio.fingerprint.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("app.audio.fingerprint.Path.unlink"),
        ):
            result = await olaf_index_track(fake_pcm, SAMPLE_TRACK_ID)

        assert result is True
        # Verify olaf_c was called with "store" command
        call_args = mock_proc.communicate.call_args
        assert call_args is not None

    async def test_index_failure_returns_false(self, fake_pcm: bytes) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"LMDB error\n")
        mock_proc.returncode = 1

        with (
            patch("app.audio.fingerprint.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("app.audio.fingerprint.Path.unlink"),
        ):
            result = await olaf_index_track(fake_pcm, SAMPLE_TRACK_ID)

        assert result is False

    async def test_index_empty_pcm_returns_false(self) -> None:
        result = await olaf_index_track(b"", SAMPLE_TRACK_ID)
        assert result is False

    async def test_index_binary_not_found_raises(self, fake_pcm: bytes) -> None:
        with (
            patch(
                "app.audio.fingerprint.asyncio.create_subprocess_exec",
                side_effect=FileNotFoundError("olaf_c"),
            ),
            pytest.raises(OlafError, match="binary not found"),
        ):
            await olaf_index_track(fake_pcm, SAMPLE_TRACK_ID)

    async def test_index_sets_olaf_db_env(self, fake_pcm: bytes) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"OK\n", b"")
        mock_proc.returncode = 0

        with (
            patch(
                "app.audio.fingerprint.asyncio.create_subprocess_exec", return_value=mock_proc
            ) as mock_exec,
            patch("app.audio.fingerprint.Path.unlink"),
        ):
            await olaf_index_track(fake_pcm, SAMPLE_TRACK_ID)

        # Verify OLAF_DB was passed in the env
        call_kwargs = mock_exec.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env", {})
        assert "OLAF_DB" in env

    async def test_index_temp_file_cleanup_on_success(self, fake_pcm: bytes) -> None:
        """Verify temp file is cleaned up after successful indexing."""
        created_files: list[str] = []

        original_named_temp = __import__("tempfile").NamedTemporaryFile

        class TrackingTempFile:
            def __init__(self, *args, **kwargs):
                kwargs["delete"] = False
                self._real = original_named_temp(*args, **kwargs)
                created_files.append(self._real.name)

            def __enter__(self):
                self._real.__enter__()
                return self._real

            def __exit__(self, *args):
                return self._real.__exit__(*args)

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"OK\n", b"")
        mock_proc.returncode = 0

        with (
            patch("app.audio.fingerprint.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("app.audio.fingerprint.tempfile.NamedTemporaryFile", TrackingTempFile),
        ):
            await olaf_index_track(fake_pcm, SAMPLE_TRACK_ID)

        # The temp file should have been cleaned up
        for f in created_files:
            assert not Path(f).exists(), f"Temp file {f} was not cleaned up"

    async def test_index_temp_file_cleanup_on_failure(self, fake_pcm: bytes) -> None:
        """Verify temp file is cleaned up even when olaf_c fails."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error\n")
        mock_proc.returncode = 1

        created_files: list[str] = []

        original_named_temp = __import__("tempfile").NamedTemporaryFile

        class TrackingTempFile:
            def __init__(self, *args, **kwargs):
                kwargs["delete"] = False
                self._real = original_named_temp(*args, **kwargs)
                created_files.append(self._real.name)

            def __enter__(self):
                self._real.__enter__()
                return self._real

            def __exit__(self, *args):
                return self._real.__exit__(*args)

        with (
            patch("app.audio.fingerprint.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("app.audio.fingerprint.tempfile.NamedTemporaryFile", TrackingTempFile),
        ):
            await olaf_index_track(fake_pcm, SAMPLE_TRACK_ID)

        for f in created_files:
            assert not Path(f).exists(), f"Temp file {f} was not cleaned up after failure"


# ---------------------------------------------------------------------------
# olaf_query tests
# ---------------------------------------------------------------------------


class TestOlafQuery:
    async def test_query_with_results(self, fake_pcm: bytes) -> None:
        csv_output = b"42, 0.5, 3.2, some-track, 1001, 10.0, 12.7\n"

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (csv_output, b"")
        mock_proc.returncode = 0

        with (
            patch("app.audio.fingerprint.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("app.audio.fingerprint.Path.unlink"),
        ):
            matches = await olaf_query(fake_pcm)

        assert len(matches) == 1
        assert matches[0].match_count == 42
        assert matches[0].reference_path == "some-track"

    async def test_query_empty_results(self, fake_pcm: bytes) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        with (
            patch("app.audio.fingerprint.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("app.audio.fingerprint.Path.unlink"),
        ):
            matches = await olaf_query(fake_pcm)

        assert matches == []

    async def test_query_empty_pcm_returns_empty(self) -> None:
        matches = await olaf_query(b"")
        assert matches == []

    async def test_query_failure_returns_empty(self, fake_pcm: bytes) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error\n")
        mock_proc.returncode = 1

        with (
            patch("app.audio.fingerprint.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("app.audio.fingerprint.Path.unlink"),
        ):
            matches = await olaf_query(fake_pcm)

        assert matches == []

    async def test_query_binary_not_found_raises(self, fake_pcm: bytes) -> None:
        with (
            patch(
                "app.audio.fingerprint.asyncio.create_subprocess_exec",
                side_effect=FileNotFoundError("olaf_c"),
            ),
            pytest.raises(OlafError, match="binary not found"),
        ):
            await olaf_query(fake_pcm)

    async def test_query_multiple_results_sorted(self, fake_pcm: bytes) -> None:
        csv_output = (
            b"5, 0.0, 1.0, track-a, 1, 0.0, 1.0\n"
            b"99, 0.0, 1.0, track-b, 2, 0.0, 1.0\n"
            b"20, 0.0, 1.0, track-c, 3, 0.0, 1.0\n"
        )

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (csv_output, b"")
        mock_proc.returncode = 0

        with (
            patch("app.audio.fingerprint.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("app.audio.fingerprint.Path.unlink"),
        ):
            matches = await olaf_query(fake_pcm)

        assert [m.match_count for m in matches] == [99, 20, 5]


# ---------------------------------------------------------------------------
# olaf_delete_track tests
# ---------------------------------------------------------------------------


class TestOlafDeleteTrack:
    async def test_delete_success(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"OK\n", b"")
        mock_proc.returncode = 0

        with patch(
            "app.audio.fingerprint.asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_exec:
            result = await olaf_delete_track(SAMPLE_TRACK_ID)

        assert result is True
        # Verify "del" command was used
        exec_args = mock_exec.call_args[0]
        assert "del" in exec_args

    async def test_delete_failure_returns_false(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"not found\n")
        mock_proc.returncode = 1

        with patch("app.audio.fingerprint.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await olaf_delete_track(SAMPLE_TRACK_ID)

        assert result is False

    async def test_delete_binary_not_found_raises(self) -> None:
        with (
            patch(
                "app.audio.fingerprint.asyncio.create_subprocess_exec",
                side_effect=FileNotFoundError("olaf_c"),
            ),
            pytest.raises(OlafError, match="binary not found"),
        ):
            await olaf_delete_track(SAMPLE_TRACK_ID)

    async def test_delete_passes_track_id_as_string(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"OK\n", b"")
        mock_proc.returncode = 0

        with patch(
            "app.audio.fingerprint.asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_exec:
            await olaf_delete_track(SAMPLE_TRACK_ID)

        exec_args = mock_exec.call_args[0]
        assert str(SAMPLE_TRACK_ID) in exec_args
