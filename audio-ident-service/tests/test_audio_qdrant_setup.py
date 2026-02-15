"""Tests for Qdrant collection management (app.audio.qdrant_setup)."""

import uuid
from unittest.mock import MagicMock, patch

from qdrant_client import models

from app.audio.embedding import AudioChunk
from app.audio.qdrant_setup import (
    BATCH_SIZE,
    delete_track_embeddings,
    ensure_collection,
    upsert_track_embeddings,
)


def _make_chunks(count: int, embedding_dim: int = 512) -> list[AudioChunk]:
    """Create a list of AudioChunks with random embeddings."""
    chunks = []
    for i in range(count):
        chunks.append(
            AudioChunk(
                embedding=[float(x) for x in range(embedding_dim)],
                offset_sec=i * 5.0,
                chunk_index=i,
                duration_sec=10.0,
            )
        )
    return chunks


def _make_mock_client(collection_exists: bool = False) -> MagicMock:
    """Create a mock Qdrant client.

    Args:
        collection_exists: If True, the mock returns a collection named
            'audio_embeddings' in get_collections().
    """
    client = MagicMock()

    # Setup get_collections response
    collections_response = MagicMock()
    if collection_exists:
        existing = MagicMock()
        existing.name = "audio_embeddings"
        collections_response.collections = [existing]
    else:
        collections_response.collections = []

    client.get_collections.return_value = collections_response
    return client


# ──────────────────────────────────────────────
# ensure_collection tests
# ──────────────────────────────────────────────


class TestEnsureCollection:
    @patch("app.audio.qdrant_setup.settings")
    def test_creates_collection_if_not_exists(self, mock_settings: MagicMock) -> None:
        """Creates collection when it does not exist."""
        mock_settings.qdrant_collection_name = "audio_embeddings"
        mock_settings.embedding_dim = 512

        client = _make_mock_client(collection_exists=False)
        ensure_collection(client)

        client.create_collection.assert_called_once()
        # Verify the collection name
        call_kwargs = client.create_collection.call_args
        assert call_kwargs.kwargs["collection_name"] == "audio_embeddings"

    @patch("app.audio.qdrant_setup.settings")
    def test_skips_creation_if_exists(self, mock_settings: MagicMock) -> None:
        """Skips collection creation when it already exists."""
        mock_settings.qdrant_collection_name = "audio_embeddings"

        client = _make_mock_client(collection_exists=True)
        ensure_collection(client)

        client.create_collection.assert_not_called()

    @patch("app.audio.qdrant_setup.settings")
    def test_correct_vector_params(self, mock_settings: MagicMock) -> None:
        """Verifies correct vector config: 512 dim, cosine distance."""
        mock_settings.qdrant_collection_name = "audio_embeddings"
        mock_settings.embedding_dim = 512

        client = _make_mock_client(collection_exists=False)
        ensure_collection(client)

        call_kwargs = client.create_collection.call_args.kwargs
        vectors_config = call_kwargs["vectors_config"]
        assert vectors_config.size == 512
        assert vectors_config.distance == models.Distance.COSINE

    @patch("app.audio.qdrant_setup.settings")
    def test_hnsw_config(self, mock_settings: MagicMock) -> None:
        """Verifies HNSW config: m=16, ef_construct=200."""
        mock_settings.qdrant_collection_name = "audio_embeddings"
        mock_settings.embedding_dim = 512

        client = _make_mock_client(collection_exists=False)
        ensure_collection(client)

        call_kwargs = client.create_collection.call_args.kwargs
        hnsw_config = call_kwargs["hnsw_config"]
        assert hnsw_config.m == 16
        assert hnsw_config.ef_construct == 200

    @patch("app.audio.qdrant_setup.settings")
    def test_quantization_config(self, mock_settings: MagicMock) -> None:
        """Verifies INT8 scalar quantization config."""
        mock_settings.qdrant_collection_name = "audio_embeddings"
        mock_settings.embedding_dim = 512

        client = _make_mock_client(collection_exists=False)
        ensure_collection(client)

        call_kwargs = client.create_collection.call_args.kwargs
        quant_config = call_kwargs["quantization_config"]
        assert isinstance(quant_config, models.ScalarQuantization)
        assert quant_config.scalar.type == models.ScalarType.INT8
        assert quant_config.scalar.quantile == 0.99
        assert quant_config.scalar.always_ram is True

    @patch("app.audio.qdrant_setup.settings")
    def test_creates_payload_indexes(self, mock_settings: MagicMock) -> None:
        """Creates payload indexes on track_id and genre."""
        mock_settings.qdrant_collection_name = "audio_embeddings"
        mock_settings.embedding_dim = 512

        client = _make_mock_client(collection_exists=False)
        ensure_collection(client)

        # Should create exactly 2 payload indexes
        assert client.create_payload_index.call_count == 2

        index_calls = client.create_payload_index.call_args_list
        field_names = [c.kwargs["field_name"] for c in index_calls]
        assert "track_id" in field_names
        assert "genre" in field_names


# ──────────────────────────────────────────────
# upsert_track_embeddings tests
# ──────────────────────────────────────────────


class TestUpsertTrackEmbeddings:
    @patch("app.audio.qdrant_setup.settings")
    @patch("app.audio.qdrant_setup.ensure_collection")
    def test_upserts_correct_number_of_points(
        self,
        mock_ensure: MagicMock,
        mock_settings: MagicMock,
    ) -> None:
        """Upserts the correct number of points."""
        mock_settings.qdrant_collection_name = "audio_embeddings"

        client = MagicMock()
        track_id = uuid.uuid4()
        chunks = _make_chunks(5)

        count = upsert_track_embeddings(client, track_id, chunks)

        assert count == 5
        client.upsert.assert_called_once()

    @patch("app.audio.qdrant_setup.settings")
    @patch("app.audio.qdrant_setup.ensure_collection")
    def test_batches_large_upserts(
        self,
        mock_ensure: MagicMock,
        mock_settings: MagicMock,
    ) -> None:
        """Batches correctly when more than BATCH_SIZE chunks."""
        mock_settings.qdrant_collection_name = "audio_embeddings"

        client = MagicMock()
        track_id = uuid.uuid4()
        # Create more chunks than batch size
        chunks = _make_chunks(BATCH_SIZE + 50)

        count = upsert_track_embeddings(client, track_id, chunks)

        assert count == BATCH_SIZE + 50
        # Should have 2 upsert calls (100 + 50)
        assert client.upsert.call_count == 2

    @patch("app.audio.qdrant_setup.settings")
    @patch("app.audio.qdrant_setup.ensure_collection")
    def test_payload_has_correct_fields(
        self,
        mock_ensure: MagicMock,
        mock_settings: MagicMock,
    ) -> None:
        """Each upserted point has correct payload fields."""
        mock_settings.qdrant_collection_name = "audio_embeddings"

        client = MagicMock()
        track_id = uuid.uuid4()
        chunks = _make_chunks(1)
        metadata = {"artist": "Test Artist", "title": "Test Track", "genre": "Rock"}

        upsert_track_embeddings(client, track_id, chunks, metadata=metadata)

        # Extract the points passed to upsert
        upsert_call = client.upsert.call_args
        points = upsert_call.kwargs["points"]
        assert len(points) == 1

        payload = points[0].payload
        assert payload["track_id"] == str(track_id)
        assert payload["offset_sec"] == 0.0
        assert payload["chunk_index"] == 0
        assert payload["duration_sec"] == 10.0
        assert payload["artist"] == "Test Artist"
        assert payload["title"] == "Test Track"
        assert payload["genre"] == "Rock"

    @patch("app.audio.qdrant_setup.settings")
    @patch("app.audio.qdrant_setup.ensure_collection")
    def test_returns_count_of_upserted_points(
        self,
        mock_ensure: MagicMock,
        mock_settings: MagicMock,
    ) -> None:
        """Returns the number of upserted points."""
        mock_settings.qdrant_collection_name = "audio_embeddings"

        client = MagicMock()
        track_id = uuid.uuid4()
        chunks = _make_chunks(7)

        count = upsert_track_embeddings(client, track_id, chunks)

        assert count == 7

    @patch("app.audio.qdrant_setup.settings")
    @patch("app.audio.qdrant_setup.ensure_collection")
    def test_empty_chunks_returns_zero(
        self,
        mock_ensure: MagicMock,
        mock_settings: MagicMock,
    ) -> None:
        """Empty chunk list returns 0 without calling upsert."""
        mock_settings.qdrant_collection_name = "audio_embeddings"

        client = MagicMock()
        track_id = uuid.uuid4()

        count = upsert_track_embeddings(client, track_id, [])

        assert count == 0
        client.upsert.assert_not_called()

    @patch("app.audio.qdrant_setup.settings")
    @patch("app.audio.qdrant_setup.ensure_collection")
    def test_no_metadata_omits_fields(
        self,
        mock_ensure: MagicMock,
        mock_settings: MagicMock,
    ) -> None:
        """When no metadata provided, payload only has core fields."""
        mock_settings.qdrant_collection_name = "audio_embeddings"

        client = MagicMock()
        track_id = uuid.uuid4()
        chunks = _make_chunks(1)

        upsert_track_embeddings(client, track_id, chunks)

        upsert_call = client.upsert.call_args
        points = upsert_call.kwargs["points"]
        payload = points[0].payload

        assert "track_id" in payload
        assert "offset_sec" in payload
        assert "chunk_index" in payload
        assert "duration_sec" in payload
        assert "artist" not in payload
        assert "title" not in payload
        assert "genre" not in payload


# ──────────────────────────────────────────────
# delete_track_embeddings tests
# ──────────────────────────────────────────────


class TestDeleteTrackEmbeddings:
    @patch("app.audio.qdrant_setup.settings")
    def test_calls_delete_with_correct_filter(self, mock_settings: MagicMock) -> None:
        """Calls client.delete with a filter on track_id."""
        mock_settings.qdrant_collection_name = "audio_embeddings"

        client = MagicMock()
        track_id = uuid.uuid4()

        delete_track_embeddings(client, track_id)

        client.delete.assert_called_once()
        delete_call = client.delete.call_args

        assert delete_call.kwargs["collection_name"] == "audio_embeddings"

        # Verify the filter structure
        selector = delete_call.kwargs["points_selector"]
        assert isinstance(selector, models.FilterSelector)
        filter_obj = selector.filter
        assert len(filter_obj.must) == 1
        condition = filter_obj.must[0]
        assert condition.key == "track_id"
        assert condition.match.value == str(track_id)
