# Plan Overview — Audio-Ident v1 Implementation

> **Date**: 2026-02-14
> **Authority**: Based on reconciled research (see `docs/research/01-initial-research/00-reconciliation-summary.md`)
> **Status**: Implementation Plan

---

## Executive Summary

Audio-ident v1 delivers a dual-mode audio search system: **exact fingerprint identification** (Olaf) and **vibe similarity search** (HuggingFace Transformers CLAP + Qdrant). Users upload or record audio via browser; the backend runs both search lanes in parallel and returns combined results.

The implementation is structured in **7 phases** (validation, infrastructure, ingestion, search lanes, orchestration, frontend, evaluation) totaling **26-38 developer-days**. A validation phase gates the entire project: if any critical technology choice fails, we pivot before investing in production code.

---

## Phase Dependency Diagram

```
Phase 1: Validation Prototypes (4d)
    │
    ▼
Phase 2: Infrastructure (3-4d)
    │
    ▼
Phase 3: Ingestion Pipeline (4-5d)
    │
    ├──────────────────────────┐
    ▼                          ▼
Phase 4a: Exact ID Lane    Phase 4b: Vibe Lane
    (2-3d)                    (2-3d)
    │                          │
    └──────────┬───────────────┘
               ▼
Phase 5: Orchestration (2-3d)
               │
               ▼
Phase 6: Frontend (4-5d)
               │
               ▼
Phase 7: Evaluation (3-4d)
```

**Parallelism opportunities:**
- Phase 4a and Phase 4b can run in parallel (both depend on Phase 3, independent of each other)
- Phase 6 UI wireframing/component scaffolding can start during Phase 4 using mock data
- Phase 7 test corpus preparation can start during Phase 5

---

## Total Effort Estimate

| Phase | Effort | Confidence | Notes |
|-------|--------|------------|-------|
| 1: Validation Prototypes | 4d | High | Go/no-go gates before committing |
| 2: Infrastructure | 3-4d | High | Contract, DB schema, Docker, health checks |
| 3: Ingestion Pipeline | 4-5d | Medium | Olaf compilation is the risk; dual sample-rate pipeline |
| 4a: Exact ID Lane (Olaf) | 2-3d | Medium | CFFI wrapper + consensus scoring |
| 4b: Vibe Lane (CLAP+Qdrant) | 2-3d | Medium | Embedding generation + chunk aggregation |
| 5: Orchestration | 2-3d | High | Parallel lane execution, error handling |
| 6: Frontend | 4-5d | High | Recorder, uploader, results, search page |
| 7: Evaluation | 3-4d | Medium | Test corpus, metrics, go/no-go assessment |
| **Total** | **26-38d** | | **Critical path: ~22-30d** (with 4a/4b parallelism) |

**Confidence range**: The 12-day spread reflects uncertainty in Olaf compilation (Phase 1/3), CLAP CPU inference speed (Phase 1), and browser recording edge cases (Phase 6). The validation phase collapses this range early.

---

## Key Decision Points & Go/No-Go Gates

### Gate 1: After Phase 1 (Validation Prototypes)
| Prototype | Go Criteria | No-Go Action |
|-----------|-------------|-------------|
| Olaf 5s Mic Accuracy | >=80% clean, >=50% mic | Switch to Dejavu (pure Python) |
| CLAP CPU Inference | p95 < 3s for 10s clip | Switch to PANNs Cnn14 or budget GPU |
| CLAP Sample Rate Quality | 16kHz vs 48kHz cosine sim > 0.95 | Must use dual-rate pipeline (48kHz for CLAP) |
| Browser End-to-End | >=90% decode, >=70% fingerprint | Investigate codec/format issues |
| Qdrant Load Test | query p95 < 500ms, RAM < 4GB | Reduce chunking or use brute-force at 20K |

### Gate 2: After Phase 3 (Ingestion Pipeline)
- Can `make ingest` process 100 tracks end-to-end without error?
- Are tracks visible in PostgreSQL, Olaf LMDB, and Qdrant?
- Does `make rebuild-index` cleanly reset and re-ingest?

### Gate 3: After Phase 5 (Orchestration)
- Does `POST /api/v1/search` return results from both lanes?
- Is p95 latency < 5s for a 10s audio clip?
- Does one lane failing gracefully return partial results from the other?

### Gate 4: After Phase 7 (Evaluation)
- Does exact ID achieve >=75% top-1 accuracy on mic recordings?
- Does vibe search return >=60% "playlist-worthy" results (human eval)?
- Is end-to-end p95 latency < 5s?

---

## Risk Summary

### Top 5 Risks (from 07-deliverables.md + 08-devils-advocate-review.md)

| # | Risk | Likelihood | Impact | Mitigation | Phase |
|---|------|-----------|--------|------------|-------|
| 1 | Olaf CFFI compilation fails on target platform | Medium | High | Validate in Phase 1 Prototype 1; Dejavu as Plan B | 1 |
| 2 | ~~CLAP CPU inference exceeds latency budget (>5s)~~ | ~~High~~ | ~~High~~ | **RETIRED** — HF Transformers CLAP inference is 0.208s p50 (sub-300ms), well within 3s budget | 1 |
| 3 | ~~laion-clap pip package dependency conflicts (numpy/PyTorch)~~ | ~~Medium~~ | ~~Medium~~ | **RETIRED** — Switched to HuggingFace Transformers CLAP; no laion-clap dependency | 1 |
| 4 | Browser MediaRecorder codec inconsistency (Safari) | Medium | Medium | Test in Phase 1 Prototype 4; server-side ffmpeg handles any codec | 1, 6 |
| 5 | 48kHz PCM pipeline complexity / storage overhead | Low | Medium | Dual-rate decode in parallel; no 48kHz cache (on-the-fly) | 3 |

### Additional Risks
| # | Risk | Mitigation |
|---|------|------------|
| 6 | Olaf AGPL-3.0 license restricts commercialization | Document; Dejavu (MIT) as Plan B if needed |
| 7 | Memory spikes during batch ingestion (concurrent workers + CLAP model) | Limit concurrency; process sequentially if <16GB RAM (dev) or <32GB RAM (production at scale) |
| 8 | Qdrant collection corruption | Persistent Docker volume + snapshots; `make rebuild-index` recovers |
| 9 | ffmpeg version differences across platforms | Pin ffmpeg >=5.0 in Docker; test on macOS Homebrew |
| 10 | Cold-start latency (CLAP model load ~1s with HF Transformers on first request) | Pre-load in FastAPI lifespan handler [Updated: was ~5-15s with laion-clap; now ~1s with HF Transformers] |

---

## Finalized v1 Stack

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Web framework | FastAPI | >=0.115 | API server |
| ASGI server | Uvicorn | >=0.34 | HTTP server |
| Relational DB | PostgreSQL | 16 | Track metadata, content dedup fingerprints |
| Vector DB | Qdrant | v1.16.3 | Chunked audio embeddings (~940K vectors) |
| Query-time fingerprinting | Olaf (C + CFFI) | latest | Short-fragment exact ID with offset, LMDB index |
| Content dedup fingerprinting | Chromaprint (pyacoustid) | >=1.3 | Ingestion-time only, not used for search |
| Embedding model | HuggingFace Transformers CLAP (`laion/larger_clap_music_and_speech`) | >=4.40 | HTSAT-large, 512-dim audio embeddings, Apache-2.0 |
| Audio decoding | ffmpeg | >=5.0 | All formats -> PCM (16kHz + 48kHz) |
| Metadata extraction | mutagen | >=1.47 | ID3, Vorbis, MP4 tags |
| Frontend | SvelteKit + Svelte 5 | ^2.51 / ^5.51 | Search UI with mic recording |
| Browser recording | MediaRecorder API | native | WebM/Opus at 128kbps (MP4/AAC Safari fallback) |

### Key Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Fingerprint engine | Olaf (not Chromaprint) | Designed for short fragments; Chromaprint 0% on mic recordings |
| Fingerprint storage | Olaf LMDB (not PostgreSQL) | Inverted index is O(matches), not O(n_tracks) |
| Embedding sample rate | 48kHz (not 16kHz) | CLAP requires 48kHz; 16kHz produces garbage embeddings |
| Fingerprint sample rate | 16kHz | Olaf's documented default per JOSS paper |
| Chunking strategy | 10s window, 5s hop | ~47 chunks per track, enables aggregate ranking |
| Vector count | ~940K (not ~20K) | One per chunk, not one per track |
| Browser bitrate | 128kbps (not 64kbps) | Preserves spectral detail for fingerprinting |
| Search latency model | max(exact, vibe) | asyncio.gather waits for both; not min() |
| CLAP model lifecycle | Pre-load in lifespan | Avoids ~1s cold-start on first request [Updated: HF Transformers loads in ~1.1s vs ~22s for laion-clap] |

---

## Quick Reference: Phase Deliverables & Acceptance Criteria

| Phase | Delivers | Acceptance Criteria |
|-------|----------|-------------------|
| 1: Validation | Go/no-go decisions for all tech choices | All 5 prototypes produce pass/fail results |
| 2: Infrastructure | API contract, DB schema, Docker+Qdrant, health checks, Pydantic schemas | `make dev` starts all services; migrations run cleanly; health checks fail-fast |
| 3: Ingestion | `make ingest AUDIO_DIR=...` populates PG + Olaf LMDB + Qdrant | 100 tracks ingested; metadata, fingerprints, and embeddings all present |
| 4a: Exact ID Lane | `run_exact_lane()` returns ExactMatch results with offset | Known tracks matched with >80% accuracy on clean clips |
| 4b: Vibe Lane | `run_vibe_lane()` returns VibeMatch results with chunk aggregation | Similar-genre tracks ranked higher than dissimilar ones |
| 5: Orchestration | `POST /api/v1/search` with parallel lanes, timeouts, error isolation | Endpoint returns combined results; one lane failing doesn't kill the other |
| 6: Frontend | Search page at `/search` with recorder, uploader, results | Recording works in Chrome/Firefox/Safari; results display correctly |
| 7: Evaluation | Accuracy metrics, latency benchmarks, go/no-go assessment | Documented metrics against target thresholds |

---

## References

- Reconciliation Summary: `docs/research/01-initial-research/00-reconciliation-summary.md`
- Devil's Advocate Review: `docs/research/01-initial-research/08-devils-advocate-review.md`
- Implementation Plan: `docs/research/01-initial-research/06-implementation-plan.md`
- Deliverables: `docs/research/01-initial-research/07-deliverables.md`
- Reality Check: `docs/research/01-initial-research/09-reality-check.md`

---

## Devil's Advocate Review

> **Reviewer**: plan-reviewer
> **Date**: 2026-02-14
> **Reviewed against**: All research files in `docs/research/01-initial-research/`

### Confidence Assessment

**Overall: MEDIUM-HIGH** — The plan is well-structured and clearly derived from the reconciliation summary. The 7-phase structure with explicit gates is sound. Main concerns are around effort estimates and the absence of contingency time.

### Gaps Identified

1. **No contingency buffer in the 26-38d estimate.** The range already reflects uncertainty, but there is no explicit "Phase 0.5: Deal with unexpected blockers" buffer. If Olaf compilation takes a full extra day (common with C/CFFI), the critical path stretches immediately. Industry standard is 15-20% buffer on development estimates.

2. **Parallelism opportunity for Phase 6 is understated.** The overview notes "UI wireframing/component scaffolding can start during Phase 4 using mock data" but no effort hours are allocated for this. If you don't actually start Phase 6 early, the critical path is 22-30d sequential. If you do, who does it? Single-developer projects can't parallelize 4a/4b AND 6 simultaneously.

3. **No mention of the AGPL licensing decision.** Risk #6 mentions Olaf's AGPL-3.0 license, but the Go/No-Go gates don't include a licensing review. If the project is ever intended for commercial use, this is a blocking concern that should be resolved in Phase 1 — before building the entire system around Olaf.

4. **Gate 2 acceptance criteria are too loose.** "Can `make ingest` process 100 tracks end-to-end without error?" is binary — it doesn't specify acceptable throughput. If 100 tracks take 12 hours, is that a Go? At ~47 chunks per track and ~2s CLAP inference per chunk, 100 tracks could take ~2.6 hours. The gate should specify a time target.

5. **Missing: data backup/recovery strategy.** The plan mentions `make rebuild-index` but doesn't address what happens if PostgreSQL data is lost. Olaf LMDB and Qdrant can be rebuilt from raw audio, but track metadata (UUIDs, timestamps) would be regenerated differently, breaking any external references.

### Edge Cases Not Addressed

1. **What happens if Phase 1 produces a "Marginal" result on multiple prototypes simultaneously?** The individual prototype docs address marginal results, but the overview doesn't define what to do if, say, Olaf is marginal AND CLAP is marginal AND browser E2E is marginal. Three marginal results collectively may warrant a No-Go even if none individually does.

2. **Single-developer bottleneck.** The parallelism diagram shows 4a/4b running in parallel, but a single developer can only context-switch between them, not truly parallelize. The "2-3 days" estimate for each lane assumes dedicated focus; interleaving them might extend to 4-5 days total rather than the implied 2-3.

### Feasibility Concerns

1. **26-38d range is wide (46% spread).** While the plan acknowledges this, stakeholders may anchor on 26d. Consider providing a "most likely" point estimate (e.g., 32d) alongside the range.

2. **Phase 7 (Evaluation) requires 200 mic recordings.** This is a physical-world activity that can't be parallelized with coding. If the developer is also the person recording, Phase 7's "3-4 days" is likely underestimated (see Phase 7 review for details).

### Missing Dependencies

1. **System dependencies not tracked.** The stack table lists software versions but doesn't mention: `fftw3` (Olaf), `lmdb` (Olaf), `libmagic` (python-magic), `chromaprint` (pyacoustid), `ffmpeg >= 5.0`. These need to be installed on every development machine and in Docker. A "Prerequisites" section or Dockerfile is missing from this overview.

2. **No Docker build strategy.** The plan uses `docker-compose.yml` for PostgreSQL and Qdrant but doesn't mention Dockerizing the service itself. If the service runs outside Docker, system dependencies (Olaf C library, FFTW, LMDB, libmagic) must be installed on the host. This is fragile and should be addressed.

### Recommended Changes

1. **Add a 15% contingency buffer** to the effort estimate (total becomes ~30-44d).
2. **Add a licensing gate** to Phase 1: "Confirm Olaf AGPL-3.0 is acceptable for intended use case."
3. **Add throughput criteria** to Gate 2: e.g., "100 tracks ingested in under 4 hours."
4. **Define a compound-marginal policy**: "If 2+ prototypes are Marginal, treat the project as Conditional Go and add 3 days to the estimate."
5. **Add a system dependencies checklist** (or Dockerfile) as a Phase 2 deliverable.
6. **Specify "most likely" effort estimate** alongside the range (suggested: ~32d).

---

## Phase 1 Validation Amendments (2026-02-14)

The following changes were applied based on Phase 1 validation prototype results:

1. **CLAP implementation switched** from `laion-clap` (HTSAT-tiny only) to HuggingFace Transformers CLAP (`laion/larger_clap_music_and_speech`), providing true HTSAT-large with 67.8M audio encoder parameters
2. **Model load time**: Improved from 22.2s (MARGINAL) to 1.1s (GO) with HF Transformers
3. **Peak memory**: Reduced from 1,578 MB to 844 MB with HF Transformers
4. **Inference latency**: 0.208s p50 for 10s clips (vs 0.03s with laion-clap) — both well within 3s GO threshold
5. **Dual-rate pipeline confirmed**: 48kHz for CLAP, 16kHz for Olaf (mean cosine similarity at 16kHz = 0.88, below 0.95 threshold)
6. **Olaf compilation risk retired**: Compiles cleanly on macOS ARM64, bundles own FFT (pffft) and LMDB
7. **Qdrant performance exceeds expectations**: p95 = 4.2ms at 50K vectors (threshold: <200ms)
8. **Production RAM**: 32GB minimum recommended (Qdrant ~11GB + CLAP ~844MB + PostgreSQL + OS)
9. **Development RAM**: 16GB sufficient with smaller datasets
