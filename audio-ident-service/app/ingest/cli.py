"""CLI entry point for batch audio ingestion.

Usage: uv run python -m app.ingest /path/to/audio/
"""

import asyncio
import logging
import sys
from pathlib import Path

from app.audio.embedding import load_clap_model
from app.audio.qdrant_setup import get_qdrant_client
from app.db.session import async_session_factory
from app.ingest.pipeline import ingest_directory


def main() -> None:
    """Main entry point for batch ingestion CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        print(  # noqa: T201
            "Usage: uv run python -m app.ingest <audio_directory>",
            file=sys.stderr,
        )
        sys.exit(1)

    audio_dir = Path(sys.argv[1])
    if not audio_dir.is_dir():
        print(f"Error: '{audio_dir}' is not a directory", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    asyncio.run(_run_ingestion(audio_dir))


async def _run_ingestion(audio_dir: Path) -> None:
    """Run the ingestion pipeline."""
    log = logging.getLogger(__name__)

    # Load CLAP model (one-time, ~5-15s first run)
    log.info("Loading CLAP model...")
    model, processor = load_clap_model()
    log.info("CLAP model loaded.")

    # Connect to Qdrant
    qdrant = get_qdrant_client()

    # Run ingestion
    report = await ingest_directory(audio_dir, model, processor, qdrant, async_session_factory)

    # Print summary
    print(f"\n{'=' * 60}")  # noqa: T201
    print("Ingestion Report")  # noqa: T201
    print(f"{'=' * 60}")  # noqa: T201
    print(f"Total files:  {report.total_files}")  # noqa: T201
    print(f"Ingested:     {report.ingested}")  # noqa: T201
    print(f"Duplicates:   {report.duplicates}")  # noqa: T201
    print(f"Skipped:      {report.skipped}")  # noqa: T201
    print(f"Errors:       {report.errors}")  # noqa: T201

    if report.errors > 0:
        print("\nFailed files:")  # noqa: T201
        for r in report.results:
            if r.status == "error":
                print(f"  - {r.file_path}: {r.error}")  # noqa: T201

    print(f"{'=' * 60}")  # noqa: T201


if __name__ == "__main__":
    main()
