"""Tests for CLAP embedding generation (app.audio.embedding)."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch

from app.audio.embedding import (
    CHUNK_HOP_SEC,
    CHUNK_WINDOW_SEC,
    MIN_CHUNK_SEC,
    SAMPLE_RATE,
    AudioChunk,
    EmbeddingError,
    chunk_audio,
    generate_chunked_embeddings,
    generate_embedding,
    load_clap_model,
)


def _make_pcm_bytes(duration_sec: float, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Create PCM float32 bytes of given duration at given sample rate."""
    num_samples = int(duration_sec * sample_rate)
    # Generate a sine wave so it's not all zeros
    t = np.linspace(0, duration_sec, num_samples, dtype=np.float32)
    audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    return audio.tobytes()


def _make_mock_model(embedding_dim: int = 512) -> MagicMock:
    """Create a mock CLAP model that returns a 512-dim tensor."""
    model = MagicMock()
    model.eval = MagicMock(return_value=model)

    def mock_get_audio_features(**kwargs: object) -> torch.Tensor:
        return torch.randn(1, embedding_dim)

    model.get_audio_features = MagicMock(side_effect=mock_get_audio_features)
    return model


def _make_mock_processor() -> MagicMock:
    """Create a mock CLAP processor."""
    processor = MagicMock()
    processor.return_value = {"input_features": torch.randn(1, 1, 1001, 64)}
    return processor


# ──────────────────────────────────────────────
# chunk_audio tests
# ──────────────────────────────────────────────


class TestChunkAudio:
    def test_30s_audio_expected_chunks(self) -> None:
        """30s audio with 10s window and 5s hop produces 5 chunks.

        Offsets: 0, 5, 10, 15, 20. The chunk starting at 20 covers 20-30s.
        The next would start at 25, which has only 5s left (>=1s min), so 6 total.
        """
        pcm = _make_pcm_bytes(30.0)
        chunks = chunk_audio(pcm)
        # 0s, 5s, 10s, 15s, 20s, 25s (25s has 5s remaining >= 1s min)
        assert len(chunks) == 6

    def test_short_audio_below_min_returns_empty(self) -> None:
        """Audio shorter than MIN_CHUNK_SEC returns empty list."""
        pcm = _make_pcm_bytes(0.5)
        chunks = chunk_audio(pcm)
        assert chunks == []

    def test_exactly_10s_returns_two_chunks(self) -> None:
        """Audio exactly equal to window size produces 2 chunks.

        With 5s hop: chunk at 0s (full 10s) and chunk at 5s (5s padded).
        """
        pcm = _make_pcm_bytes(10.0)
        chunks = chunk_audio(pcm)
        assert len(chunks) == 2
        _, offset, index, duration = chunks[0]
        assert offset == 0.0
        assert index == 0
        assert abs(duration - 10.0) < 0.01
        # Second chunk starts at 5s with 5s of audio
        _, offset2, index2, duration2 = chunks[1]
        assert abs(offset2 - 5.0) < 0.01
        assert index2 == 1
        assert abs(duration2 - 5.0) < 0.01

    def test_15s_returns_three_chunks(self) -> None:
        """15s audio: chunks at 0s, 5s, 10s (three 5s hops)."""
        pcm = _make_pcm_bytes(15.0)
        chunks = chunk_audio(pcm)
        assert len(chunks) == 3
        assert chunks[0][1] == 0.0  # offset
        assert abs(chunks[1][1] - 5.0) < 0.01  # second offset
        assert abs(chunks[2][1] - 10.0) < 0.01  # third offset

    def test_chunk_offsets_and_indices(self) -> None:
        """Verify offset and index values for 25s audio."""
        pcm = _make_pcm_bytes(25.0)
        chunks = chunk_audio(pcm)

        for i, (_, offset, index, _) in enumerate(chunks):
            assert index == i
            assert abs(offset - i * CHUNK_HOP_SEC) < 0.01

    def test_empty_input_returns_empty(self) -> None:
        """Empty PCM bytes returns empty list."""
        chunks = chunk_audio(b"")
        assert chunks == []

    def test_chunk_data_is_padded_for_short_final_chunk(self) -> None:
        """Final chunk shorter than window is zero-padded to window size."""
        pcm = _make_pcm_bytes(12.0)
        chunks = chunk_audio(pcm)
        # 12s with 5s hop: chunks at 0s (10s full), 5s (7s padded), 10s (2s padded)
        assert len(chunks) == 3
        window_samples = int(CHUNK_WINDOW_SEC * SAMPLE_RATE)
        # All chunks should be padded to full window size
        for chunk_data, _, _, _ in chunks:
            assert len(chunk_data) == window_samples

    def test_audio_exactly_min_chunk_returns_one(self) -> None:
        """Audio exactly equal to MIN_CHUNK_SEC should produce 1 chunk."""
        pcm = _make_pcm_bytes(MIN_CHUNK_SEC)
        chunks = chunk_audio(pcm)
        assert len(chunks) == 1

    def test_chunk_audio_preserves_dtype(self) -> None:
        """Chunk audio data should be float32."""
        pcm = _make_pcm_bytes(10.0)
        chunks = chunk_audio(pcm)
        assert chunks[0][0].dtype == np.float32


# ──────────────────────────────────────────────
# generate_embedding tests
# ──────────────────────────────────────────────


class TestGenerateEmbedding:
    def test_returns_512_dim_array(self) -> None:
        """generate_embedding returns a 512-dim numpy array."""
        model = _make_mock_model()
        processor = _make_mock_processor()
        audio = np.random.randn(SAMPLE_RATE * 10).astype(np.float32)

        result = generate_embedding(audio, model, processor)

        assert isinstance(result, np.ndarray)
        assert result.shape == (512,)

    def test_handles_tensor_return_type(self) -> None:
        """Handles case where model returns a plain torch.Tensor."""
        model = MagicMock()
        model.get_audio_features = MagicMock(return_value=torch.randn(1, 512))
        processor = _make_mock_processor()
        audio = np.random.randn(SAMPLE_RATE * 5).astype(np.float32)

        result = generate_embedding(audio, model, processor)

        assert result.shape == (512,)

    def test_handles_pooler_output_return_type(self) -> None:
        """Handles case where model returns object with pooler_output."""
        mock_output = MagicMock()
        mock_output.pooler_output = torch.randn(1, 512)

        model = MagicMock()
        # Make sure isinstance check for torch.Tensor fails
        model.get_audio_features = MagicMock(return_value=mock_output)

        processor = _make_mock_processor()
        audio = np.random.randn(SAMPLE_RATE * 5).astype(np.float32)

        result = generate_embedding(audio, model, processor)

        assert result.shape == (512,)

    def test_handles_last_hidden_state_return_type(self) -> None:
        """Handles case where model returns object with last_hidden_state."""
        mock_output = MagicMock(spec=[])
        mock_output.pooler_output = None
        mock_output.last_hidden_state = torch.randn(1, 10, 512)

        model = MagicMock()
        model.get_audio_features = MagicMock(return_value=mock_output)

        processor = _make_mock_processor()
        audio = np.random.randn(SAMPLE_RATE * 5).astype(np.float32)

        result = generate_embedding(audio, model, processor)

        assert result.shape == (512,)

    def test_raises_embedding_error_on_failure(self) -> None:
        """Raises EmbeddingError when model inference fails."""
        model = MagicMock()
        model.get_audio_features = MagicMock(side_effect=RuntimeError("CUDA OOM"))
        processor = _make_mock_processor()
        audio = np.random.randn(SAMPLE_RATE * 5).astype(np.float32)

        with pytest.raises(EmbeddingError, match="Failed to generate embedding"):
            generate_embedding(audio, model, processor)


# ──────────────────────────────────────────────
# generate_chunked_embeddings tests
# ──────────────────────────────────────────────


class TestGenerateChunkedEmbeddings:
    def test_correct_number_of_chunks(self) -> None:
        """30s audio should produce correct number of AudioChunks."""
        model = _make_mock_model()
        processor = _make_mock_processor()
        pcm = _make_pcm_bytes(30.0)

        result = generate_chunked_embeddings(pcm, model, processor)

        # Same count as chunk_audio
        expected_chunks = len(chunk_audio(pcm))
        assert len(result) == expected_chunks

    def test_each_chunk_has_512_dim_embedding(self) -> None:
        """Each AudioChunk should have a 512-element embedding list."""
        model = _make_mock_model()
        processor = _make_mock_processor()
        pcm = _make_pcm_bytes(15.0)

        result = generate_chunked_embeddings(pcm, model, processor)

        for chunk in result:
            assert isinstance(chunk, AudioChunk)
            assert len(chunk.embedding) == 512
            assert all(isinstance(v, float) for v in chunk.embedding)

    def test_metadata_preserved(self) -> None:
        """Chunk offsets, indices, and durations are correct."""
        model = _make_mock_model()
        processor = _make_mock_processor()
        pcm = _make_pcm_bytes(20.0)

        result = generate_chunked_embeddings(pcm, model, processor)

        for i, chunk in enumerate(result):
            assert chunk.chunk_index == i
            assert abs(chunk.offset_sec - i * CHUNK_HOP_SEC) < 0.01
            assert chunk.duration_sec > 0

    def test_empty_audio_returns_empty_list(self) -> None:
        """Empty PCM produces empty result."""
        model = _make_mock_model()
        processor = _make_mock_processor()

        result = generate_chunked_embeddings(b"", model, processor)

        assert result == []

    def test_short_audio_returns_empty_list(self) -> None:
        """Audio shorter than MIN_CHUNK_SEC returns empty list."""
        model = _make_mock_model()
        processor = _make_mock_processor()
        pcm = _make_pcm_bytes(0.5)

        result = generate_chunked_embeddings(pcm, model, processor)

        assert result == []


# ──────────────────────────────────────────────
# load_clap_model tests
# ──────────────────────────────────────────────


class TestLoadClapModel:
    @patch("app.audio.embedding.ClapProcessor", create=True)
    @patch("app.audio.embedding.ClapModel", create=True)
    def test_load_returns_model_and_processor(
        self,
        mock_clap_model_class: MagicMock,
        mock_clap_processor_class: MagicMock,
    ) -> None:
        """load_clap_model returns a (model, processor) tuple."""
        mock_model = MagicMock()
        mock_processor = MagicMock()

        with (
            patch(
                "transformers.ClapModel.from_pretrained",
                return_value=mock_model,
            ),
            patch(
                "transformers.ClapProcessor.from_pretrained",
                return_value=mock_processor,
            ),
        ):
            model, processor = load_clap_model()

        assert model is not None
        assert processor is not None
