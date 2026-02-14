# Phase 7: Evaluation (~3-4 days)

> **Depends on**: Phase 5 (orchestration) for backend evaluation, Phase 6 (frontend) for browser tests
> **Blocks**: Nothing — this is the final phase
> **Goal**: Measure system quality against defined thresholds; produce go/no-go assessment

---

## Overview

This phase creates a test corpus, runs all queries through both search lanes, measures accuracy and latency, and produces a final assessment of whether the system meets quality bars for wider use.

**Reference**: 02-fingerprinting-survey.md §2.4, 03-embeddings-and-qdrant.md §3.6, 09-reality-check.md

---

## Step 1: Build Test Corpus (~1 day)

### 1.1 Clean Clips (200)

Extract random 10-second clips from library tracks with known ground truth:

```python
# scripts/build_eval_corpus.py
import random
import subprocess
from pathlib import Path

# Select 200 random tracks from library
tracks = list(Path("/path/to/mp3s").glob("*.mp3"))
selected = random.sample(tracks, min(200, len(tracks)))

for track in selected:
    # Random offset (leaving 10s before end)
    duration = get_duration(track)  # via ffprobe
    max_offset = max(0, duration - 10)
    offset = random.uniform(0, max_offset)

    # Extract 10s clip
    output = f"eval_corpus/clean/{track.stem}_{offset:.0f}.mp3"
    subprocess.run([
        "ffmpeg", "-i", str(track), "-ss", str(offset),
        "-t", "10", "-acodec", "copy", output
    ])

    # Record ground truth
    # CSV: clip_path, true_track_id, true_offset_sec, type
```

**Ground truth file**: `eval_corpus/ground_truth.csv`

```csv
clip_path,true_track_id,true_offset_sec,type,environment,device
clean/track_001_45.mp3,uuid-xxx,45.0,clean,,
mic/track_001_quiet.webm,uuid-xxx,45.0,mic,quiet_room,macbook
browser/track_001_chrome.webm,uuid-xxx,45.0,browser,,chrome_desktop
negative/unknown_001.mp3,,,negative,,
```

### 1.2 Mic Recordings (200)

Record 5-second clips using phone/laptop mic in varied environments:

| Environment | Count | Method |
|-------------|-------|--------|
| Quiet room, 1m from speaker | 50 | Play track through speaker, record with phone |
| Office with HVAC + keyboard | 50 | Same, with ambient office noise |
| Cafe/restaurant background | 50 | Same, in a real or simulated cafe environment |
| Outdoor / street noise | 50 | Same, outdoors |

**Recording format**: WAV or WebM (use the browser UI from Phase 6 for WebM recordings, or a phone's voice recorder for WAV).

Label each with: `(recording_id, true_track_id, true_offset_sec, environment, device)`

### 1.3 Browser-Captured Recordings (50)

Record via the actual SvelteKit UI (Phase 6) using different browsers:

| Browser | Count | Notes |
|---------|-------|-------|
| Chrome (desktop) | 15 | Primary target |
| Firefox (desktop) | 10 | |
| Safari (desktop) | 10 | MP4/AAC fallback |
| Chrome (Android) | 10 | Mobile mic quality |
| Safari (iOS) | 5 | iOS mic processing |

### 1.4 Negative Controls (50)

Recordings of tracks NOT in the library:
- 25 from a different music collection
- 15 from random internet audio (podcasts, sound effects)
- 10 of silence / ambient noise only

### Acceptance Criteria
- [ ] 200 clean clips with ground truth labels
- [ ] 200 mic recordings across 4 environments with labels
- [ ] 50 browser recordings across 5 browser/platform combos
- [ ] 50 negative controls
- [ ] All labels in `eval_corpus/ground_truth.csv`

---

## Step 2: Exact ID Evaluation (~1 day)

### 2.1 Run All Queries

```python
# scripts/eval_exact.py
import asyncio
import csv
import time

async def evaluate_exact_lane():
    results = []

    with open("eval_corpus/ground_truth.csv") as f:
        reader = csv.DictReader(f)
        for row in reader:
            clip_path = row["clip_path"]
            true_track_id = row.get("true_track_id")
            true_offset = float(row["true_offset_sec"]) if row.get("true_offset_sec") else None
            clip_type = row["type"]

            # Decode to 16kHz PCM
            pcm_16k = await decode_to_pcm(clip_path, target_sample_rate=16000)

            # Run exact lane
            t0 = time.perf_counter()
            matches = await run_exact_lane(pcm_16k, max_results=5)
            latency_ms = (time.perf_counter() - t0) * 1000

            # Evaluate
            top1_correct = (
                len(matches) > 0
                and str(matches[0].track.id) == true_track_id
            )
            top5_correct = any(
                str(m.track.id) == true_track_id for m in matches
            )
            offset_error = (
                abs(matches[0].offset_seconds - true_offset)
                if top1_correct and matches[0].offset_seconds is not None and true_offset is not None
                else None
            )
            false_positive = (
                clip_type == "negative" and len(matches) > 0
            )

            results.append({
                "clip": clip_path,
                "type": clip_type,
                "environment": row.get("environment", ""),
                "top1_correct": top1_correct,
                "top5_correct": top5_correct,
                "offset_error": offset_error,
                "false_positive": false_positive,
                "latency_ms": latency_ms,
                "num_matches": len(matches),
                "top1_confidence": matches[0].confidence if matches else 0,
                "top1_aligned_hashes": matches[0].aligned_hashes if matches else 0,
            })

    return results
```

### 2.2 Compute Metrics

| Metric | Target | Segmentation |
|--------|--------|-------------|
| Top-1 accuracy (clean clips) | >=98% | By input type |
| Top-1 accuracy (mic recordings) | >=75% | By environment |
| Top-1 accuracy (browser WebM) | >=70% | By browser |
| Top-5 accuracy (mic recordings) | >=85% | By environment |
| Offset error (median) | <0.5s | Overall |
| False positive rate | <2% | Negative controls only |
| Query latency (p95) | <2s | Overall |

### 2.3 Segment Results

Break down by:
- **Input type**: clean / mic / browser / negative
- **Environment** (mic only): quiet / office / cafe / outdoor
- **Browser** (browser only): Chrome / Firefox / Safari / Android / iOS

### 2.4 Compare Against Go/No-Go Criteria

From Phase 1 and 09-reality-check.md:

| Environment | Expected Exact ID | Actual |
|-------------|------------------|--------|
| Quiet room | 85-95% | ? |
| Office | 65-80% | ? |
| Cafe | 40-60% | ? |
| Outdoor | 20-40% | ? |

### Acceptance Criteria
- [ ] All 500 queries processed
- [ ] Metrics computed and segmented
- [ ] Results compared against targets from 02-fingerprinting-survey.md §2.4

---

## Step 3: Vibe Evaluation (~1-2 days)

**Reference**: 03-embeddings-and-qdrant.md §3.6

### 3.1 Select Query Set

50 diverse query tracks spanning the library's genres:

| Category | Count | Purpose |
|----------|-------|---------|
| Genre-diverse | 20 | One per major genre |
| Similar-artist | 15 | Known similar artists (e.g., Miles Davis → Coltrane) |
| Cross-genre | 10 | "Jazz-influenced rock" → should find both |
| Edge cases | 5 | Spoken word, ambient, very short |

### 3.2 Run Vibe Queries

For each query, get top-10 vibe results:

```python
# scripts/eval_vibe.py
for query in query_set:
    pcm_48k = await decode_to_pcm(query.path, target_sample_rate=48000)
    results = await run_vibe_lane(pcm_48k, max_results=10)
    # Store for human evaluation
```

### 3.3 Human Evaluation

**Rubric** (from 03-embeddings-and-qdrant.md §3.6):

| Score | Label | Description |
|-------|-------|-------------|
| 5 | Perfect vibe match | "Add to same playlist without hesitation" |
| 4 | Strong vibe match | "Similar mood/energy, close genre, same playlist" |
| 3 | Moderate match | "Some shared qualities, noticeably different" |
| 2 | Weak match | "I see why the algorithm matched, but it's a stretch" |
| 1 | No match | "Completely different vibe" |

**Process**:
1. 3 raters evaluate each query's top-5 results (50 queries × 5 results = 250 ratings per rater)
2. Raters listen to query + result, assign 1-5 score
3. Each rating session takes ~1-2 hours per rater

**Inter-rater reliability**: Compute Krippendorff's alpha. If alpha < 0.6, the rubric is too ambiguous — refine and re-rate.

### 3.4 Compute Metrics

| Metric | Target | How to Compute |
|--------|--------|---------------|
| Mean Reciprocal Rank (MRR) | >=0.5 | Position of first result scored >=4 |
| nDCG@5 | >=0.6 | Normalized DCG using human scores |
| "Playlist-worthy" rate | >=60% | Fraction of top-5 results scored >=4 |

### 3.5 Objective Proxies (Automated)

These don't replace human eval but catch obvious failures:

| Proxy | How | What it Catches |
|-------|-----|-----------------|
| Genre overlap | Compare predicted genre of query vs results | Classical matched with metal |
| Tempo similarity | Compare BPM (librosa/Essentia) | Slow ballad matched with fast dance |
| Key compatibility | Compare estimated key (Essentia) | Suspicious if all same key |
| Energy correlation | Compare RMS energy | Quiet acoustic matched with loud electronic |

### Acceptance Criteria
- [ ] 50 queries × top-10 results generated
- [ ] 3 raters completed evaluation
- [ ] Krippendorff's alpha >= 0.6
- [ ] MRR, nDCG@5, playlist-worthy rate computed
- [ ] Objective proxies identify no catastrophic failures

---

## Step 4: End-to-End Latency (~0.5 days)

### 4.1 Measure Full Pipeline

```python
# scripts/eval_latency.py
import httpx
import time

async def measure_e2e_latency():
    latencies = []

    for clip_path in eval_clips[:100]:  # 100 representative clips
        with open(clip_path, "rb") as f:
            content = f.read()

        t0 = time.perf_counter()
        response = await httpx.AsyncClient().post(
            "http://localhost:17010/api/v1/search",
            files={"audio": ("test.webm", content, "audio/webm")},
            data={"mode": "both", "max_results": "10"},
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        latencies.append(latency_ms)

    latencies.sort()
    print(f"E2E latency:")
    print(f"  p50: {latencies[49]:.0f}ms")
    print(f"  p95: {latencies[94]:.0f}ms")
    print(f"  p99: {latencies[98]:.0f}ms")
```

### 4.2 Breakdown by Component

If p95 exceeds 5s, identify the bottleneck:

| Component | How to Measure | Optimization if Slow |
|-----------|---------------|---------------------|
| Upload receive | Log timestamp at endpoint entry | N/A (network-bound) |
| ffmpeg decode | Time `decode_dual_rate()` | Use single ffmpeg process with multiple outputs |
| Olaf query | Time `run_exact_lane()` | Already fast (<300ms) |
| CLAP inference | Time embedding generation | GPU, ONNX export, or switch to PANNs |
| Qdrant query | Time `query_points()` | Tune ef, use cache |
| DB lookup | Time PostgreSQL queries | Add indexes, use connection pool |

### 4.3 Target

| Metric | Target | Action if Missed |
|--------|--------|-----------------|
| E2E p50 | <3s | Acceptable |
| E2E p95 | <5s | Critical threshold |
| E2E p99 | <8s | Investigate outliers |

### Acceptance Criteria
- [ ] 100 E2E latency measurements collected
- [ ] p50, p95, p99 computed
- [ ] Bottleneck identified if p95 > 5s
- [ ] Component-level breakdown documented

---

## Step 5: Go/No-Go Assessment

### Assessment Template

Create `eval_corpus/evaluation-report.md`:

```markdown
# v1 Evaluation Report

Date: YYYY-MM-DD
Library size: X tracks
Evaluation corpus: 500 queries (200 clean + 200 mic + 50 browser + 50 negative)

## Exact ID Results

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Top-1 accuracy (clean) | >=98% | X% | PASS/FAIL |
| Top-1 accuracy (mic) | >=75% | X% | PASS/FAIL |
| Top-1 accuracy (browser) | >=70% | X% | PASS/FAIL |
| Top-5 accuracy (mic) | >=85% | X% | PASS/FAIL |
| Offset error (median) | <0.5s | Xs | PASS/FAIL |
| False positive rate | <2% | X% | PASS/FAIL |
| Query latency (p95) | <2s | Xms | PASS/FAIL |

## Vibe Results

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| MRR | >=0.5 | X | PASS/FAIL |
| nDCG@5 | >=0.6 | X | PASS/FAIL |
| Playlist-worthy rate | >=60% | X% | PASS/FAIL |

## E2E Latency

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| p50 | <3s | Xms | PASS/FAIL |
| p95 | <5s | Xms | PASS/FAIL |

## Decision

[ ] GO — system meets quality bars for wider use
[ ] CONDITIONAL GO — meets most bars; specific improvements needed (list below)
[ ] NO-GO — fundamental issues require re-architecture

## Recommended Improvements (if CONDITIONAL GO)

1. ...
2. ...

## Parameter Tuning Opportunities

1. Adjust MIN_ALIGNED_HASHES threshold based on ROC curve
2. Adjust VIBE_MATCH_THRESHOLD based on human eval scores
3. Enable denoising for noisy environments if accuracy < target
```

### Decision Criteria

**GO**: All metrics meet or exceed targets.

**CONDITIONAL GO**: Most metrics meet targets, with specific actionable improvements:
- Exact ID accuracy below target in one environment → add denoising or sub-window tuning
- Vibe quality below target → try different aggregation parameters
- Latency above target → profile and optimize the bottleneck

**NO-GO**: Fundamental issues that require rethinking the approach:
- Exact ID < 50% even on clean clips → fingerprinting engine is broken
- Vibe MRR < 0.3 → embedding model isn't capturing music similarity
- E2E p95 > 15s → architecture doesn't scale

---

## Tooling

### Make Targets

```makefile
eval-corpus: ## Build evaluation test corpus
	cd $(SERVICE_DIR) && uv run python scripts/build_eval_corpus.py

eval-exact: ## Run exact ID evaluation
	cd $(SERVICE_DIR) && uv run python scripts/eval_exact.py

eval-vibe: ## Run vibe evaluation (generates files for human rating)
	cd $(SERVICE_DIR) && uv run python scripts/eval_vibe.py

eval-latency: ## Run E2E latency benchmark
	cd $(SERVICE_DIR) && uv run python scripts/eval_latency.py

eval-report: ## Generate evaluation report from results
	cd $(SERVICE_DIR) && uv run python scripts/eval_report.py
```

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Test corpus too small for statistical significance | Medium | Medium | 500 queries provides reasonable power; increase for marginal results |
| Human raters disagree strongly (low alpha) | Medium | Medium | Refine rubric; add training examples; use 2 raters if 3 unavailable |
| Results are environment-dependent (inconsistent) | High | Low | This is expected — report per-environment breakdown |
| Evaluation takes longer than 3-4 days | Medium | Low | Parallelize: corpus building overlaps with Phase 5; latency tests are fast |

---

## Effort Breakdown

| Task | Hours |
|------|-------|
| Build test corpus (clean clips + scripts) | 4h |
| Record mic + browser test clips | 8h |
| Run exact ID evaluation + compute metrics | 4h |
| Vibe evaluation setup + run queries | 4h |
| Human rating (3 raters × 1-2h each) | 6h |
| E2E latency benchmark | 2h |
| Analysis + report writing | 4h |
| **Total** | **~32h (4 days)** |

---

## Devil's Advocate Review

> **Reviewer**: plan-reviewer
> **Date**: 2026-02-14
> **Reviewed against**: All research files in `docs/research/01-initial-research/`

### Confidence Assessment

**Overall: MEDIUM-LOW** — The evaluation methodology is scientifically sound and well-structured. However, the **logistics** of creating the test corpus are severely underestimated. This phase requires extensive manual, physical-world work that cannot be parallelized with coding.

### Gaps Identified

1. **200 mic recordings is an enormous manual effort.** Each recording requires: (a) select a track, (b) play it through speakers, (c) start recording on phone/laptop, (d) wait 5-10 seconds, (e) stop recording, (f) transfer file to computer, (g) label with ground truth. At ~2 minutes per recording (optimistic), that's ~400 minutes = **6.7 hours of continuous manual recording**. The plan estimates 8h for "Record mic + browser test clips" (all 250 recordings), which is extremely tight. Realistically, budget 12-16h with setup, breaks, and re-records.

2. **"4 environments" for mic recordings is logistically challenging.** The plan requires recordings in: quiet room (50), office with HVAC (50), cafe/restaurant (50), outdoor/street (50). Finding and accessing 4 distinct environments, transporting equipment, and spending ~1.5 hours recording in each location is a multi-day activity, not a few-hour task. Consider: Can cafe/outdoor recordings be simulated by mixing clean audio with noise profiles? This would be faster and more reproducible.

3. **3 human raters for vibe evaluation may not be available.** The plan assumes 3 raters × 1-2 hours each for vibe evaluation. Where do these raters come from? If this is a solo project, the developer is the only rater. Single-rater evaluation can't compute inter-rater reliability (Krippendorff's alpha). Either accept single-rater evaluation or plan how to recruit raters.

4. **"Clean clips" test is too easy.** 200 clean clips extracted from the same MP3 files that were ingested are essentially testing "does the system recognize its own input." For a fingerprinting system, clean-clip accuracy should be >99% — if it's not, something is fundamentally broken. The real test is mic recordings. Consider reducing clean clips to 50 and increasing mic recordings.

5. **No baseline comparison.** The evaluation measures absolute metrics but doesn't compare against a baseline. For exact ID: what accuracy would a random guesser achieve? (At 20K tracks, random is ~0.005%.) For vibe: what nDCG@5 would random ranking achieve? Including baselines makes the evaluation more meaningful and helps calibrate whether targets are ambitious or trivial.

6. **The go/no-go criteria don't define what to do for CONDITIONAL GO.** The assessment template lists "Recommended Improvements" but doesn't specify a timeline or commitment. A CONDITIONAL GO without a concrete improvement plan tends to become a de facto GO with known bugs. Define: "CONDITIONAL GO requires a follow-up sprint of max 5 days addressing the listed improvements."

### Edge Cases Not Addressed

1. **Evaluation corpus contains the developer's own music library.** If the library has genre imbalances (e.g., 60% jazz, 10% classical), the evaluation results will be biased toward the dominant genre. Report results per genre, or at least note the genre distribution of the test corpus.

2. **Ground truth offset accuracy.** For mic recordings, the "true offset" is when the speaker started playing, which is hard to measure precisely. There's a startup delay between pressing "play" on the source and the audio reaching the recording microphone. Offset accuracy of <0.5s may be within this measurement error. Consider whether offset evaluation is meaningful for mic recordings.

3. **Browser recordings may differ between sessions.** The plan records 50 browser clips, but MediaRecorder behavior can vary between sessions (different audio contexts, different system audio settings). If the browser recordings are done on a different day than the initial testing, system state differences could affect results.

### Feasibility Concerns

1. **32h (4 days) is severely underestimated.** The effort breakdown shows 8h for "Record mic + browser test clips" but as analyzed above, this alone could take 12-16h. Adding time for environment logistics, file transfer, labeling, and re-records, the recording phase alone is 2-3 days. Total realistic estimate: 6-8 days (48-64h).

2. **"Run exact ID evaluation + compute metrics" (4h) assumes the system works.** If bugs are discovered during evaluation (e.g., offset calculation is wrong, confidence thresholds need tuning), debugging and re-running could double this time.

3. **Human vibe evaluation requires preparation.** Creating a usable evaluation UI (or even a spreadsheet with links to audio files), training raters on the rubric, and managing the evaluation process takes time not included in the estimate.

### Missing Dependencies

1. **Test corpus storage.** 200 clean clips + 200 mic recordings + 50 browser recordings + 50 negative controls = 500 files. At ~500KB each, that's ~250MB. Plus the ground truth CSV. Where is this stored? Is it committed to the repo? (Probably not — too large.) Add to `.gitignore` and document the storage location.

2. **Evaluation scripts depend on a running backend.** `eval_exact.py` and `eval_vibe.py` call `run_exact_lane()` and `run_vibe_lane()` directly (not via HTTP). This means the scripts must run in the same Python environment as the service, with access to LMDB, Qdrant, and CLAP model. Alternatively, `eval_latency.py` uses HTTP. Clarify which approach is used and ensure the test environment is properly configured.

3. **Negative controls from "a different music collection" (25 tracks) and "random internet audio" (15 tracks).** These need to be sourced. If the developer doesn't have a second music collection, this is a blocker. Suggest specific free sources: Free Music Archive, Freesound.org, or LibriVox (spoken word).

### Recommended Changes

1. **Reduce mic recordings to 100** (25 per environment) and **reduce clean clips to 50**. This cuts manual effort in half while still providing statistically meaningful results (100 mic recordings gives ~±10% confidence intervals at 95% CI).
2. **Accept single-rater vibe evaluation** for v1 and note the limitation. Multi-rater evaluation is a v2 enhancement.
3. **Add noise simulation as an alternative** to physical-environment recording: mix clean clips with noise profiles (RNNoise dataset, ESC-50 environmental sounds) at various SNRs. This is reproducible and faster.
4. **Add baseline comparisons** (random guesser) to the evaluation report template.
5. **Increase effort estimate** to 48-64h (6-8 days) or reduce the corpus size per recommendation #1.
6. **Specify free audio sources** for negative controls.
7. **Define CONDITIONAL GO follow-up commitment**: max 5 additional days with specific improvement targets.

---

## Cross-Plan Review Notes

> These observations span multiple plan files and address consistency/integration issues.

### Research Reference Validity

All plan files correctly reference the research documents. Key references verified:
- Phase 1 correctly cites 02-fingerprinting-survey.md for Olaf evaluation criteria
- Phase 3 correctly cites 00-reconciliation-summary.md for dual sample-rate pipeline
- Phase 4 correctly cites 03-embeddings-and-qdrant.md for chunk aggregation algorithm
- Phase 5 correctly cites 04-architecture-and-api.md for asyncio.gather pattern
- Phase 7 correctly cites 09-reality-check.md for expected accuracy ranges

### Cross-Plan Contradictions Found

1. **~~RESOLVED~~ PCM format for Olaf: s16le vs f32le.** Fixed: Phase 3 Step 1.1 now correctly specifies f32le for Olaf. All ffmpeg commands, pseudocode, and research files have been updated to use f32le for Olaf and CLAP, with s16le only for Chromaprint.

2. **Error status code inconsistency.** Phase 5 Step 1.2 uses HTTP 400 for `UNSUPPORTED_FORMAT`, but Step 4 error mapping says 422. Phase 2 contract says 400. All must align.

3. **Effort totals don't match overview.** Sum of individual phase efforts: 32h + 28h + 40h + 32h + 28h + 36h + 32h = 228h = ~28.5 days. The overview says "26-38d." The low end (26d) is unreachable even at the optimistic sub-estimates. More accurate range: **29-38d** (or 28.5 at absolute minimum).

### Version Pinning Consistency

- Qdrant: Consistently pinned to v1.16.3 across all plans. OK.
- PostgreSQL: Consistently pinned to 16. OK.
- CLAP: `>=1.1` in overview, not pinned in Phase 3 (`uv add laion-clap` with no version). Pin it.
- ffmpeg: `>=5.0` in overview, not verified at runtime. Add startup check.
- `qdrant-client`: Not version-pinned anywhere. Pin it.

### File Path Consistency

All file paths are consistent across plans:
- `app/audio/decode.py`, `app/audio/metadata.py`, etc. in Phase 3
- `app/search/exact.py`, `app/search/vibe.py` in Phase 4
- `app/routers/search.py`, `app/search/orchestrator.py` in Phase 5
- No conflicting paths detected.

### Missing Cross-Phase Integration

1. **No integration test that spans Phase 3 → Phase 4 → Phase 5.** Each phase has its own tests, but there's no end-to-end test that ingests a track and then searches for it via the API. Phase 5's integration tests use `httpx.AsyncClient` but may use mocked data. Add a true E2E test: ingest 5 tracks, then search for one.

2. **CLAP model loading appears in Phase 3 (ingestion) and Phase 5 (search).** During ingestion, CLAP is needed for embedding generation. During search, CLAP is needed for query embedding. Are these the same model instance? The Phase 5 lifespan handler loads CLAP into `app.state.clap_model`, but Phase 3's `generate_chunked_embeddings()` loads its own instance via `load_clap_model()`. This could mean **two copies of CLAP in memory (~1.2-2GB total)** if ingestion and search run simultaneously. Unify the model instance.
