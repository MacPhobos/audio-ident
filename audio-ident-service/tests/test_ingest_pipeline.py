"""Tests for the ingestion pipeline orchestration.

All external dependencies (DB, Qdrant, CLAP, Olaf, ffmpeg) are mocked.
"""

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.audio.embedding import EmbeddingError
from app.ingest.pipeline import (
    AUDIO_EXTENSIONS,
    MAX_INGESTION_DURATION,
    MIN_INGESTION_DURATION,
    IngestReport,
    IngestResult,
    ingest_directory,
    ingest_file,
)

# Mock settings before tests run
_MOCK_SETTINGS = MagicMock()
_MOCK_SETTINGS.embedding_model = "clap-htsat-large"
_MOCK_SETTINGS.embedding_dim = 512

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session():
    """Create a mock async session that supports context manager usage."""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def mock_session_factory(mock_session):
    """Create a mock session factory returning an async context manager."""

    @asynccontextmanager
    async def _factory():
        yield mock_session

    # We need a callable that returns an async context manager.
    # async_sessionmaker() returns an AsyncSession context manager.
    class _MockSessionFactory:
        def __call__(self):
            return _factory()

    return _MockSessionFactory()


@pytest.fixture
def mock_clap_model():
    """Mock CLAP model."""
    return MagicMock(name="mock_clap_model")


@pytest.fixture
def mock_clap_processor():
    """Mock CLAP processor."""
    return MagicMock(name="mock_clap_processor")


@pytest.fixture
def mock_qdrant_client():
    """Mock Qdrant client."""
    client = MagicMock(name="mock_qdrant_client")
    client.get_collections.return_value = MagicMock(collections=[])
    return client


@pytest.fixture
def temp_audio_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with fake audio files."""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()

    # Create dummy audio files (content doesn't matter for mocked tests)
    for name in ["song1.mp3", "song2.wav", "song3.flac"]:
        (audio_dir / name).write_bytes(b"\x00" * 1024)

    # Create a non-audio file that should be ignored
    (audio_dir / "readme.txt").write_text("not audio")

    return audio_dir


@pytest.fixture
def temp_single_file(tmp_path: Path) -> Path:
    """Create a single temporary audio file."""
    audio_file = tmp_path / "test_track.mp3"
    audio_file.write_bytes(b"\xff\xfb\x90\x00" * 256)
    return audio_file


# Helper: PCM data for a specific duration at 16kHz f32le (4 bytes/sample)
def pcm_for_duration(seconds: float, sample_rate: int = 16000) -> bytes:
    """Generate silent PCM bytes for a given duration."""
    num_samples = int(seconds * sample_rate)
    return b"\x00\x00\x00\x00" * num_samples  # 4 bytes per f32le sample


# ---------------------------------------------------------------------------
# Tests for ingest_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_ingestion(
    temp_single_file,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """All pipeline steps succeed -> status='success'."""
    pcm_16k = pcm_for_duration(10.0, 16000)
    pcm_48k = pcm_for_duration(10.0, 48000)
    track_uuid = uuid.uuid4()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    with (
        patch("app.ingest.pipeline.compute_file_hash", return_value="abcdef1234567890" * 4),
        patch(
            "app.ingest.pipeline.check_file_duplicate",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.ingest.pipeline.extract_metadata",
            return_value=MagicMock(
                title="Test Song",
                artist="Test Artist",
                album="Test Album",
                sample_rate=44100,
                channels=2,
                bitrate=320000,
                format="mp3",
                file_size_bytes=1024,
            ),
        ),
        patch(
            "app.ingest.pipeline.decode_dual_rate",
            new_callable=AsyncMock,
            return_value=(pcm_16k, pcm_48k),
        ),
        patch("app.ingest.pipeline.raw_audio_path", return_value=Path("/tmp/fake.mp3")),
        patch("app.ingest.pipeline.ensure_storage_dirs"),
        patch("shutil.copy2"),
        patch("app.ingest.pipeline.f32le_to_s16le", return_value=b"\x00" * 100),
        patch(
            "app.ingest.pipeline.generate_chromaprint",
            new_callable=AsyncMock,
            return_value="1234,5678,9012",
        ),
        patch(
            "app.ingest.pipeline.check_content_duplicate",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.ingest.pipeline.olaf_index_track",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.ingest.pipeline.generate_chunked_embeddings",
            return_value=[
                MagicMock(embedding=[0.1] * 512, offset_sec=0.0, chunk_index=0, duration_sec=10.0)
            ],
        ),
        patch("app.ingest.pipeline.upsert_track_embeddings", return_value=1),
        patch("app.ingest.pipeline.uuid.uuid4", return_value=track_uuid),
        patch("app.ingest.pipeline.settings", _MOCK_SETTINGS),
    ):
        result = await ingest_file(
            temp_single_file,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    assert result.status == "success"
    assert result.track_id == track_uuid
    assert result.duration_seconds == pytest.approx(10.0, abs=0.1)


@pytest.mark.asyncio
async def test_file_hash_duplicate_detected(
    temp_single_file,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """Step 2: file hash already in DB -> status='duplicate'."""
    existing_uuid = uuid.uuid4()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    with (
        patch("app.ingest.pipeline.compute_file_hash", return_value="abcdef1234567890" * 4),
        patch(
            "app.ingest.pipeline.check_file_duplicate",
            new_callable=AsyncMock,
            return_value=existing_uuid,
        ),
    ):
        result = await ingest_file(
            temp_single_file,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    assert result.status == "duplicate"
    assert result.track_id == existing_uuid


@pytest.mark.asyncio
async def test_content_duplicate_detected(
    temp_single_file,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """Chromaprint finds a content match -> status='duplicate'.

    The dedup check now runs BEFORE Olaf/CLAP indexing, so no cleanup is needed.
    """
    content_dup_uuid = uuid.uuid4()
    pcm_16k = pcm_for_duration(10.0, 16000)
    pcm_48k = pcm_for_duration(10.0, 48000)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    with (
        patch("app.ingest.pipeline.compute_file_hash", return_value="abcdef1234567890" * 4),
        patch(
            "app.ingest.pipeline.check_file_duplicate",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.ingest.pipeline.extract_metadata",
            return_value=MagicMock(
                title="Song",
                artist="Artist",
                album=None,
                sample_rate=44100,
                channels=2,
                bitrate=320000,
                format="mp3",
                file_size_bytes=1024,
            ),
        ),
        patch(
            "app.ingest.pipeline.decode_dual_rate",
            new_callable=AsyncMock,
            return_value=(pcm_16k, pcm_48k),
        ),
        patch("app.ingest.pipeline.raw_audio_path", return_value=Path("/tmp/fake.mp3")),
        patch("app.ingest.pipeline.ensure_storage_dirs"),
        patch("shutil.copy2"),
        patch("app.ingest.pipeline.f32le_to_s16le", return_value=b"\x00" * 100),
        patch(
            "app.ingest.pipeline.generate_chromaprint",
            new_callable=AsyncMock,
            return_value="1234,5678",
        ),
        patch(
            "app.ingest.pipeline.check_content_duplicate",
            new_callable=AsyncMock,
            return_value=content_dup_uuid,
        ),
        patch(
            "app.ingest.pipeline.olaf_index_track",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_olaf,
    ):
        result = await ingest_file(
            temp_single_file,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    assert result.status == "duplicate"
    assert result.track_id == content_dup_uuid
    # Olaf should NOT be called since dedup runs before indexing
    mock_olaf.assert_not_called()


@pytest.mark.asyncio
async def test_decode_error_returns_error_status(
    temp_single_file,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """FFmpeg decode failure -> status='error'. Raw file should not be saved."""
    from app.audio.decode import AudioDecodeError

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    with (
        patch("app.ingest.pipeline.compute_file_hash", return_value="abcdef1234567890" * 4),
        patch(
            "app.ingest.pipeline.check_file_duplicate",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.ingest.pipeline.extract_metadata",
            return_value=MagicMock(
                title="Song",
                artist=None,
                album=None,
                sample_rate=None,
                channels=None,
                bitrate=None,
                format="mp3",
                file_size_bytes=1024,
            ),
        ),
        patch(
            "app.ingest.pipeline.decode_dual_rate",
            new_callable=AsyncMock,
            side_effect=AudioDecodeError("ffmpeg crashed"),
        ),
    ):
        result = await ingest_file(
            temp_single_file,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    assert result.status == "error"
    assert result.error is not None
    assert "Decode error" in result.error


@pytest.mark.asyncio
async def test_too_short_file_skipped(
    temp_single_file,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """Audio shorter than MIN_INGESTION_DURATION -> status='skipped'.

    Raw file should NOT be saved since duration validation fails first.
    """
    pcm_16k = pcm_for_duration(1.0, 16000)  # 1 second < 3 second min
    pcm_48k = pcm_for_duration(1.0, 48000)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    with (
        patch("app.ingest.pipeline.compute_file_hash", return_value="abcdef1234567890" * 4),
        patch(
            "app.ingest.pipeline.check_file_duplicate",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.ingest.pipeline.extract_metadata",
            return_value=MagicMock(
                title="Short",
                artist=None,
                album=None,
                sample_rate=44100,
                channels=2,
                bitrate=128000,
                format="mp3",
                file_size_bytes=512,
            ),
        ),
        patch(
            "app.ingest.pipeline.decode_dual_rate",
            new_callable=AsyncMock,
            return_value=(pcm_16k, pcm_48k),
        ),
    ):
        result = await ingest_file(
            temp_single_file,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    assert result.status == "skipped"
    assert result.error is not None
    assert "Too short" in result.error
    assert result.duration_seconds == pytest.approx(1.0, abs=0.1)


@pytest.mark.asyncio
async def test_too_long_file_skipped(
    temp_single_file,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """Audio longer than MAX_INGESTION_DURATION -> status='skipped'.

    Raw file should NOT be saved since duration validation fails first.
    """
    pcm_16k = pcm_for_duration(2000.0, 16000)  # 2000s > 1800s max
    pcm_48k = pcm_for_duration(2000.0, 48000)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    with (
        patch("app.ingest.pipeline.compute_file_hash", return_value="abcdef1234567890" * 4),
        patch(
            "app.ingest.pipeline.check_file_duplicate",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.ingest.pipeline.extract_metadata",
            return_value=MagicMock(
                title="Long",
                artist=None,
                album=None,
                sample_rate=44100,
                channels=2,
                bitrate=128000,
                format="mp3",
                file_size_bytes=1024000,
            ),
        ),
        patch(
            "app.ingest.pipeline.decode_dual_rate",
            new_callable=AsyncMock,
            return_value=(pcm_16k, pcm_48k),
        ),
    ):
        result = await ingest_file(
            temp_single_file,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    assert result.status == "skipped"
    assert result.error is not None
    assert "Too long" in result.error


@pytest.mark.asyncio
async def test_olaf_failure_continues(
    temp_single_file,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """Olaf indexing fails -> ingestion still succeeds (partial failure)."""
    pcm_16k = pcm_for_duration(10.0, 16000)
    pcm_48k = pcm_for_duration(10.0, 48000)
    track_uuid = uuid.uuid4()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    with (
        patch("app.ingest.pipeline.compute_file_hash", return_value="abcdef1234567890" * 4),
        patch(
            "app.ingest.pipeline.check_file_duplicate",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.ingest.pipeline.extract_metadata",
            return_value=MagicMock(
                title="Song",
                artist="Artist",
                album=None,
                sample_rate=44100,
                channels=2,
                bitrate=320000,
                format="mp3",
                file_size_bytes=1024,
            ),
        ),
        patch(
            "app.ingest.pipeline.decode_dual_rate",
            new_callable=AsyncMock,
            return_value=(pcm_16k, pcm_48k),
        ),
        patch("app.ingest.pipeline.raw_audio_path", return_value=Path("/tmp/fake.mp3")),
        patch("app.ingest.pipeline.ensure_storage_dirs"),
        patch("shutil.copy2"),
        patch("app.ingest.pipeline.f32le_to_s16le", return_value=b"\x00" * 100),
        patch(
            "app.ingest.pipeline.generate_chromaprint",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.ingest.pipeline.olaf_index_track",
            new_callable=AsyncMock,
            side_effect=Exception("Olaf LMDB error"),
        ),
        patch(
            "app.ingest.pipeline.generate_chunked_embeddings",
            return_value=[
                MagicMock(embedding=[0.1] * 512, offset_sec=0.0, chunk_index=0, duration_sec=10.0)
            ],
        ),
        patch("app.ingest.pipeline.upsert_track_embeddings", return_value=1),
        patch("app.ingest.pipeline.uuid.uuid4", return_value=track_uuid),
        patch("app.ingest.pipeline.settings", _MOCK_SETTINGS),
    ):
        result = await ingest_file(
            temp_single_file,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    # Olaf failure is not fatal; track still ingested
    assert result.status == "success"
    assert result.track_id == track_uuid


@pytest.mark.asyncio
async def test_embedding_failure_continues(
    temp_single_file,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """CLAP embedding failure -> ingestion still succeeds (partial)."""
    pcm_16k = pcm_for_duration(10.0, 16000)
    pcm_48k = pcm_for_duration(10.0, 48000)
    track_uuid = uuid.uuid4()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    with (
        patch("app.ingest.pipeline.compute_file_hash", return_value="abcdef1234567890" * 4),
        patch(
            "app.ingest.pipeline.check_file_duplicate",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.ingest.pipeline.extract_metadata",
            return_value=MagicMock(
                title="Song",
                artist="Artist",
                album=None,
                sample_rate=44100,
                channels=2,
                bitrate=320000,
                format="mp3",
                file_size_bytes=1024,
            ),
        ),
        patch(
            "app.ingest.pipeline.decode_dual_rate",
            new_callable=AsyncMock,
            return_value=(pcm_16k, pcm_48k),
        ),
        patch("app.ingest.pipeline.raw_audio_path", return_value=Path("/tmp/fake.mp3")),
        patch("app.ingest.pipeline.ensure_storage_dirs"),
        patch("shutil.copy2"),
        patch("app.ingest.pipeline.f32le_to_s16le", return_value=b"\x00" * 100),
        patch(
            "app.ingest.pipeline.generate_chromaprint",
            new_callable=AsyncMock,
            return_value="1234,5678",
        ),
        patch(
            "app.ingest.pipeline.check_content_duplicate",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.ingest.pipeline.olaf_index_track",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.ingest.pipeline.generate_chunked_embeddings",
            side_effect=EmbeddingError("CLAP crashed"),
        ),
        patch("app.ingest.pipeline.uuid.uuid4", return_value=track_uuid),
        patch("app.ingest.pipeline.settings", _MOCK_SETTINGS),
    ):
        result = await ingest_file(
            temp_single_file,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    # Embedding failure is not fatal; track still ingested with 0 embeddings
    assert result.status == "success"
    assert result.track_id == track_uuid


@pytest.mark.asyncio
async def test_unexpected_error_returns_error_status(
    temp_single_file,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
):
    """Unexpected exception -> status='error' with message."""

    class _Factory:
        def __call__(self):
            raise RuntimeError("DB connection refused")

    factory = _Factory()

    with patch(
        "app.ingest.pipeline.compute_file_hash",
        side_effect=RuntimeError("disk error"),
    ):
        result = await ingest_file(
            temp_single_file,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    assert result.status == "error"
    assert result.error is not None
    assert "Unexpected error" in result.error


@pytest.mark.asyncio
async def test_no_metadata_title_uses_filename(
    temp_single_file,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """When metadata has no title, use the filename stem instead."""
    pcm_16k = pcm_for_duration(10.0, 16000)
    pcm_48k = pcm_for_duration(10.0, 48000)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    with (
        patch("app.ingest.pipeline.compute_file_hash", return_value="abcdef1234567890" * 4),
        patch(
            "app.ingest.pipeline.check_file_duplicate",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.ingest.pipeline.extract_metadata",
            return_value=MagicMock(
                title=None,  # No title in metadata
                artist=None,
                album=None,
                sample_rate=44100,
                channels=2,
                bitrate=320000,
                format="mp3",
                file_size_bytes=1024,
            ),
        ),
        patch(
            "app.ingest.pipeline.decode_dual_rate",
            new_callable=AsyncMock,
            return_value=(pcm_16k, pcm_48k),
        ),
        patch("app.ingest.pipeline.raw_audio_path", return_value=Path("/tmp/fake.mp3")),
        patch("app.ingest.pipeline.ensure_storage_dirs"),
        patch("shutil.copy2"),
        patch("app.ingest.pipeline.f32le_to_s16le", return_value=b"\x00" * 100),
        patch(
            "app.ingest.pipeline.generate_chromaprint",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.ingest.pipeline.olaf_index_track",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("app.ingest.pipeline.generate_chunked_embeddings", return_value=[]),
        patch("app.ingest.pipeline.upsert_track_embeddings", return_value=0),
        patch("app.ingest.pipeline.settings", _MOCK_SETTINGS),
    ):
        result = await ingest_file(
            temp_single_file,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    assert result.status == "success"
    # The title should fall back to the filename stem
    # We verify the Track was added to session with correct title
    add_call = mock_session.add
    assert add_call.called
    track_arg = add_call.call_args[0][0]
    assert track_arg.title == temp_single_file.stem


# ---------------------------------------------------------------------------
# Tests for ingest_directory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scans_correct_extensions(
    temp_audio_dir,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """Only files with audio extensions are picked up."""
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    with (
        patch(
            "app.ingest.pipeline.ingest_file",
            new_callable=AsyncMock,
            return_value=IngestResult(file_path="x", status="success"),
        ) as mock_ingest,
        patch("app.ingest.pipeline.ensure_collection"),
    ):
        report = await ingest_directory(
            temp_audio_dir,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    # song1.mp3, song2.wav, song3.flac = 3 audio files
    # readme.txt should be ignored
    assert report.total_files == 3
    assert mock_ingest.call_count == 3


@pytest.mark.asyncio
async def test_empty_directory(
    tmp_path,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """Empty directory -> report with total_files=0."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    class _Factory:
        def __call__(self):
            raise AssertionError("Should not be called")

    factory = _Factory()

    report = await ingest_directory(
        empty_dir,
        mock_clap_model,
        mock_clap_processor,
        mock_qdrant_client,
        factory,
    )

    assert report.total_files == 0
    assert report.ingested == 0
    assert report.duplicates == 0
    assert report.skipped == 0
    assert report.errors == 0


@pytest.mark.asyncio
async def test_report_counts_correct(
    temp_audio_dir,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """Report correctly tallies success/duplicate/error counts."""
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    # Return different statuses for each file
    results = [
        IngestResult(file_path="song1.mp3", status="success", track_id=uuid.uuid4()),
        IngestResult(file_path="song2.wav", status="duplicate", track_id=uuid.uuid4()),
        IngestResult(file_path="song3.flac", status="error", error="boom"),
    ]
    call_count = 0

    async def mock_ingest(*args, **kwargs):
        nonlocal call_count
        r = results[call_count]
        call_count += 1
        return r

    with (
        patch("app.ingest.pipeline.ingest_file", side_effect=mock_ingest),
        patch("app.ingest.pipeline.ensure_collection"),
    ):
        report = await ingest_directory(
            temp_audio_dir,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    assert report.total_files == 3
    assert report.ingested == 1
    assert report.duplicates == 1
    assert report.errors == 1
    assert len(report.results) == 3


@pytest.mark.asyncio
async def test_error_does_not_halt_batch(
    temp_audio_dir,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """An error on one file doesn't stop processing the rest."""
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    results = [
        IngestResult(file_path="song1.mp3", status="error", error="boom"),
        IngestResult(file_path="song2.wav", status="success", track_id=uuid.uuid4()),
        IngestResult(file_path="song3.flac", status="success", track_id=uuid.uuid4()),
    ]
    call_count = 0

    async def mock_ingest(*args, **kwargs):
        nonlocal call_count
        r = results[call_count]
        call_count += 1
        return r

    with (
        patch("app.ingest.pipeline.ingest_file", side_effect=mock_ingest),
        patch("app.ingest.pipeline.ensure_collection"),
    ):
        report = await ingest_directory(
            temp_audio_dir,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    # All 3 files were processed despite the first erroring
    assert report.total_files == 3
    assert report.ingested == 2
    assert report.errors == 1
    assert len(report.results) == 3


@pytest.mark.asyncio
async def test_recursive_directory_scan(
    tmp_path,
    mock_clap_model,
    mock_clap_processor,
    mock_qdrant_client,
    mock_session,
):
    """Files in subdirectories are also discovered."""
    root = tmp_path / "music"
    root.mkdir()
    sub = root / "subdir"
    sub.mkdir()

    (root / "top.mp3").write_bytes(b"\x00" * 100)
    (sub / "nested.flac").write_bytes(b"\x00" * 100)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    class _Factory:
        def __call__(self):
            return mock_session_ctx

    factory = _Factory()

    with (
        patch(
            "app.ingest.pipeline.ingest_file",
            new_callable=AsyncMock,
            return_value=IngestResult(file_path="x", status="success"),
        ) as mock_ingest,
        patch("app.ingest.pipeline.ensure_collection"),
    ):
        report = await ingest_directory(
            root,
            mock_clap_model,
            mock_clap_processor,
            mock_qdrant_client,
            factory,
        )

    assert report.total_files == 2
    assert mock_ingest.call_count == 2


# ---------------------------------------------------------------------------
# Tests for data classes and constants
# ---------------------------------------------------------------------------


def test_audio_extensions_are_lowercase():
    """All audio extensions should be lowercase with leading dot."""
    for ext in AUDIO_EXTENSIONS:
        assert ext.startswith(".")
        assert ext == ext.lower()


def test_duration_limits():
    """Duration constants have sensible values."""
    assert MIN_INGESTION_DURATION == 3.0
    assert MAX_INGESTION_DURATION == 1800.0
    assert MIN_INGESTION_DURATION < MAX_INGESTION_DURATION


def test_ingest_result_defaults():
    """IngestResult has correct default values."""
    result = IngestResult(file_path="/some/file.mp3")
    assert result.status == "pending"
    assert result.track_id is None
    assert result.error is None
    assert result.duration_seconds is None


def test_ingest_report_defaults():
    """IngestReport has correct default values."""
    report = IngestReport()
    assert report.total_files == 0
    assert report.ingested == 0
    assert report.duplicates == 0
    assert report.skipped == 0
    assert report.errors == 0
    assert report.results == []
