"""Run exact ID evaluation against the test corpus.

Reads ground_truth.csv, runs each clip through the exact (Olaf) search lane,
measures accuracy and latency, and outputs per-query results and aggregate
metrics.

Usage:
    uv run python scripts/eval_exact.py --corpus-dir eval_corpus
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

from app.audio.decode import decode_to_pcm
from app.search.exact import run_exact_lane

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("eval.exact")

try:
    from rich.logging import RichHandler

    _log_handler: logging.Handler = RichHandler(rich_tracebacks=True)
except ImportError:
    _log_handler = logging.StreamHandler()

logging.basicConfig(level=logging.INFO, handlers=[_log_handler])

# ---------------------------------------------------------------------------
# Target thresholds (from phase plan)
# ---------------------------------------------------------------------------

TARGETS = {
    "top1_clean": 0.98,
    "top1_mic": 0.75,
    "top1_browser": 0.70,
    "top5_mic": 0.85,
    "offset_error_median": 0.5,  # seconds
    "false_positive_rate": 0.02,
    "query_latency_p95": 2000.0,  # milliseconds
}


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


async def evaluate_clip(
    clip_path: Path,
    max_results: int,
) -> dict[str, Any]:
    """Evaluate a single clip through the exact lane.

    Returns a dict with evaluation fields, or None fields on error.
    """
    try:
        audio_data = clip_path.read_bytes()
    except OSError as exc:
        logger.error("Could not read %s: %s", clip_path, exc)
        return {
            "error": str(exc),
            "latency_ms": 0.0,
            "num_matches": 0,
        }

    try:
        pcm_16k = await decode_to_pcm(audio_data, target_sample_rate=16000)
    except Exception as exc:
        logger.error("Decode failed for %s: %s", clip_path, exc)
        return {
            "error": f"decode: {exc}",
            "latency_ms": 0.0,
            "num_matches": 0,
        }

    t0 = time.perf_counter()
    try:
        matches = await run_exact_lane(pcm_16k, max_results=max_results)
    except Exception as exc:
        logger.error("Exact lane failed for %s: %s", clip_path, exc)
        return {
            "error": f"exact_lane: {exc}",
            "latency_ms": (time.perf_counter() - t0) * 1000,
            "num_matches": 0,
        }

    latency_ms = (time.perf_counter() - t0) * 1000

    return {
        "matches": matches,
        "latency_ms": latency_ms,
        "num_matches": len(matches),
        "error": None,
    }


async def run_evaluation(
    corpus_dir: Path,
    max_results: int,
) -> None:
    """Run the full exact ID evaluation."""
    gt_path = corpus_dir / "ground_truth.csv"
    if not gt_path.exists():
        logger.error("Ground truth file not found: %s", gt_path)
        sys.exit(1)

    # Read corpus metadata for random baseline
    meta_path = corpus_dir / "corpus_metadata.json"
    total_tracks = 0
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
            total_tracks = meta.get("total_library_tracks", 0)

    # Parse ground truth
    rows: list[dict[str, str]] = []
    with open(gt_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip comment rows (template entries)
            if row["clip_path"].startswith("#"):
                continue
            rows.append(row)

    if not rows:
        logger.error("No valid entries in ground_truth.csv.")
        sys.exit(1)

    logger.info("Evaluating %d clips...", len(rows))

    # Run evaluation
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        from rich.progress import Progress

        progress_ctx = Progress()
    except ImportError:
        progress_ctx = None

    async def process_row(row: dict[str, str], idx: int) -> dict[str, Any]:
        clip_path = corpus_dir / row["clip_path"]
        true_track_id = row.get("true_track_id", "")
        true_offset_str = row.get("true_offset_sec", "")
        true_offset = float(true_offset_str) if true_offset_str else None
        clip_type = row.get("type", "clean")
        environment = row.get("environment", "")
        device = row.get("device", "")

        eval_result = await evaluate_clip(clip_path, max_results)

        if eval_result.get("error"):
            errors.append(f"{clip_path}: {eval_result['error']}")
            return {
                "clip": row["clip_path"],
                "type": clip_type,
                "environment": environment,
                "device": device,
                "top1_correct": False,
                "top5_correct": False,
                "offset_error": None,
                "false_positive": False,
                "latency_ms": eval_result["latency_ms"],
                "num_matches": 0,
                "top1_confidence": 0.0,
                "top1_aligned_hashes": 0,
                "error": eval_result["error"],
            }

        matches = eval_result["matches"]

        # Evaluate correctness
        top1_correct = len(matches) > 0 and str(matches[0].track.id) == true_track_id
        top5_correct = any(str(m.track.id) == true_track_id for m in matches)

        offset_error = None
        if top1_correct and matches[0].offset_seconds is not None and true_offset is not None:
            offset_error = abs(matches[0].offset_seconds - true_offset)

        false_positive = clip_type == "negative" and len(matches) > 0

        return {
            "clip": row["clip_path"],
            "type": clip_type,
            "environment": environment,
            "device": device,
            "top1_correct": top1_correct,
            "top5_correct": top5_correct,
            "offset_error": offset_error,
            "false_positive": false_positive,
            "latency_ms": eval_result["latency_ms"],
            "num_matches": eval_result["num_matches"],
            "top1_confidence": matches[0].confidence if matches else 0.0,
            "top1_aligned_hashes": matches[0].aligned_hashes if matches else 0,
            "error": None,
        }

    if progress_ctx:
        with progress_ctx as progress:
            task = progress.add_task("Evaluating", total=len(rows))
            for i, row in enumerate(rows):
                r = await process_row(row, i)
                results.append(r)
                progress.advance(task)
    else:
        for i, row in enumerate(rows):
            if (i + 1) % 20 == 0 or i == 0:
                logger.info("Evaluating clip %d/%d...", i + 1, len(rows))
            r = await process_row(row, i)
            results.append(r)

    # Write per-query results CSV
    results_path = corpus_dir / "exact_results.csv"
    _write_results_csv(results, results_path)

    # Compute and write aggregate metrics
    metrics = _compute_metrics(results, total_tracks)
    metrics_path = corpus_dir / "exact_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    # Print summary
    _print_summary(metrics, errors)

    logger.info("Results: %s", results_path)
    logger.info("Metrics: %s", metrics_path)


def _write_results_csv(results: list[dict[str, Any]], path: Path) -> None:
    """Write per-query evaluation results to CSV."""
    if not results:
        return

    fieldnames = [
        "clip",
        "type",
        "environment",
        "device",
        "top1_correct",
        "top5_correct",
        "offset_error",
        "false_positive",
        "latency_ms",
        "num_matches",
        "top1_confidence",
        "top1_aligned_hashes",
        "error",
    ]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(r)


def _compute_metrics(
    results: list[dict[str, Any]],
    total_tracks: int,
) -> dict[str, Any]:
    """Compute aggregate metrics segmented by type and environment."""
    metrics: dict[str, Any] = {}

    # Overall
    all_latencies = [float(r["latency_ms"]) for r in results if r["error"] is None]

    if all_latencies:
        all_latencies_sorted = sorted(all_latencies)
        n = len(all_latencies_sorted)
        metrics["latency_p50_ms"] = all_latencies_sorted[n // 2]
        metrics["latency_p95_ms"] = all_latencies_sorted[int(n * 0.95)]
        metrics["latency_p99_ms"] = all_latencies_sorted[min(int(n * 0.99), n - 1)]
        metrics["latency_mean_ms"] = statistics.mean(all_latencies)
    else:
        metrics["latency_p50_ms"] = None
        metrics["latency_p95_ms"] = None
        metrics["latency_p99_ms"] = None
        metrics["latency_mean_ms"] = None

    # Random baseline
    if total_tracks > 0:
        metrics["random_baseline_top1"] = round(1.0 / total_tracks * 100, 4)
        metrics["random_baseline_top5"] = round(5.0 / total_tracks * 100, 4)
    else:
        metrics["random_baseline_top1"] = 0.0
        metrics["random_baseline_top5"] = 0.0

    metrics["total_tracks_in_library"] = total_tracks

    # Segment by type
    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        if r["error"] is not None:
            continue
        t = str(r["type"])
        by_type.setdefault(t, []).append(r)

    type_metrics: dict[str, dict[str, Any]] = {}
    for t, type_results in by_type.items():
        tm: dict[str, Any] = {}
        n_total = len(type_results)
        tm["count"] = n_total

        if t == "negative":
            fp_count = sum(1 for r in type_results if r["false_positive"])
            tm["false_positive_count"] = fp_count
            tm["false_positive_rate"] = round(fp_count / n_total, 4) if n_total > 0 else 0.0
        else:
            top1_correct = sum(1 for r in type_results if r["top1_correct"])
            top5_correct = sum(1 for r in type_results if r["top5_correct"])
            tm["top1_accuracy"] = round(top1_correct / n_total, 4) if n_total > 0 else 0.0
            tm["top5_accuracy"] = round(top5_correct / n_total, 4) if n_total > 0 else 0.0

            # Offset errors
            offsets = [
                float(r["offset_error"]) for r in type_results if r["offset_error"] is not None
            ]
            if offsets:
                tm["offset_error_median"] = round(statistics.median(offsets), 3)
                tm["offset_error_mean"] = round(statistics.mean(offsets), 3)
            else:
                tm["offset_error_median"] = None
                tm["offset_error_mean"] = None

        # Latency for this type
        lats = [float(r["latency_ms"]) for r in type_results]
        if lats:
            lats_sorted = sorted(lats)
            n_l = len(lats_sorted)
            tm["latency_p95_ms"] = lats_sorted[int(n_l * 0.95)] if n_l > 1 else lats_sorted[0]
        else:
            tm["latency_p95_ms"] = None

        type_metrics[t] = tm

    metrics["by_type"] = type_metrics

    # Segment by environment (for mic recordings)
    mic_results = by_type.get("mic", [])
    if mic_results:
        by_env: dict[str, list[dict[str, Any]]] = {}
        for r in mic_results:
            env = str(r["environment"]) or "unknown"
            by_env.setdefault(env, []).append(r)

        env_metrics: dict[str, dict[str, Any]] = {}
        for env, env_results in by_env.items():
            n_total = len(env_results)
            top1_correct = sum(1 for r in env_results if r["top1_correct"])
            top5_correct = sum(1 for r in env_results if r["top5_correct"])
            env_metrics[env] = {
                "count": n_total,
                "top1_accuracy": round(top1_correct / n_total, 4) if n_total > 0 else 0.0,
                "top5_accuracy": round(top5_correct / n_total, 4) if n_total > 0 else 0.0,
            }
        metrics["by_environment"] = env_metrics

    # Segment by device/browser (for browser recordings)
    browser_results = by_type.get("browser", [])
    if browser_results:
        by_device: dict[str, list[dict[str, Any]]] = {}
        for r in browser_results:
            dev = str(r["device"]) or "unknown"
            by_device.setdefault(dev, []).append(r)

        device_metrics: dict[str, dict[str, Any]] = {}
        for dev, dev_results in by_device.items():
            n_total = len(dev_results)
            top1_correct = sum(1 for r in dev_results if r["top1_correct"])
            top5_correct = sum(1 for r in dev_results if r["top5_correct"])
            device_metrics[dev] = {
                "count": n_total,
                "top1_accuracy": round(top1_correct / n_total, 4) if n_total > 0 else 0.0,
                "top5_accuracy": round(top5_correct / n_total, 4) if n_total > 0 else 0.0,
            }
        metrics["by_browser"] = device_metrics

    # Targets for comparison
    metrics["targets"] = TARGETS

    return metrics


def _print_summary(metrics: dict[str, Any], errors: list[str]) -> None:
    """Print a human-readable summary table to stdout."""
    print("\n" + "=" * 70)
    print("EXACT ID EVALUATION SUMMARY")
    print("=" * 70)

    by_type = metrics.get("by_type", {})

    # Clean clips
    clean = by_type.get("clean", {})
    if clean:
        acc = clean.get("top1_accuracy", 0)
        target = TARGETS["top1_clean"]
        status = "PASS" if acc >= target else "FAIL"
        print(f"\nClean clips (n={clean.get('count', 0)}):")
        print(f"  Top-1 accuracy: {acc:.1%}  (target: >={target:.0%})  [{status}]")
        print(f"  Top-5 accuracy: {clean.get('top5_accuracy', 0):.1%}")
        if clean.get("offset_error_median") is not None:
            oe = clean["offset_error_median"]
            oe_target = TARGETS["offset_error_median"]
            oe_status = "PASS" if oe <= oe_target else "FAIL"
            print(f"  Offset error (median): {oe:.3f}s  (target: <{oe_target}s)  [{oe_status}]")

    # Mic recordings
    mic = by_type.get("mic", {})
    if mic:
        acc = mic.get("top1_accuracy", 0)
        target = TARGETS["top1_mic"]
        status = "PASS" if acc >= target else "FAIL"
        print(f"\nMic recordings (n={mic.get('count', 0)}):")
        print(f"  Top-1 accuracy: {acc:.1%}  (target: >={target:.0%})  [{status}]")
        top5 = mic.get("top5_accuracy", 0)
        t5_target = TARGETS["top5_mic"]
        t5_status = "PASS" if top5 >= t5_target else "FAIL"
        print(f"  Top-5 accuracy: {top5:.1%}  (target: >={t5_target:.0%})  [{t5_status}]")

    # Environment breakdown
    by_env = metrics.get("by_environment", {})
    if by_env:
        print("\n  By environment:")
        for env, em in by_env.items():
            print(
                f"    {env}: top-1={em['top1_accuracy']:.1%}, top-5={em['top5_accuracy']:.1%} (n={em['count']})"
            )

    # Browser recordings
    browser = by_type.get("browser", {})
    if browser:
        acc = browser.get("top1_accuracy", 0)
        target = TARGETS["top1_browser"]
        status = "PASS" if acc >= target else "FAIL"
        print(f"\nBrowser recordings (n={browser.get('count', 0)}):")
        print(f"  Top-1 accuracy: {acc:.1%}  (target: >={target:.0%})  [{status}]")

    # Browser breakdown
    by_browser = metrics.get("by_browser", {})
    if by_browser:
        print("\n  By browser:")
        for dev, dm in by_browser.items():
            print(
                f"    {dev}: top-1={dm['top1_accuracy']:.1%}, top-5={dm['top5_accuracy']:.1%} (n={dm['count']})"
            )

    # Noisy clips
    noisy = by_type.get("noisy", {})
    if noisy:
        print(f"\nNoisy clips (n={noisy.get('count', 0)}):")
        print(f"  Top-1 accuracy: {noisy.get('top1_accuracy', 0):.1%}")
        print(f"  Top-5 accuracy: {noisy.get('top5_accuracy', 0):.1%}")

    # Negative controls
    negative = by_type.get("negative", {})
    if negative:
        fpr = negative.get("false_positive_rate", 0)
        target = TARGETS["false_positive_rate"]
        status = "PASS" if fpr <= target else "FAIL"
        print(f"\nNegative controls (n={negative.get('count', 0)}):")
        print(f"  False positive rate: {fpr:.1%}  (target: <={target:.0%})  [{status}]")

    # Latency
    p95 = metrics.get("latency_p95_ms")
    if p95 is not None:
        target = TARGETS["query_latency_p95"]
        status = "PASS" if p95 <= target else "FAIL"
        print("\nLatency:")
        print(f"  p50: {metrics.get('latency_p50_ms', 0):.0f}ms")
        print(f"  p95: {p95:.0f}ms  (target: <={target:.0f}ms)  [{status}]")
        print(f"  p99: {metrics.get('latency_p99_ms', 0):.0f}ms")
        print(f"  mean: {metrics.get('latency_mean_ms', 0):.0f}ms")

    # Random baseline
    rb1 = metrics.get("random_baseline_top1", 0)
    rb5 = metrics.get("random_baseline_top5", 0)
    total = metrics.get("total_tracks_in_library", 0)
    if total:
        print(f"\nRandom baseline ({total} tracks): top-1={rb1:.4f}%, top-5={rb5:.4f}%")

    # Errors
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors[:10]:
            print(f"  - {e}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more.")

    print("\n" + "=" * 70)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run exact ID evaluation against the test corpus.",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("eval_corpus"),
        help="Directory containing ground_truth.csv and clip files (default: eval_corpus).",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=5,
        help="Max results per query (default: 5).",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = parse_args()

    if not args.corpus_dir.is_dir():
        logger.error("Corpus directory does not exist: %s", args.corpus_dir)
        logger.error("Run 'make eval-corpus' first to build the test corpus.")
        sys.exit(1)

    asyncio.run(run_evaluation(args.corpus_dir, args.max_results))


if __name__ == "__main__":
    main()
