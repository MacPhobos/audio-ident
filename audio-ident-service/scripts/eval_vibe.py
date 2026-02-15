"""Run vibe search evaluation against the test corpus.

Reads ground_truth.csv (or a separate query dir), runs each query through
the vibe (CLAP + Qdrant) search lane, outputs results CSV and a human
rating sheet for manual vibe evaluation.

Usage:
    uv run python scripts/eval_vibe.py --corpus-dir eval_corpus
    uv run python scripts/eval_vibe.py --query-dir /path/to/vibe_queries
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import statistics
import sys
import time
from pathlib import Path
from typing import Any, cast

from qdrant_client import AsyncQdrantClient

from app.audio.decode import decode_to_pcm
from app.audio.embedding import load_clap_model
from app.db.session import async_session_factory
from app.search.vibe import run_vibe_lane
from app.settings import settings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("eval.vibe")

try:
    from rich.logging import RichHandler

    _log_handler: logging.Handler = RichHandler(rich_tracebacks=True)
except ImportError:
    _log_handler = logging.StreamHandler()

logging.basicConfig(level=logging.INFO, handlers=[_log_handler])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_RESULTS = 10
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".webm", ".opus"}

# Vibe evaluation targets (from plan)
TARGETS = {
    "mrr": 0.5,
    "ndcg_at_5": 0.6,
    "playlist_worthy_rate": 0.60,  # fraction of top-5 results scored >= 4
}


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


async def evaluate_vibe_query(
    audio_path: Path,
    max_results: int,
    *,
    qdrant_client: AsyncQdrantClient,
    clap_model: object,
    clap_processor: object,
) -> dict[str, object]:
    """Run a single vibe query and return results.

    Returns a dict with matches, latency, or error info.
    """
    try:
        audio_data = audio_path.read_bytes()
    except OSError as exc:
        logger.error("Could not read %s: %s", audio_path, exc)
        return {"error": str(exc), "matches": [], "latency_ms": 0.0}

    try:
        pcm_48k = await decode_to_pcm(audio_data, target_sample_rate=48000)
    except Exception as exc:
        logger.error("Decode failed for %s: %s", audio_path, exc)
        return {"error": f"decode: {exc}", "matches": [], "latency_ms": 0.0}

    t0 = time.perf_counter()
    try:
        async with async_session_factory() as session:
            matches = await run_vibe_lane(
                pcm_48k,
                max_results,
                qdrant_client=qdrant_client,
                clap_model=clap_model,
                clap_processor=clap_processor,
                session=session,
            )
    except Exception as exc:
        logger.error("Vibe lane failed for %s: %s", audio_path, exc)
        return {
            "error": f"vibe_lane: {exc}",
            "matches": [],
            "latency_ms": (time.perf_counter() - t0) * 1000,
        }

    latency_ms = (time.perf_counter() - t0) * 1000

    return {
        "matches": matches,
        "latency_ms": latency_ms,
        "error": None,
    }


async def run_evaluation(
    corpus_dir: Path,
    query_dir: Path | None,
    max_results: int,
    query_type: str | None,
) -> None:
    """Run the full vibe evaluation."""
    # Load CLAP model once
    logger.info("Loading CLAP model (this may take a moment)...")
    clap_model, clap_processor = load_clap_model()
    logger.info("CLAP model loaded.")

    # Initialize Qdrant client
    qdrant_client = AsyncQdrantClient(url=settings.qdrant_url)

    # Build query list
    queries = _build_query_list(corpus_dir, query_dir, query_type)

    if not queries:
        logger.error("No queries found. Check corpus-dir or query-dir.")
        sys.exit(1)

    logger.info("Running %d vibe queries (max_results=%d)...", len(queries), max_results)

    # Run queries
    all_results: list[dict[str, str]] = []
    rating_rows: list[dict[str, str]] = []
    errors: list[str] = []

    try:
        from rich.progress import Progress

        progress_ctx = Progress()
    except ImportError:
        progress_ctx = None

    async def process_query(query: dict[str, str], idx: int) -> None:
        audio_path = Path(query["audio_path"])
        query_label = query.get("label", audio_path.stem)

        eval_result = await evaluate_vibe_query(
            audio_path,
            max_results,
            qdrant_client=qdrant_client,
            clap_model=clap_model,
            clap_processor=clap_processor,
        )

        if eval_result.get("error"):
            errors.append(f"{audio_path}: {eval_result['error']}")

        matches: list[Any] = cast(list[Any], eval_result["matches"])

        # Store per-query result
        for rank, match in enumerate(matches):
            all_results.append(
                {
                    "query_path": str(audio_path),
                    "query_label": query_label,
                    "query_type": query.get("type", ""),
                    "result_rank": str(rank + 1),
                    "result_track_id": str(match.track.id),
                    "result_title": match.track.title,
                    "result_artist": match.track.artist or "",
                    "result_similarity": f"{match.similarity:.4f}",
                    "latency_ms": f"{eval_result['latency_ms']:.1f}",
                }
            )

            # Add to rating sheet
            rating_rows.append(
                {
                    "query_path": str(audio_path),
                    "query_label": query_label,
                    "result_rank": str(rank + 1),
                    "result_track_id": str(match.track.id),
                    "result_title": match.track.title,
                    "result_artist": match.track.artist or "",
                    "result_similarity": f"{match.similarity:.4f}",
                    "human_score": "",  # Empty for rater to fill
                }
            )

        if not matches:
            all_results.append(
                {
                    "query_path": str(audio_path),
                    "query_label": query_label,
                    "query_type": query.get("type", ""),
                    "result_rank": "0",
                    "result_track_id": "",
                    "result_title": "(no results)",
                    "result_artist": "",
                    "result_similarity": "0.0",
                    "latency_ms": f"{eval_result['latency_ms']:.1f}",
                }
            )

    if progress_ctx:
        with progress_ctx as progress:
            task = progress.add_task("Vibe evaluation", total=len(queries))
            for i, query in enumerate(queries):
                await process_query(query, i)
                progress.advance(task)
    else:
        for i, query in enumerate(queries):
            if (i + 1) % 10 == 0 or i == 0:
                logger.info("Processing query %d/%d...", i + 1, len(queries))
            await process_query(query, i)

    # Write results CSV
    results_path = corpus_dir / "vibe_results.csv"
    _write_csv(
        all_results,
        results_path,
        fieldnames=[
            "query_path",
            "query_label",
            "query_type",
            "result_rank",
            "result_track_id",
            "result_title",
            "result_artist",
            "result_similarity",
            "latency_ms",
        ],
    )

    # Write rating sheet
    rating_path = corpus_dir / "vibe_rating_sheet.csv"
    _write_csv(
        rating_rows,
        rating_path,
        fieldnames=[
            "query_path",
            "query_label",
            "result_rank",
            "result_track_id",
            "result_title",
            "result_artist",
            "result_similarity",
            "human_score",
        ],
    )

    # Print summary
    _print_summary(all_results, errors, queries)

    await qdrant_client.close()

    logger.info("Results: %s", results_path)
    logger.info("Rating sheet: %s", rating_path)
    logger.info(
        "To complete vibe evaluation, fill in 'human_score' (1-5) in %s",
        rating_path,
    )


def _build_query_list(
    corpus_dir: Path,
    query_dir: Path | None,
    query_type: str | None,
) -> list[dict[str, str]]:
    """Build the list of queries to evaluate.

    Sources queries from either a dedicated query dir or ground_truth.csv.
    """
    queries: list[dict[str, str]] = []

    if query_dir is not None and query_dir.is_dir():
        # Use audio files in query_dir
        for audio_file in sorted(query_dir.iterdir()):
            if audio_file.suffix.lower() in AUDIO_EXTENSIONS:
                queries.append(
                    {
                        "audio_path": str(audio_file),
                        "label": audio_file.stem,
                        "type": "query",
                    }
                )
        logger.info("Found %d audio files in query dir: %s", len(queries), query_dir)
        return queries

    # Fall back to ground_truth.csv
    gt_path = corpus_dir / "ground_truth.csv"
    if not gt_path.exists():
        logger.error("No ground_truth.csv found and no --query-dir specified.")
        return []

    with open(gt_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["clip_path"].startswith("#"):
                continue
            clip_type = row.get("type", "")

            # Filter by type if specified
            if query_type and clip_type != query_type:
                continue

            audio_path = corpus_dir / row["clip_path"]
            if audio_path.exists():
                queries.append(
                    {
                        "audio_path": str(audio_path),
                        "label": Path(row["clip_path"]).stem,
                        "type": clip_type,
                    }
                )

    logger.info("Found %d queries from ground_truth.csv.", len(queries))
    return queries


def _write_csv(
    rows: list[dict[str, str]],
    path: Path,
    fieldnames: list[str],
) -> None:
    """Write rows to CSV."""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _print_summary(
    results: list[dict[str, Any]],
    errors: list[str],
    queries: list[dict[str, str]],
) -> None:
    """Print a human-readable summary."""
    print("\n" + "=" * 70)
    print("VIBE EVALUATION SUMMARY")
    print("=" * 70)

    # Count queries with/without results
    queries_with_results: set[str] = set()
    queries_without_results: set[str] = set()
    all_similarities: list[float] = []

    for r in results:
        rank = int(r.get("result_rank", 0))
        qpath = str(r.get("query_path", ""))
        if rank == 0:
            queries_without_results.add(qpath)
        else:
            queries_with_results.add(qpath)
            sim = float(r.get("result_similarity", 0))
            all_similarities.append(sim)

    total_queries = len(queries)
    with_results = len(queries_with_results)
    without_results = total_queries - with_results

    print(f"\nTotal queries: {total_queries}")
    print(f"  With results: {with_results}")
    print(f"  Without results: {without_results}")

    if all_similarities:
        print("\nSimilarity scores (across all results):")
        print(f"  Mean: {statistics.mean(all_similarities):.4f}")
        print(f"  Median: {statistics.median(all_similarities):.4f}")
        print(f"  Min: {min(all_similarities):.4f}")
        print(f"  Max: {max(all_similarities):.4f}")

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors[:10]:
            print(f"  - {e}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more.")

    print("\nNext step: Fill in human_score (1-5) in vibe_rating_sheet.csv")
    print("  5 = Perfect vibe match ('add to same playlist without hesitation')")
    print("  4 = Strong vibe match ('similar mood/energy, same playlist')")
    print("  3 = Moderate match ('some shared qualities, noticeably different')")
    print("  2 = Weak match ('I see why, but it's a stretch')")
    print("  1 = No match ('completely different vibe')")

    print("\n" + "=" * 70)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run vibe search evaluation.",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("eval_corpus"),
        help="Directory containing ground_truth.csv and output location (default: eval_corpus).",
    )
    parser.add_argument(
        "--query-dir",
        type=Path,
        default=None,
        help="Optional directory with audio files to use as vibe queries (overrides ground_truth.csv).",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=DEFAULT_MAX_RESULTS,
        help=f"Max results per query (default: {DEFAULT_MAX_RESULTS}).",
    )
    parser.add_argument(
        "--query-type",
        type=str,
        default=None,
        help="Filter ground_truth.csv by type (e.g., 'clean', 'mic'). Only used without --query-dir.",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = parse_args()

    args.corpus_dir.mkdir(parents=True, exist_ok=True)

    asyncio.run(
        run_evaluation(
            corpus_dir=args.corpus_dir,
            query_dir=args.query_dir,
            max_results=args.max_results,
            query_type=args.query_type,
        )
    )


if __name__ == "__main__":
    main()
