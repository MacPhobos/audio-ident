"""Tests for app.audio.decode module."""

import io
import struct
import wave

import pytest

from app.audio.decode import (
    AudioDecodeError,
    decode_and_validate,
    decode_dual_rate,
    decode_to_pcm,
    pcm_duration_seconds,
)


def _make_wav_bytes(
    duration_seconds: float = 1.0,
    sample_rate: int = 44100,
    num_channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Create a minimal WAV file in memory with a sine wave.

    Returns raw bytes of a valid WAV file.
    """
    import math

    num_frames = int(sample_rate * duration_seconds)
    buf = io.BytesIO()

    with wave.open(buf, "wb") as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)

        frames = bytearray()
        for i in range(num_frames):
            # Generate a 440 Hz sine wave
            sample = int(16000 * math.sin(2 * math.pi * 440 * i / sample_rate))
            frames.extend(struct.pack("<h", sample))

        wf.writeframes(bytes(frames))

    return buf.getvalue()


@pytest.fixture
def wav_1s() -> bytes:
    """A 1-second WAV file at 44.1kHz mono."""
    return _make_wav_bytes(duration_seconds=1.0)


@pytest.fixture
def wav_5s() -> bytes:
    """A 5-second WAV file at 44.1kHz mono."""
    return _make_wav_bytes(duration_seconds=5.0)


class TestDecodeTopcm:
    async def test_decode_to_16k_f32le(self, wav_1s: bytes) -> None:
        pcm = await decode_to_pcm(wav_1s, target_sample_rate=16000, output_format="f32le")
        assert isinstance(pcm, bytes)
        assert len(pcm) > 0
        # f32le: 4 bytes per sample, 16000 samples/sec, ~1 second
        expected_bytes = 16000 * 4  # 1 second at 16kHz f32le
        # Allow 5% tolerance for resampling
        assert abs(len(pcm) - expected_bytes) < expected_bytes * 0.05

    async def test_decode_to_48k_f32le(self, wav_1s: bytes) -> None:
        pcm = await decode_to_pcm(wav_1s, target_sample_rate=48000, output_format="f32le")
        assert isinstance(pcm, bytes)
        expected_bytes = 48000 * 4
        assert abs(len(pcm) - expected_bytes) < expected_bytes * 0.05

    async def test_decode_to_s16le(self, wav_1s: bytes) -> None:
        pcm = await decode_to_pcm(wav_1s, target_sample_rate=16000, output_format="s16le")
        assert isinstance(pcm, bytes)
        # s16le: 2 bytes per sample
        expected_bytes = 16000 * 2
        assert abs(len(pcm) - expected_bytes) < expected_bytes * 0.05

    async def test_decode_empty_input_raises(self) -> None:
        with pytest.raises(AudioDecodeError, match="Empty audio data"):
            await decode_to_pcm(b"", target_sample_rate=16000)

    async def test_decode_corrupt_input_raises(self) -> None:
        with pytest.raises(AudioDecodeError):
            await decode_to_pcm(b"not audio data at all", target_sample_rate=16000)


class TestDecodeDualRate:
    async def test_returns_two_outputs(self, wav_1s: bytes) -> None:
        pcm_16k, pcm_48k = await decode_dual_rate(wav_1s)
        assert isinstance(pcm_16k, bytes)
        assert isinstance(pcm_48k, bytes)
        assert len(pcm_16k) > 0
        assert len(pcm_48k) > 0

    async def test_output_sizes_match_sample_rates(self, wav_1s: bytes) -> None:
        pcm_16k, pcm_48k = await decode_dual_rate(wav_1s)
        # 48kHz should produce ~3x more data than 16kHz (same format, same duration)
        ratio = len(pcm_48k) / len(pcm_16k)
        assert 2.8 < ratio < 3.2


class TestPcmDurationSeconds:
    def test_f32le_duration(self) -> None:
        # 1 second of 16kHz f32le = 16000 * 4 bytes
        pcm = bytes(16000 * 4)
        duration = pcm_duration_seconds(pcm, sample_rate=16000, sample_width=4)
        assert duration == pytest.approx(1.0)

    def test_s16le_duration(self) -> None:
        # 1 second of 16kHz s16le = 16000 * 2 bytes
        pcm = bytes(16000 * 2)
        duration = pcm_duration_seconds(pcm, sample_rate=16000, sample_width=2)
        assert duration == pytest.approx(1.0)

    def test_48k_duration(self) -> None:
        # 2 seconds of 48kHz f32le = 48000 * 4 * 2 bytes
        pcm = bytes(48000 * 4 * 2)
        duration = pcm_duration_seconds(pcm, sample_rate=48000, sample_width=4)
        assert duration == pytest.approx(2.0)

    def test_empty_pcm(self) -> None:
        duration = pcm_duration_seconds(b"", sample_rate=16000, sample_width=4)
        assert duration == 0.0


class TestDecodeAndValidate:
    async def test_valid_audio_passes(self, wav_1s: bytes) -> None:
        pcm_16k, pcm_48k = await decode_and_validate(wav_1s, max_duration=10.0, min_duration=0.5)
        assert len(pcm_16k) > 0
        assert len(pcm_48k) > 0

    async def test_too_short_raises(self, wav_1s: bytes) -> None:
        with pytest.raises(AudioDecodeError, match="too short"):
            await decode_and_validate(wav_1s, min_duration=5.0)

    async def test_too_long_raises(self, wav_5s: bytes) -> None:
        with pytest.raises(AudioDecodeError, match="too long"):
            await decode_and_validate(wav_5s, max_duration=2.0)

    async def test_default_bounds_pass_normal_audio(self, wav_5s: bytes) -> None:
        pcm_16k, pcm_48k = await decode_and_validate(wav_5s)
        assert len(pcm_16k) > 0
        assert len(pcm_48k) > 0
