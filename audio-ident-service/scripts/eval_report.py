"""Generate the go/no-go evaluation report from all results.

Reads exact_metrics.json, latency_metrics.json, and optionally
vibe_rating_sheet.csv to produce a comprehensive evaluation-report.md.

Usage:
    uv run python scripts/eval_report.py --corpus-dir eval_corpus
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------


class RatingEntry(TypedDict):
    """A single rating entry for a query result."""

    rank: int
    score: int
    similarity: float


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("eval.report")

try:
    from rich.logging import RichHandler

    _log_handler: logging.Handler = RichHandler(rich_tracebacks=True)
except ImportError:
    _log_handler = logging.StreamHandler()

logging.basicConfig(level=logging.INFO, handlers=[_log_handler])

# ---------------------------------------------------------------------------
# Targets (canonical thresholds)
# ---------------------------------------------------------------------------

EXACT_TARGETS = {
    "top1_clean": 0.98,
    "top1_mic": 0.75,
    "top1_browser": 0.70,
    "top5_mic": 0.85,
    "offset_error_median": 0.5,
    "false_positive_rate": 0.02,
    "query_latency_p95": 2000.0,
}

VIBE_TARGETS = {
    "mrr": 0.5,
    "ndcg_at_5": 0.6,
    "playlist_worthy_rate": 0.60,
}

LATENCY_TARGETS = {
    "p50_ms": 3000.0,
    "p95_ms": 5000.0,
    "p99_ms": 8000.0,
}

# NO-GO thresholds (below these = fundamental failure)
NO_GO = {
    "exact_clean_top1": 0.50,
    "vibe_mrr": 0.30,
    "latency_p95": 15000.0,
}


# ---------------------------------------------------------------------------
# Vibe metrics from human ratings
# ---------------------------------------------------------------------------


def compute_vibe_metrics(rating_path: Path) -> dict[str, Any] | None:
    """Compute vibe metrics from a human-rated CSV.

    Returns None if the file doesn't exist or has no ratings.
    """
    if not rating_path.exists():
        return None

    # Parse ratings
    ratings_by_query: dict[str, list[RatingEntry]] = defaultdict(list)
    rated_count = 0

    with open(rating_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            score_str = row.get("human_score", "").strip()
            if not score_str:
                continue

            try:
                score = int(score_str)
            except ValueError:
                continue

            rated_count += 1
            query_key = row.get("query_path", "") or row.get("query_label", "")
            rank = int(row.get("result_rank", 0))

            ratings_by_query[query_key].append(
                RatingEntry(
                    rank=rank,
                    score=score,
                    similarity=float(row.get("result_similarity", 0)),
                )
            )

    if rated_count == 0:
        return None

    # Compute MRR (Mean Reciprocal Rank)
    # Position of first result scored >= 4
    reciprocal_ranks: list[float] = []
    for _query, results in ratings_by_query.items():
        results_sorted = sorted(results, key=lambda x: x["rank"])
        found = False
        for r in results_sorted:
            if r["score"] >= 4:
                reciprocal_ranks.append(1.0 / r["rank"])
                found = True
                break
        if not found:
            reciprocal_ranks.append(0.0)

    mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0

    # Compute nDCG@5
    ndcg_values: list[float] = []
    for _query, results in ratings_by_query.items():
        results_sorted = sorted(results, key=lambda x: x["rank"])[:5]
        ndcg = _compute_ndcg(results_sorted, k=5)
        ndcg_values.append(ndcg)

    ndcg_at_5 = sum(ndcg_values) / len(ndcg_values) if ndcg_values else 0.0

    # Compute playlist-worthy rate (fraction of top-5 results scored >= 4)
    total_top5 = 0
    worthy_count = 0
    for _query, results in ratings_by_query.items():
        results_sorted = sorted(results, key=lambda x: x["rank"])[:5]
        for r in results_sorted:
            total_top5 += 1
            if r["score"] >= 4:
                worthy_count += 1

    playlist_worthy_rate = worthy_count / total_top5 if total_top5 > 0 else 0.0

    return {
        "mrr": round(mrr, 4),
        "ndcg_at_5": round(ndcg_at_5, 4),
        "playlist_worthy_rate": round(playlist_worthy_rate, 4),
        "num_queries_rated": len(ratings_by_query),
        "num_ratings": rated_count,
    }


def _compute_ndcg(results: list[RatingEntry], k: int = 5) -> float:
    """Compute nDCG@k from a list of ranked results with human scores.

    Assumes results are sorted by rank ascending.
    """
    if not results:
        return 0.0

    # DCG (position-based: positions 1..k)
    dcg = 0.0
    for i, r in enumerate(results[:k]):
        score = r["score"]
        dcg += float(score) / math.log2(i + 2)  # i+2 because log2(1)=0

    # Ideal DCG (sort by score descending, same position-based indexing)
    ideal_scores = sorted([r["score"] for r in results[:k]], reverse=True)
    idcg = 0.0
    for i, score in enumerate(ideal_scores):
        idcg += float(score) / math.log2(i + 2)

    return dcg / idcg if idcg > 0 else 0.0


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(corpus_dir: Path) -> str:
    """Generate the evaluation report as Markdown.

    Returns the report text.
    """
    # Load exact metrics
    exact_metrics: dict[str, Any] | None = None
    exact_path = corpus_dir / "exact_metrics.json"
    if exact_path.exists():
        with open(exact_path) as f:
            exact_metrics = json.load(f)

    # Load latency metrics
    latency_metrics: dict[str, Any] | None = None
    latency_path = corpus_dir / "latency_metrics.json"
    if latency_path.exists():
        with open(latency_path) as f:
            latency_metrics = json.load(f)

    # Compute vibe metrics from ratings
    vibe_metrics = compute_vibe_metrics(corpus_dir / "vibe_rating_sheet.csv")

    # Load corpus metadata
    corpus_meta: dict[str, Any] = {}
    meta_path = corpus_dir / "corpus_metadata.json"
    if meta_path.exists():
        with open(meta_path) as f:
            corpus_meta = json.load(f)

    # Build report
    lines: list[str] = []
    pass_count = 0
    fail_count = 0
    no_go_triggered = False
    improvements_needed: list[str] = []

    # Header
    now = datetime.now(UTC).strftime("%Y-%m-%d")
    total_tracks = corpus_meta.get("total_library_tracks", "?")
    clips_extracted = corpus_meta.get("clips_extracted", "?")

    lines.append("# v1 Evaluation Report\n")
    lines.append(f"Date: {now}")
    lines.append(f"Library size: {total_tracks} tracks")
    lines.append(f"Evaluation corpus: {clips_extracted} clips")
    lines.append("")

    # ------------- Exact ID Results -----------

    lines.append("## Exact ID Results\n")

    if exact_metrics is None:
        lines.append("*No exact evaluation results found. Run `make eval-exact` first.*\n")
    else:
        by_type = exact_metrics.get("by_type", {})

        lines.append("| Metric | Target | Actual | Status |")
        lines.append("|--------|--------|--------|--------|")

        # Clean top-1
        clean = by_type.get("clean", {})
        if clean:
            val = clean.get("top1_accuracy", 0)
            target = EXACT_TARGETS["top1_clean"]
            passed = val >= target
            status = "PASS" if passed else "FAIL"
            if passed:
                pass_count += 1
            else:
                fail_count += 1
                improvements_needed.append(
                    f"Clean clip top-1 accuracy ({val:.1%}) below target ({target:.0%})"
                )
            if val < NO_GO["exact_clean_top1"]:
                no_go_triggered = True
            lines.append(f"| Top-1 accuracy (clean) | >={target:.0%} | {val:.1%} | {status} |")

        # Mic top-1
        mic = by_type.get("mic", {})
        if mic:
            val = mic.get("top1_accuracy", 0)
            target = EXACT_TARGETS["top1_mic"]
            passed = val >= target
            status = "PASS" if passed else "FAIL"
            if passed:
                pass_count += 1
            else:
                fail_count += 1
                improvements_needed.append(
                    f"Mic top-1 accuracy ({val:.1%}) below target ({target:.0%}). Consider denoising or sub-window tuning."
                )
            lines.append(f"| Top-1 accuracy (mic) | >={target:.0%} | {val:.1%} | {status} |")

        # Browser top-1
        browser = by_type.get("browser", {})
        if browser:
            val = browser.get("top1_accuracy", 0)
            target = EXACT_TARGETS["top1_browser"]
            passed = val >= target
            status = "PASS" if passed else "FAIL"
            if passed:
                pass_count += 1
            else:
                fail_count += 1
                improvements_needed.append(
                    f"Browser top-1 accuracy ({val:.1%}) below target ({target:.0%})"
                )
            lines.append(f"| Top-1 accuracy (browser) | >={target:.0%} | {val:.1%} | {status} |")

        # Mic top-5
        if mic:
            val = mic.get("top5_accuracy", 0)
            target = EXACT_TARGETS["top5_mic"]
            passed = val >= target
            status = "PASS" if passed else "FAIL"
            if passed:
                pass_count += 1
            else:
                fail_count += 1
            lines.append(f"| Top-5 accuracy (mic) | >={target:.0%} | {val:.1%} | {status} |")

        # Offset error
        if clean:
            oe = clean.get("offset_error_median")
            if oe is not None:
                target = EXACT_TARGETS["offset_error_median"]
                passed = oe <= target
                status = "PASS" if passed else "FAIL"
                if passed:
                    pass_count += 1
                else:
                    fail_count += 1
                lines.append(f"| Offset error (median) | <{target}s | {oe:.3f}s | {status} |")

        # False positive rate
        negative = by_type.get("negative", {})
        if negative:
            val = negative.get("false_positive_rate", 0)
            target = EXACT_TARGETS["false_positive_rate"]
            passed = val <= target
            status = "PASS" if passed else "FAIL"
            if passed:
                pass_count += 1
            else:
                fail_count += 1
                improvements_needed.append(
                    f"False positive rate ({val:.1%}) above target ({target:.0%}). Adjust MIN_ALIGNED_HASHES."
                )
            lines.append(f"| False positive rate | <={target:.0%} | {val:.1%} | {status} |")

        # Query latency (exact lane only)
        p95 = exact_metrics.get("latency_p95_ms")
        if p95 is not None:
            target = EXACT_TARGETS["query_latency_p95"]
            passed = p95 <= target
            status = "PASS" if passed else "FAIL"
            if passed:
                pass_count += 1
            else:
                fail_count += 1
            lines.append(f"| Query latency (p95) | <={target:.0f}ms | {p95:.0f}ms | {status} |")

        # Random baseline
        rb1 = exact_metrics.get("random_baseline_top1", 0)
        rb5 = exact_metrics.get("random_baseline_top5", 0)
        lines.append(f"\nRandom baseline: top-1 = {rb1:.4f}%, top-5 = {rb5:.4f}%")

        # Environment breakdown
        by_env = exact_metrics.get("by_environment", {})
        if by_env:
            lines.append("\n### Environment Breakdown (mic recordings)\n")
            lines.append("| Environment | Top-1 | Top-5 | Count |")
            lines.append("|-------------|-------|-------|-------|")
            for env, em in by_env.items():
                lines.append(
                    f"| {env} | {em.get('top1_accuracy', 0):.1%} | "
                    f"{em.get('top5_accuracy', 0):.1%} | {em.get('count', 0)} |"
                )

        # Browser breakdown
        by_browser = exact_metrics.get("by_browser", {})
        if by_browser:
            lines.append("\n### Browser Breakdown\n")
            lines.append("| Browser/Device | Top-1 | Top-5 | Count |")
            lines.append("|----------------|-------|-------|-------|")
            for dev, dm in by_browser.items():
                lines.append(
                    f"| {dev} | {dm.get('top1_accuracy', 0):.1%} | "
                    f"{dm.get('top5_accuracy', 0):.1%} | {dm.get('count', 0)} |"
                )

    lines.append("")

    # ------------- Vibe Results -----------

    lines.append("## Vibe Results\n")

    if vibe_metrics is None:
        lines.append(
            "*No vibe ratings found. Run `make eval-vibe`, fill in human_score in "
            "vibe_rating_sheet.csv, then re-run `make eval-report`.*\n"
        )
    else:
        lines.append("| Metric | Target | Actual | Status |")
        lines.append("|--------|--------|--------|--------|")

        mrr = vibe_metrics.get("mrr", 0)
        mrr_target = VIBE_TARGETS["mrr"]
        mrr_passed = mrr >= mrr_target
        mrr_status = "PASS" if mrr_passed else "FAIL"
        if mrr_passed:
            pass_count += 1
        else:
            fail_count += 1
            improvements_needed.append(
                f"Vibe MRR ({mrr:.3f}) below target ({mrr_target}). Try different aggregation parameters."
            )
        if mrr < NO_GO["vibe_mrr"]:
            no_go_triggered = True
        lines.append(f"| MRR | >={mrr_target} | {mrr:.4f} | {mrr_status} |")

        ndcg = vibe_metrics.get("ndcg_at_5", 0)
        ndcg_target = VIBE_TARGETS["ndcg_at_5"]
        ndcg_passed = ndcg >= ndcg_target
        ndcg_status = "PASS" if ndcg_passed else "FAIL"
        if ndcg_passed:
            pass_count += 1
        else:
            fail_count += 1
        lines.append(f"| nDCG@5 | >={ndcg_target} | {ndcg:.4f} | {ndcg_status} |")

        pwr = vibe_metrics.get("playlist_worthy_rate", 0)
        pwr_target = VIBE_TARGETS["playlist_worthy_rate"]
        pwr_passed = pwr >= pwr_target
        pwr_status = "PASS" if pwr_passed else "FAIL"
        if pwr_passed:
            pass_count += 1
        else:
            fail_count += 1
        lines.append(f"| Playlist-worthy rate | >={pwr_target:.0%} | {pwr:.1%} | {pwr_status} |")

        lines.append(
            f"\n*Based on {vibe_metrics.get('num_queries_rated', 0)} rated queries, "
            f"{vibe_metrics.get('num_ratings', 0)} total ratings.*"
        )
        lines.append(
            "\n*Note: Single-rater evaluation accepted for v1. "
            "Multi-rater evaluation with Krippendorff's alpha is a v2 enhancement.*"
        )

    lines.append("")

    # ------------- E2E Latency -----------

    lines.append("## E2E Latency\n")

    if latency_metrics is None:
        lines.append("*No latency results found. Run `make eval-latency` first.*\n")
    else:
        lines.append("| Metric | Target | Actual | Status |")
        lines.append("|--------|--------|--------|--------|")

        p50 = latency_metrics.get("p50_ms", 0)
        p50_target = LATENCY_TARGETS["p50_ms"]
        p50_passed = p50 <= p50_target
        p50_status = "PASS" if p50_passed else "FAIL"
        if p50_passed:
            pass_count += 1
        else:
            fail_count += 1
        lines.append(f"| p50 | <{p50_target:.0f}ms | {p50:.0f}ms | {p50_status} |")

        p95 = latency_metrics.get("p95_ms", 0)
        p95_target = LATENCY_TARGETS["p95_ms"]
        p95_passed = p95 <= p95_target
        p95_status = "PASS" if p95_passed else "FAIL"
        if p95_passed:
            pass_count += 1
        else:
            fail_count += 1
            improvements_needed.append(
                f"E2E p95 latency ({p95:.0f}ms) above target ({p95_target:.0f}ms). Profile and optimize bottleneck."
            )
        if p95 > NO_GO["latency_p95"]:
            no_go_triggered = True
        lines.append(f"| p95 | <{p95_target:.0f}ms | {p95:.0f}ms | {p95_status} |")

        p99 = latency_metrics.get("p99_ms", 0)
        p99_target = LATENCY_TARGETS["p99_ms"]
        p99_passed = p99 <= p99_target
        p99_status = "PASS" if p99_passed else "FAIL"
        if p99_passed:
            pass_count += 1
        else:
            fail_count += 1
        lines.append(f"| p99 | <{p99_target:.0f}ms | {p99:.0f}ms | {p99_status} |")

    lines.append("")

    # ------------- Decision -----------

    lines.append("## Decision\n")

    if no_go_triggered:
        lines.append("- [ ] GO")
        lines.append("- [ ] CONDITIONAL GO")
        lines.append("- [x] **NO-GO** -- fundamental issues require re-architecture")
        lines.append("")
        lines.append("### NO-GO Triggers\n")
        if exact_metrics:
            clean = exact_metrics.get("by_type", {}).get("clean", {})
            if clean and clean.get("top1_accuracy", 0) < NO_GO["exact_clean_top1"]:
                lines.append(
                    f"- Exact ID clean clip accuracy < {NO_GO['exact_clean_top1']:.0%} -- fingerprinting engine is broken"
                )
        if vibe_metrics and vibe_metrics.get("mrr", 0) < NO_GO["vibe_mrr"]:
            lines.append(
                f"- Vibe MRR < {NO_GO['vibe_mrr']} -- embedding model is not capturing music similarity"
            )
        if latency_metrics and latency_metrics.get("p95_ms", 0) > NO_GO["latency_p95"]:
            lines.append(f"- E2E p95 > {NO_GO['latency_p95']:.0f}ms -- architecture does not scale")
    elif fail_count == 0:
        lines.append("- [x] **GO** -- system meets quality bars for wider use")
        lines.append("- [ ] CONDITIONAL GO")
        lines.append("- [ ] NO-GO")
    else:
        lines.append("- [ ] GO")
        lines.append(
            f"- [x] **CONDITIONAL GO** -- meets most bars; "
            f"{fail_count} metric(s) need improvement (see below)"
        )
        lines.append("- [ ] NO-GO")

    lines.append(f"\n**Score: {pass_count} PASS / {fail_count} FAIL**\n")

    # ------------- Improvements -----------

    if improvements_needed:
        lines.append("## Recommended Improvements\n")
        lines.append(
            "*CONDITIONAL GO requires a follow-up sprint of max 5 days "
            "addressing these improvements:*\n"
        )
        for i, imp in enumerate(improvements_needed, 1):
            lines.append(f"{i}. {imp}")
        lines.append("")

    # ------------- Parameter Tuning -----------

    lines.append("## Parameter Tuning Opportunities\n")
    lines.append("1. Adjust `MIN_ALIGNED_HASHES` threshold based on ROC curve (exact lane)")
    lines.append("2. Adjust `VIBE_MATCH_THRESHOLD` based on human eval scores (vibe lane)")
    lines.append("3. Enable denoising for noisy environments if accuracy < target")
    lines.append("4. Tune Qdrant `hnsw_ef` parameter for latency vs. recall tradeoff")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate the go/no-go evaluation report from all results.",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("eval_corpus"),
        help="Directory containing evaluation results (default: eval_corpus).",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = parse_args()

    if not args.corpus_dir.is_dir():
        logger.error("Corpus directory does not exist: %s", args.corpus_dir)
        sys.exit(1)

    report = generate_report(args.corpus_dir)

    # Write report
    report_path = args.corpus_dir / "evaluation-report.md"
    report_path.write_text(report)

    # Print summary to stdout
    print(report)

    logger.info("Full report written to: %s", report_path)


if __name__ == "__main__":
    main()
