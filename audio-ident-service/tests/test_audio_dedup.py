"""Tests for app.audio.dedup module."""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.audio.dedup import (
    _fingerprint_similarity,
    check_content_duplicate,
    check_file_duplicate,
    f32le_to_s16le,
    generate_chromaprint,
)


class TestCheckFileDuplicate:
    async def test_finds_existing_hash(self) -> None:
        """Returns track_id when file hash exists in the database."""
        expected_id = uuid.uuid4()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = expected_id
        mock_session.execute.return_value = mock_result

        result = await check_file_duplicate(mock_session, "abc123def456")
        assert result == expected_id

    async def test_returns_none_for_new_hash(self) -> None:
        """Returns None when file hash is not in the database."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await check_file_duplicate(mock_session, "brand_new_hash")
        assert result is None


class TestF32leToS16le:
    def test_basic_conversion(self) -> None:
        """Float32 values should scale to int16 range."""
        f32_samples = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
        f32_bytes = f32_samples.tobytes()

        s16_bytes = f32le_to_s16le(f32_bytes)

        s16_samples = np.frombuffer(s16_bytes, dtype=np.int16)
        assert len(s16_samples) == 5
        assert s16_samples[0] == 0  # 0.0 -> 0
        assert s16_samples[1] == 16383  # 0.5 * 32767 ~= 16383
        assert s16_samples[2] == -16383  # -0.5 * 32767 ~= -16383

    def test_output_length(self) -> None:
        """Output should be half the length of input (f32=4 bytes, s16=2 bytes)."""
        f32_data = np.zeros(100, dtype=np.float32).tobytes()
        s16_data = f32le_to_s16le(f32_data)
        assert len(s16_data) == len(f32_data) // 2

    def test_empty_input(self) -> None:
        result = f32le_to_s16le(b"")
        assert result == b""


class TestGenerateChromaprint:
    @pytest.mark.asyncio
    async def test_returns_none_on_empty_input(self) -> None:
        result = await generate_chromaprint(b"", 1.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_fingerprint_on_success(self) -> None:
        """When fpcalc succeeds, returns the fingerprint string."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (
            b"DURATION=5\nFINGERPRINT=123456789,987654321,111222333\n",
            b"",
        )
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch(
            "app.audio.dedup.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            result = await generate_chromaprint(b"\x00" * 32000, 1.0)
        assert result == "123456789,987654321,111222333"

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self) -> None:
        """When fpcalc fails, returns None."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error occurred")
        mock_proc.returncode = 1
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch(
            "app.audio.dedup.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            result = await generate_chromaprint(b"\x00" * 32000, 1.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_fpcalc_missing(self) -> None:
        with patch(
            "app.audio.dedup.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError,
        ):
            result = await generate_chromaprint(b"\x00" * 32000, 1.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self) -> None:
        """When fpcalc times out, returns None."""
        mock_proc = AsyncMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch(
            "app.audio.dedup.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            result = await generate_chromaprint(b"\x00" * 32000, 1.0)
        assert result is None


class TestFingerprintSimilarity:
    def test_identical_fingerprints(self) -> None:
        fp = "100,200,300,400,500"
        assert _fingerprint_similarity(fp, fp) == pytest.approx(1.0)

    def test_completely_different(self) -> None:
        fp1 = "0,0,0,0"
        fp2 = "-1,-1,-1,-1"  # all bits flipped (0xFFFFFFFF)
        similarity = _fingerprint_similarity(fp1, fp2)
        assert similarity < 0.1

    def test_empty_fingerprints(self) -> None:
        assert _fingerprint_similarity("", "") == 0.0

    def test_invalid_fingerprints(self) -> None:
        assert _fingerprint_similarity("abc", "def") == 0.0

    def test_different_lengths_penalized(self) -> None:
        fp_short = "100,200"
        fp_long = "100,200,300,400,500,600,700,800"
        similarity = _fingerprint_similarity(fp_short, fp_long)
        # Even if overlapping portion is identical, length penalty applies
        assert similarity < 1.0
        assert similarity > 0.0

    def test_partial_match(self) -> None:
        # Two fingerprints with some matching and some differing bits
        fp1 = "100,200,300"
        fp2 = "100,201,300"  # middle element differs by 1 bit
        similarity = _fingerprint_similarity(fp1, fp2)
        assert 0.9 < similarity < 1.0


class TestCheckContentDuplicate:
    async def test_finds_matching_track(self) -> None:
        """Returns track_id when a similar fingerprint is found."""
        expected_id = uuid.uuid4()
        fp = "100,200,300,400"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (expected_id, fp, 10.0),
        ]
        mock_session.execute.return_value = mock_result

        result = await check_content_duplicate(
            mock_session,
            fingerprint=fp,
            duration=10.0,
            threshold=0.85,
        )
        assert result == expected_id

    async def test_returns_none_when_no_match(self) -> None:
        """Returns None when no fingerprint exceeds the threshold."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        # Use maximally different fingerprints (all bits flipped)
        mock_result.all.return_value = [
            (uuid.uuid4(), "4294967295,4294967295,4294967295,4294967295", 10.0),
        ]
        mock_session.execute.return_value = mock_result

        result = await check_content_duplicate(
            mock_session,
            fingerprint="0,0,0,0",
            duration=10.0,
            threshold=0.85,
        )
        assert result is None

    async def test_returns_none_when_no_candidates(self) -> None:
        """Returns None when the query returns no rows."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result

        result = await check_content_duplicate(
            mock_session,
            fingerprint="100,200,300",
            duration=10.0,
        )
        assert result is None

    async def test_selects_best_match(self) -> None:
        """When multiple candidates, returns the best match above threshold."""
        best_id = uuid.uuid4()
        other_id = uuid.uuid4()
        fp = "100,200,300,400"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (other_id, "999,888,777,666", 10.0),  # Poor match
            (best_id, fp, 10.0),  # Exact match
        ]
        mock_session.execute.return_value = mock_result

        result = await check_content_duplicate(
            mock_session,
            fingerprint=fp,
            duration=10.0,
            threshold=0.85,
        )
        assert result == best_id
