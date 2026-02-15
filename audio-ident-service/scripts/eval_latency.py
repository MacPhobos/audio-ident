"""End-to-end latency benchmark via HTTP.

Sends audio files to the search endpoint via multipart/form-data and
measures wall-clock latency. Tests the full stack including network,
ffmpeg decode, Olaf query, CLAP inference, and Qdrant search.

Usage:
    uv run python scripts/eval_latency.py --corpus-dir eval_corpus
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import statistics
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("eval.latency")

try:
    from rich.logging import RichHandler

    _log_handler: logging.Handler = RichHandler(rich_tracebacks=True)
except ImportError:
    _log_handler = logging.StreamHandler()

logging.basicConfig(level=logging.INFO, handlers=[_log_handler])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:17010"
DEFAULT_NUM_QUERIES = 100
DEFAULT_MODE = "both"
WARMUP_COUNT = 3
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".webm", ".opus"}

# Latency targets (from plan)
TARGETS = {
    "p50_ms": 3000.0,
    "p95_ms": 5000.0,
    "p99_ms": 8000.0,
}


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


async def send_search_request(
    client: object,  # httpx.AsyncClient
    base_url: str,
    audio_path: Path,
    mode: str,
) -> dict[str, object]:
    """Send a single search request and measure latency.

    Returns a dict with latency_ms, status_code, and any error.
    """
    import httpx

    assert isinstance(client, httpx.AsyncClient)  # nosec B101

    try:
        audio_data = audio_path.read_bytes()
    except OSError as exc:
        return {"error": str(exc), "latency_ms": 0.0, "status_code": 0}

    # Determine content type from extension
    ext = audio_path.suffix.lower()
    content_type_map = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
        ".opus": "audio/opus",
    }
    content_type = content_type_map.get(ext, "audio/mpeg")

    t0 = time.perf_counter()
    try:
        response = await client.post(
            f"{base_url}/api/v1/search",
            files={"audio": (audio_path.name, audio_data, content_type)},
            data={"mode": mode, "max_results": "10"},
            timeout=30.0,
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        return {
            "latency_ms": latency_ms,
            "status_code": response.status_code,
            "error": None if response.status_code == 200 else f"HTTP {response.status_code}",
        }
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000
        return {
            "latency_ms": latency_ms,
            "status_code": 0,
            "error": str(exc),
        }


async def run_benchmark(
    corpus_dir: Path,
    base_url: str,
    mode: str,
    num_queries: int,
) -> None:
    """Run the E2E latency benchmark."""
    try:
        import httpx
    except ImportError:
        logger.error("httpx is required for latency benchmarks. Install with: uv add httpx")
        sys.exit(1)

    # Collect audio files for benchmarking
    audio_files = _collect_audio_files(corpus_dir, num_queries)

    if not audio_files:
        logger.error("No audio files found in corpus directory.")
        sys.exit(1)

    logger.info("Benchmarking %d queries against %s (mode=%s)", len(audio_files), base_url, mode)

    # Check connectivity first
    async with httpx.AsyncClient() as client:
        try:
            health = await client.get(f"{base_url}/health", timeout=5.0)
            if health.status_code != 200:
                logger.error("Backend health check failed: HTTP %d", health.status_code)
                sys.exit(1)
        except Exception as exc:
            logger.error(
                "Cannot connect to backend at %s: %s\n"
                "Make sure the service is running (make dev).",
                base_url,
                exc,
            )
            sys.exit(1)

        logger.info("Backend health check passed.")

        # Warmup
        if WARMUP_COUNT > 0 and audio_files:
            logger.info("Running %d warmup queries (excluded from measurements)...", WARMUP_COUNT)
            warmup_files = audio_files[:WARMUP_COUNT]
            for wf in warmup_files:
                await send_search_request(client, base_url, wf, mode)
            logger.info("Warmup complete.")

        # Benchmark
        results: list[dict[str, Any]] = []
        errors: list[str] = []

        try:
            from rich.progress import Progress

            progress_ctx = Progress()
        except ImportError:
            progress_ctx = None

        if progress_ctx:
            with progress_ctx as progress:
                task = progress.add_task("Benchmarking", total=len(audio_files))
                for audio_file in audio_files:
                    r = await send_search_request(client, base_url, audio_file, mode)
                    r["audio_path"] = str(audio_file)
                    results.append(r)
                    if r.get("error"):
                        errors.append(f"{audio_file.name}: {r['error']}")
                    progress.advance(task)
        else:
            for i, audio_file in enumerate(audio_files):
                if (i + 1) % 20 == 0 or i == 0:
                    logger.info("Query %d/%d...", i + 1, len(audio_files))
                r = await send_search_request(client, base_url, audio_file, mode)
                r["audio_path"] = str(audio_file)
                results.append(r)
                if r.get("error"):
                    errors.append(f"{audio_file.name}: {r['error']}")

    # Compute metrics (only successful requests)
    successful = [r for r in results if r.get("error") is None]
    latencies = [float(r["latency_ms"]) for r in successful]

    if not latencies:
        logger.error("No successful requests. All %d queries failed.", len(results))
        for e in errors[:10]:
            logger.error("  %s", e)
        sys.exit(1)

    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)

    metrics: dict[str, Any] = {
        "total_queries": len(results),
        "successful_queries": len(successful),
        "failed_queries": len(errors),
        "mode": mode,
        "base_url": base_url,
        "p50_ms": round(latencies_sorted[n // 2], 1),
        "p95_ms": round(latencies_sorted[int(n * 0.95)], 1)
        if n > 1
        else round(latencies_sorted[0], 1),
        "p99_ms": round(latencies_sorted[min(int(n * 0.99), n - 1)], 1),
        "mean_ms": round(statistics.mean(latencies), 1),
        "min_ms": round(min(latencies), 1),
        "max_ms": round(max(latencies), 1),
        "stdev_ms": round(statistics.stdev(latencies), 1) if n > 1 else 0.0,
        "targets": TARGETS,
    }

    # Write results
    results_path = corpus_dir / "latency_results.csv"
    _write_results_csv(results, results_path)

    metrics_path = corpus_dir / "latency_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    # Print summary
    _print_summary(metrics, errors)

    logger.info("Results: %s", results_path)
    logger.info("Metrics: %s", metrics_path)


def _collect_audio_files(corpus_dir: Path, max_count: int) -> list[Path]:
    """Collect audio files from the corpus for benchmarking."""
    audio_files: list[Path] = []

    # Check for clean clips first
    clean_dir = corpus_dir / "clean"
    if clean_dir.is_dir():
        for f in sorted(clean_dir.iterdir()):
            if f.suffix.lower() in AUDIO_EXTENSIONS:
                audio_files.append(f)

    # Also check noisy dir if present
    noisy_dir = corpus_dir / "noisy"
    if noisy_dir.is_dir():
        for f in sorted(noisy_dir.iterdir()):
            if f.suffix.lower() in AUDIO_EXTENSIONS:
                audio_files.append(f)

    # Also check mic and browser dirs
    for sub in ["mic", "browser", "negative"]:
        sub_dir = corpus_dir / sub
        if sub_dir.is_dir():
            for f in sorted(sub_dir.iterdir()):
                if f.suffix.lower() in AUDIO_EXTENSIONS:
                    audio_files.append(f)

    # Limit to requested count
    if len(audio_files) > max_count:
        audio_files = audio_files[:max_count]

    return audio_files


def _write_results_csv(results: list[dict[str, Any]], path: Path) -> None:
    """Write per-query latency results."""
    fieldnames = ["audio_path", "latency_ms", "status_code", "error"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "audio_path": r.get("audio_path", ""),
                    "latency_ms": f"{float(r.get('latency_ms', 0)):.1f}",
                    "status_code": r.get("status_code", 0),
                    "error": r.get("error", ""),
                }
            )


def _print_summary(metrics: dict[str, Any], errors: list[str]) -> None:
    """Print summary to stdout."""
    print("\n" + "=" * 70)
    print("E2E LATENCY BENCHMARK")
    print("=" * 70)

    print(f"\nMode: {metrics.get('mode', 'unknown')}")
    print(
        f"Queries: {metrics.get('successful_queries', 0)} successful / {metrics.get('total_queries', 0)} total"
    )

    if int(metrics.get("failed_queries", 0)) > 0:
        print(f"Failed: {metrics['failed_queries']}")

    p50 = float(metrics.get("p50_ms", 0))
    p95 = float(metrics.get("p95_ms", 0))
    p99 = float(metrics.get("p99_ms", 0))

    p50_status = "PASS" if p50 <= TARGETS["p50_ms"] else "FAIL"
    p95_status = "PASS" if p95 <= TARGETS["p95_ms"] else "FAIL"
    p99_status = "PASS" if p99 <= TARGETS["p99_ms"] else "FAIL"

    print("\nLatency:")
    print(f"  p50:  {p50:.0f}ms  (target: <={TARGETS['p50_ms']:.0f}ms)  [{p50_status}]")
    print(f"  p95:  {p95:.0f}ms  (target: <={TARGETS['p95_ms']:.0f}ms)  [{p95_status}]")
    print(f"  p99:  {p99:.0f}ms  (target: <={TARGETS['p99_ms']:.0f}ms)  [{p99_status}]")
    print(f"  mean: {metrics.get('mean_ms', 0):.0f}ms")
    print(f"  min:  {metrics.get('min_ms', 0):.0f}ms")
    print(f"  max:  {metrics.get('max_ms', 0):.0f}ms")
    if metrics.get("stdev_ms"):
        print(f"  stdev: {metrics['stdev_ms']:.0f}ms")

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors[:5]:
            print(f"  - {e}")
        if len(errors) > 5:
            print(f"  ... and {len(errors) - 5} more.")

    print("\n" + "=" * 70)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run E2E latency benchmark via HTTP.",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("eval_corpus"),
        help="Directory containing audio clips (default: eval_corpus).",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the running service (default: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default=DEFAULT_MODE,
        choices=["exact", "vibe", "both"],
        help=f"Search mode (default: {DEFAULT_MODE}).",
    )
    parser.add_argument(
        "--num-queries",
        type=int,
        default=DEFAULT_NUM_QUERIES,
        help=f"Maximum number of queries to run (default: {DEFAULT_NUM_QUERIES}).",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = parse_args()

    args.corpus_dir.mkdir(parents=True, exist_ok=True)

    asyncio.run(
        run_benchmark(
            corpus_dir=args.corpus_dir,
            base_url=args.base_url,
            mode=args.mode,
            num_queries=args.num_queries,
        )
    )


if __name__ == "__main__":
    main()
