"""Audio decoding via FFmpeg subprocess.

Provides async functions to decode audio files to raw PCM using ffmpeg,
supporting dual sample-rate output for Olaf (16kHz) and CLAP (48kHz).
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


class AudioDecodeError(Exception):
    """Raised when audio decoding fails."""


async def decode_to_pcm(
    audio_data: bytes,
    target_sample_rate: int,
    output_format: str = "f32le",
) -> bytes:
    """Decode audio to raw PCM using ffmpeg subprocess.

    Args:
        audio_data: Raw audio file bytes (any format ffmpeg supports).
        target_sample_rate: Output sample rate (e.g. 16000 or 48000).
        output_format: PCM format - ``"f32le"`` for Olaf/CLAP,
            ``"s16le"`` for Chromaprint.

    Returns:
        Raw PCM bytes in the requested format.

    Raises:
        AudioDecodeError: If ffmpeg fails or produces no output.
    """
    if not audio_data:
        raise AudioDecodeError("Empty audio data provided")

    codec = f"pcm_{output_format}"

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-ar",
        str(target_sample_rate),
        "-ac",
        "1",
        "-f",
        output_format,
        "-acodec",
        codec,
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await proc.communicate(input=audio_data)

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace").strip()
        raise AudioDecodeError(f"ffmpeg exited with code {proc.returncode}: {err_msg}")

    if not stdout:
        raise AudioDecodeError("ffmpeg produced no output")

    return stdout


async def decode_dual_rate(audio_data: bytes) -> tuple[bytes, bytes]:
    """Decode to both 16kHz f32le and 48kHz f32le in parallel.

    Returns:
        Tuple of ``(pcm_16k_f32le, pcm_48k_f32le)``.

    Raises:
        AudioDecodeError: If either decode fails.
    """
    pcm_16k, pcm_48k = await asyncio.gather(
        decode_to_pcm(audio_data, target_sample_rate=16000, output_format="f32le"),
        decode_to_pcm(audio_data, target_sample_rate=48000, output_format="f32le"),
    )
    return pcm_16k, pcm_48k


def pcm_duration_seconds(
    pcm_data: bytes,
    sample_rate: int,
    sample_width: int = 4,
) -> float:
    """Calculate duration from PCM bytes.

    Args:
        pcm_data: Raw PCM byte data.
        sample_rate: Sample rate in Hz.
        sample_width: Bytes per sample (4 for f32le, 2 for s16le).

    Returns:
        Duration in seconds.
    """
    return len(pcm_data) / (sample_rate * sample_width)


async def decode_and_validate(
    audio_data: bytes,
    max_duration: float = 1800.0,
    min_duration: float = 0.0,
) -> tuple[bytes, bytes]:
    """Decode dual rate and validate duration constraints.

    Args:
        audio_data: Raw audio file bytes.
        max_duration: Maximum allowed duration in seconds (default 1800 = 30 min).
        min_duration: Minimum allowed duration in seconds (default 0).

    Returns:
        Tuple of ``(pcm_16k_f32le, pcm_48k_f32le)``.

    Raises:
        AudioDecodeError: If decoding fails or duration is outside bounds.
    """
    pcm_16k, pcm_48k = await decode_dual_rate(audio_data)

    duration = pcm_duration_seconds(pcm_16k, sample_rate=16000, sample_width=4)

    if duration < min_duration:
        raise AudioDecodeError(f"Audio too short: {duration:.2f}s < minimum {min_duration:.2f}s")

    if duration > max_duration:
        raise AudioDecodeError(f"Audio too long: {duration:.2f}s > maximum {max_duration:.2f}s")

    return pcm_16k, pcm_48k
