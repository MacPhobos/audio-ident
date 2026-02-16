"""CLAP audio embedding generation with chunked processing.

Uses HuggingFace Transformers CLAP (laion/larger_clap_music_and_speech)
to generate 512-dim embeddings from 48kHz audio. Audio is chunked into
10s windows with 5s hop for fine-grained similarity search.
"""

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch

logger = logging.getLogger(__name__)

# Chunking parameters
CHUNK_WINDOW_SEC: float = 10.0  # CLAP's native input length
CHUNK_HOP_SEC: float = 5.0  # 50% overlap
MIN_CHUNK_SEC: float = 1.0  # Skip chunks shorter than this
SAMPLE_RATE: int = 48000  # CLAP requires 48kHz

MODEL_NAME: str = "laion/larger_clap_music_and_speech"


@dataclass
class AudioChunk:
    """A single audio chunk with embedding."""

    embedding: list[float]  # 512-dim vector
    offset_sec: float  # chunk start time in the track
    chunk_index: int  # sequential index
    duration_sec: float  # actual chunk duration


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""


def load_clap_model() -> tuple[Any, Any]:
    """Load the CLAP model and processor.

    Returns:
        (model, processor) tuple. The types are ClapModel and ClapProcessor
        from HuggingFace Transformers, typed as Any to avoid import at
        module scope.

    Note:
        This should be called ONCE during app startup and stored
        in app.state. Do NOT call in module-level code.
    """
    from transformers import ClapModel, ClapProcessor

    logger.info("Loading CLAP model: %s", MODEL_NAME)
    processor = ClapProcessor.from_pretrained(MODEL_NAME)
    model = ClapModel.from_pretrained(MODEL_NAME)
    model.eval()
    logger.info("CLAP model loaded successfully")
    return model, processor


def generate_embedding(
    audio_48k: np.ndarray,
    model: Any,
    processor: Any,
) -> np.ndarray:
    """Generate a single embedding from audio.

    Args:
        audio_48k: float32 numpy array of audio at 48kHz.
        model: ClapModel instance.
        processor: ClapProcessor instance.

    Returns:
        numpy array of shape (512,).

    Raises:
        EmbeddingError: If embedding generation fails.
    """
    try:
        inputs = processor(audio=[audio_48k], sampling_rate=SAMPLE_RATE, return_tensors="pt")
        with torch.no_grad():
            raw_output = model.get_audio_features(**inputs)

        # Handle varying return types across model versions
        if isinstance(raw_output, torch.Tensor):
            embedding_tensor = raw_output
        elif hasattr(raw_output, "pooler_output") and raw_output.pooler_output is not None:
            embedding_tensor = raw_output.pooler_output
        elif hasattr(raw_output, "last_hidden_state"):
            embedding_tensor = raw_output.last_hidden_state[:, 0, :]
        else:
            embedding_tensor = raw_output

        result: np.ndarray = embedding_tensor.squeeze().numpy()
        return result
    except Exception as e:
        raise EmbeddingError(f"Failed to generate embedding: {e}") from e


def chunk_audio(pcm_48k_f32le: bytes) -> list[tuple[np.ndarray, float, int, float]]:
    """Chunk audio into overlapping windows for embedding.

    Chunks 48kHz float32 PCM into 10s windows with 5s hop.
    Skips chunks shorter than MIN_CHUNK_SEC. Pads final chunk with
    zeros if shorter than window.

    Args:
        pcm_48k_f32le: Raw 48kHz mono float32 little-endian PCM bytes.

    Returns:
        List of (audio_array, offset_sec, chunk_index, duration_sec) tuples.
        The audio_array is a float32 numpy array of the chunk audio data,
        zero-padded to CHUNK_WINDOW_SEC if shorter.
    """
    audio = np.frombuffer(pcm_48k_f32le, dtype=np.float32)

    total_samples = len(audio)
    window_samples = int(CHUNK_WINDOW_SEC * SAMPLE_RATE)
    hop_samples = int(CHUNK_HOP_SEC * SAMPLE_RATE)

    if total_samples == 0:
        return []

    chunks: list[tuple[np.ndarray, float, int, float]] = []
    chunk_index = 0
    start = 0

    while start < total_samples:
        end = min(start + window_samples, total_samples)
        chunk_samples = end - start
        chunk_duration = chunk_samples / SAMPLE_RATE

        # Skip chunks shorter than minimum
        if chunk_duration < MIN_CHUNK_SEC:
            break

        chunk_data = audio[start:end].copy()

        # Zero-pad if shorter than full window
        if chunk_samples < window_samples:
            padded = np.zeros(window_samples, dtype=np.float32)
            padded[:chunk_samples] = chunk_data
            chunk_data = padded

        offset_sec = start / SAMPLE_RATE

        chunks.append((chunk_data, offset_sec, chunk_index, chunk_duration))
        chunk_index += 1
        start += hop_samples

    return chunks


def generate_chunked_embeddings(
    pcm_48k_f32le: bytes,
    model: Any,
    processor: Any,
) -> list[AudioChunk]:
    """Generate embeddings for all chunks of an audio track.

    Args:
        pcm_48k_f32le: Raw 48kHz mono float32 PCM data.
        model: ClapModel instance.
        processor: ClapProcessor instance.

    Returns:
        List of AudioChunk with embeddings populated.

    Raises:
        EmbeddingError: If embedding generation fails for any chunk.
    """
    raw_chunks = chunk_audio(pcm_48k_f32le)

    if not raw_chunks:
        logger.warning("No audio chunks produced (audio may be too short)")
        return []

    result: list[AudioChunk] = []

    for audio_data, offset_sec, chunk_index, duration_sec in raw_chunks:
        embedding = generate_embedding(audio_data, model, processor)

        result.append(
            AudioChunk(
                embedding=embedding.tolist(),
                offset_sec=offset_sec,
                chunk_index=chunk_index,
                duration_sec=duration_sec,
            )
        )

    logger.info("Generated %d chunk embeddings", len(result))
    return result
