"""Build evaluation test corpus from ingested library tracks.

Queries PostgreSQL for all ingested tracks, selects random clips,
extracts them via ffmpeg, and produces a ground_truth.csv for downstream
evaluation scripts.

Usage:
    uv run python scripts/build_eval_corpus.py --audio-dir /path/to/mp3s
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import random
import subprocess  # nosec B404
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.track import Track

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("eval.build_corpus")

try:
    from rich.logging import RichHandler

    _log_handler: logging.Handler = RichHandler(rich_tracebacks=True)
except ImportError:
    _log_handler = logging.StreamHandler()

logging.basicConfig(level=logging.INFO, handlers=[_log_handler])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_NUM_CLIPS = 200
DEFAULT_CLIP_DURATION = 10
DEFAULT_OUTPUT_DIR = "eval_corpus"
DEFAULT_SEED = 42

GROUND_TRUTH_HEADER = [
    "clip_path",
    "true_track_id",
    "true_offset_sec",
    "type",
    "environment",
    "device",
]

NEGATIVE_SOURCES_HELP = """\
Suggested free sources for negative control audio:
  - Free Music Archive  https://freemusicarchive.org/
  - Freesound.org       https://freesound.org/
  - LibriVox            https://librivox.org/ (spoken word)
Place negative audio files in <output-dir>/negative/ and add entries
to ground_truth.csv with type=negative (true_track_id left empty).
"""


# ---------------------------------------------------------------------------
# ffprobe / ffmpeg helpers
# ---------------------------------------------------------------------------


def _get_duration_ffprobe(audio_path: Path) -> float | None:
    """Get audio duration in seconds using ffprobe.

    Returns None if ffprobe fails or is not installed.
    """
    try:
        result = subprocess.run(  # nosec B603 B607
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("ffprobe failed for %s: %s", audio_path, result.stderr.strip())
            return None
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except FileNotFoundError:
        logger.error("ffprobe not found. Install ffmpeg (brew install ffmpeg).")
        sys.exit(1)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Could not parse ffprobe output for %s: %s", audio_path, exc)
        return None


def _extract_clip(
    source: Path,
    output: Path,
    offset_sec: float,
    duration_sec: float,
) -> bool:
    """Extract a clip from source audio using ffmpeg.

    Uses stream copy when possible for speed.
    Returns True on success.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(  # nosec B603 B607
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-ss",
                str(offset_sec),
                "-t",
                str(duration_sec),
                "-acodec",
                "copy",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("ffmpeg failed for %s: %s", source, result.stderr.strip())
            return False
        return True
    except FileNotFoundError:
        logger.error("ffmpeg not found. Install ffmpeg (brew install ffmpeg).")
        sys.exit(1)


def _add_noise_to_clip(
    clean_clip: Path,
    noisy_output: Path,
    snr_db: float,
) -> bool:
    """Mix white noise into a clip at the given SNR using ffmpeg.

    Uses the anoisesrc filter mixed with the original at specified SNR level.
    Returns True on success.
    """
    noisy_output.parent.mkdir(parents=True, exist_ok=True)
    # Calculate volume for noise based on SNR
    # noise_vol = 10^(-snr_db/20) to achieve desired SNR
    import math

    noise_vol = math.pow(10, -snr_db / 20)

    try:
        result = subprocess.run(  # nosec B603 B607
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(clean_clip),
                "-filter_complex",
                (
                    f"anoisesrc=d=30:c=white:a={noise_vol}[noise];"
                    f"[0:a][noise]amix=inputs=2:duration=first"
                ),
                str(noisy_output),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("ffmpeg noise mix failed: %s", result.stderr.strip())
            return False
        return True
    except FileNotFoundError:
        logger.error("ffmpeg not found.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Genre distribution check
# ---------------------------------------------------------------------------


def _check_genre_distribution(tracks: list[Track]) -> None:
    """Warn if library is heavily skewed toward one genre/artist.

    Uses artist as a proxy for genre since we don't have genre metadata.
    """
    artists = [t.artist or "Unknown" for t in tracks]
    counter = Counter(artists)

    if not counter:
        return

    most_common_artist, most_common_count = counter.most_common(1)[0]
    pct = most_common_count / len(tracks) * 100

    if pct > 70:
        logger.warning(
            "GENRE BIAS WARNING: >70%% of tracks are by '%s' (%d/%d = %.0f%%). "
            "Evaluation results may be biased. Consider diversifying the library.",
            most_common_artist,
            most_common_count,
            len(tracks),
            pct,
        )
    elif pct > 40:
        logger.info(
            "Artist distribution note: '%s' accounts for %.0f%% of tracks.",
            most_common_artist,
            pct,
        )


# ---------------------------------------------------------------------------
# Main corpus builder
# ---------------------------------------------------------------------------


async def build_corpus(
    audio_dir: Path,
    output_dir: Path,
    num_clips: int,
    clip_duration: float,
    seed: int,
    add_noise: bool,
    noise_snr_db: float,
) -> None:
    """Build the evaluation corpus.

    1. Query PostgreSQL for all ingested tracks.
    2. Select a random subset.
    3. For each selected track, extract a random clip from the source audio.
    4. Optionally add noisy variants.
    5. Write ground_truth.csv.
    """
    rng = random.Random(seed)  # nosec B311

    # Query all ingested tracks
    async with async_session_factory() as session:
        stmt = select(Track).order_by(Track.ingested_at)
        result = await session.execute(stmt)
        all_tracks = list(result.scalars().all())

    if not all_tracks:
        logger.error("No tracks found in database. Run 'make ingest' first.")
        sys.exit(1)

    logger.info("Found %d tracks in database.", len(all_tracks))
    _check_genre_distribution(all_tracks)

    # Count tracks in database for random baseline context
    total_tracks = len(all_tracks)

    # Select random subset
    selected = rng.sample(all_tracks, min(num_clips, len(all_tracks)))
    logger.info("Selected %d tracks for corpus.", len(selected))

    # Prepare directories
    clean_dir = output_dir / "clean"
    clean_dir.mkdir(parents=True, exist_ok=True)

    if add_noise:
        noisy_dir = output_dir / "noisy"
        noisy_dir.mkdir(parents=True, exist_ok=True)

    gt_rows: list[dict[str, str]] = []
    success_count = 0
    skip_count = 0

    try:
        from rich.progress import Progress

        progress_ctx = Progress()
    except ImportError:
        progress_ctx = None

    if progress_ctx:
        with progress_ctx as progress:
            task = progress.add_task("Extracting clips", total=len(selected))
            for track in selected:
                ok = await _process_track(
                    track=track,
                    audio_dir=audio_dir,
                    clean_dir=clean_dir,
                    clip_duration=clip_duration,
                    rng=rng,
                    gt_rows=gt_rows,
                    add_noise=add_noise,
                    noise_snr_db=noise_snr_db,
                    output_dir=output_dir,
                )
                if ok:
                    success_count += 1
                else:
                    skip_count += 1
                progress.advance(task)
    else:
        for i, track in enumerate(selected):
            if (i + 1) % 20 == 0 or i == 0:
                logger.info("Processing track %d/%d...", i + 1, len(selected))
            ok = await _process_track(
                track=track,
                audio_dir=audio_dir,
                clean_dir=clean_dir,
                clip_duration=clip_duration,
                rng=rng,
                gt_rows=gt_rows,
                add_noise=add_noise,
                noise_snr_db=noise_snr_db,
                output_dir=output_dir,
            )
            if ok:
                success_count += 1
            else:
                skip_count += 1

    # Add template entries for mic/browser/negative (commented guidance)
    gt_rows.append(
        {
            "clip_path": "# --- MIC RECORDINGS (add manually) ---",
            "true_track_id": "",
            "true_offset_sec": "",
            "type": "",
            "environment": "",
            "device": "",
        }
    )
    gt_rows.append(
        {
            "clip_path": "# mic/track_001_quiet.webm",
            "true_track_id": "uuid-here",
            "true_offset_sec": "45.0",
            "type": "mic",
            "environment": "quiet_room",
            "device": "macbook",
        }
    )
    gt_rows.append(
        {
            "clip_path": "# --- BROWSER RECORDINGS (add manually) ---",
            "true_track_id": "",
            "true_offset_sec": "",
            "type": "",
            "environment": "",
            "device": "",
        }
    )
    gt_rows.append(
        {
            "clip_path": "# browser/track_001_chrome.webm",
            "true_track_id": "uuid-here",
            "true_offset_sec": "45.0",
            "type": "browser",
            "environment": "",
            "device": "chrome_desktop",
        }
    )
    gt_rows.append(
        {
            "clip_path": "# --- NEGATIVE CONTROLS (add manually) ---",
            "true_track_id": "",
            "true_offset_sec": "",
            "type": "",
            "environment": "",
            "device": "",
        }
    )
    gt_rows.append(
        {
            "clip_path": "# negative/unknown_001.mp3",
            "true_track_id": "",
            "true_offset_sec": "",
            "type": "negative",
            "environment": "",
            "device": "",
        }
    )

    # Write ground truth CSV
    gt_path = output_dir / "ground_truth.csv"
    with open(gt_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=GROUND_TRUTH_HEADER)
        writer.writeheader()
        writer.writerows(gt_rows)

    # Write corpus metadata
    meta = {
        "total_library_tracks": total_tracks,
        "clips_extracted": success_count,
        "clips_skipped": skip_count,
        "clip_duration_sec": clip_duration,
        "seed": seed,
        "add_noise": add_noise,
        "noise_snr_db": noise_snr_db if add_noise else None,
        "random_baseline_top1": round(1.0 / total_tracks * 100, 4) if total_tracks > 0 else 0,
    }
    meta_path = output_dir / "corpus_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    logger.info("Corpus built: %d clips extracted, %d skipped.", success_count, skip_count)
    logger.info("Ground truth: %s", gt_path)
    logger.info("Metadata: %s", meta_path)
    logger.info(
        "Random baseline (top-1 at %d tracks): %.4f%%",
        total_tracks,
        meta["random_baseline_top1"],
    )

    if skip_count > 0:
        logger.warning(
            "%d tracks were skipped (too short, missing file, or ffmpeg error).", skip_count
        )

    print(f"\n{NEGATIVE_SOURCES_HELP}")


async def _process_track(
    track: Track,
    audio_dir: Path,
    clean_dir: Path,
    clip_duration: float,
    rng: random.Random,
    gt_rows: list[dict[str, str]],
    add_noise: bool,
    noise_snr_db: float,
    output_dir: Path,
) -> bool:
    """Process a single track: find source file, extract clip, add to ground truth.

    Returns True if a clip was successfully extracted.
    """
    # Find source audio file
    source_path = _find_source_file(track, audio_dir)
    if source_path is None:
        logger.warning(
            "Source file not found for track %s (%s). Skipping.",
            track.id,
            track.title,
        )
        return False

    # Get duration
    duration = _get_duration_ffprobe(source_path)
    if duration is None:
        logger.warning("Could not get duration for %s. Skipping.", source_path)
        return False

    if duration < clip_duration:
        logger.warning(
            "Track '%s' (%.1fs) is shorter than clip duration (%.1fs). Skipping.",
            track.title,
            duration,
            clip_duration,
        )
        return False

    # Random offset
    max_offset = max(0, duration - clip_duration)
    offset = rng.uniform(0, max_offset) if max_offset > 0 else 0.0

    # Extract clip
    clip_filename = f"{track.id}_{offset:.0f}{source_path.suffix}"
    clip_path = clean_dir / clip_filename

    if not _extract_clip(source_path, clip_path, offset, clip_duration):
        return False

    # Add to ground truth
    gt_rows.append(
        {
            "clip_path": f"clean/{clip_filename}",
            "true_track_id": str(track.id),
            "true_offset_sec": f"{offset:.1f}",
            "type": "clean",
            "environment": "",
            "device": "",
        }
    )

    # Optionally create noisy variant
    if add_noise:
        noisy_dir = output_dir / "noisy"
        noisy_filename = f"{track.id}_{offset:.0f}_snr{noise_snr_db:.0f}{source_path.suffix}"
        noisy_path = noisy_dir / noisy_filename

        if _add_noise_to_clip(clip_path, noisy_path, noise_snr_db):
            gt_rows.append(
                {
                    "clip_path": f"noisy/{noisy_filename}",
                    "true_track_id": str(track.id),
                    "true_offset_sec": f"{offset:.1f}",
                    "type": "noisy",
                    "environment": f"white_noise_snr{noise_snr_db:.0f}dB",
                    "device": "",
                }
            )

    return True


def _find_source_file(track: Track, audio_dir: Path) -> Path | None:
    """Find the source audio file for a track.

    Tries the audio_dir first, then falls back to track.file_path.
    """
    # Try matching by filename in audio_dir
    file_path = Path(track.file_path)

    # Check if file_path is absolute and exists
    if file_path.is_absolute() and file_path.exists():
        return file_path

    # Try audio_dir / filename
    candidate = audio_dir / file_path.name
    if candidate.exists():
        return candidate

    # Try audio_dir / relative path
    candidate = audio_dir / file_path
    if candidate.exists():
        return candidate

    # Try common patterns
    for ext in [".mp3", ".flac", ".wav", ".ogg", ".m4a"]:
        candidate = audio_dir / f"{file_path.stem}{ext}"
        if candidate.exists():
            return candidate

    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Build evaluation test corpus from ingested library tracks.",
        epilog=NEGATIVE_SOURCES_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--audio-dir",
        type=Path,
        help="Directory containing source audio files (the same dir used for ingestion).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(DEFAULT_OUTPUT_DIR),
        help=f"Output directory for corpus (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--num-clips",
        type=int,
        default=DEFAULT_NUM_CLIPS,
        help=f"Number of clips to extract (default: {DEFAULT_NUM_CLIPS}).",
    )
    parser.add_argument(
        "--clip-duration",
        type=float,
        default=DEFAULT_CLIP_DURATION,
        help=f"Duration of each clip in seconds (default: {DEFAULT_CLIP_DURATION}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Random seed for reproducibility (default: {DEFAULT_SEED}).",
    )
    parser.add_argument(
        "--add-noise",
        action="store_true",
        help="Also create noisy variants by mixing white noise at --noise-snr dB.",
    )
    parser.add_argument(
        "--noise-snr",
        type=float,
        default=20.0,
        help="Signal-to-noise ratio in dB for noisy variants (default: 20.0).",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = parse_args()

    if args.audio_dir is None:
        logger.error("--audio-dir is required. Provide the path to source audio files.")
        sys.exit(1)

    if not args.audio_dir.is_dir():
        logger.error("Audio directory does not exist: %s", args.audio_dir)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    asyncio.run(
        build_corpus(
            audio_dir=args.audio_dir,
            output_dir=args.output_dir,
            num_clips=args.num_clips,
            clip_duration=args.clip_duration,
            seed=args.seed,
            add_noise=args.add_noise,
            noise_snr_db=args.noise_snr,
        )
    )


if __name__ == "__main__":
    main()
