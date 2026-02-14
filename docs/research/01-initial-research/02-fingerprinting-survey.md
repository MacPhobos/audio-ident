# Section 2 — Exact ID Lane: Acoustic Fingerprinting Survey

> Last Verified: 2026-02-14

## 2.1 Candidate Survey

### Overview Decision Matrix

| Criterion | **Olaf** | **Panako** | **audfprint** | **Chromaprint/AcoustID** | **Dejavu** |
|---|---|---|---|---|---|
| **Primary use-case** | Short fragment search in large archives | Time-scale & pitch-invariant fingerprinting | Landmark-based search for ads/clips in archives | Full-track identification & dedup | Full-track and short-clip recognition |
| **Language** | C (with Python CFFI wrapper) | Java (JVM) | Python (native) | C (with Python bindings via `pyacoustid`) | Python (native) |
| **5s mic robustness** | **High** — designed for short fragments, noise-robust landmarks | **High** — handles noise, reverb, time/pitch modification | **Medium-High** — landmark-based, similar to Shazam approach | **Low** — designed for full-track, fails on short mic recordings (0% in comparative tests) | **High** — 100% recall at 5s from clean source, ~80-90% with noise |
| **Offset estimation** | **Yes** — returns time offset | **Yes** — returns time offset with pitch/speed correction | **Yes** — returns alignment offset | **No** — identifies track only, no sub-track offset | **Yes** — uses hash offset alignment |
| **Noise/reverb tolerance** | **High** — spectral peak-based, tolerant to background noise | **High** — explicitly handles degraded audio | **Medium-High** — depends on peak density, phone mic EQ can reduce peaks | **Low** — requires clean, near-complete audio | **Medium** — relies on MySQL for storage, performance degrades with noise |
| **Phone mic EQ/clipping** | Moderate tolerance — peak positions stable under mild EQ | Good — pitch-invariant helps with mic coloration | Moderate — sensitive to strong EQ that shifts peak positions | Poor — fingerprint designed for near-lossless comparisons | Moderate — same peak-based approach as others |
| **Scalability (20K tracks)** | **Excellent** — LMDB backend, handles millions of fingerprints | **Good** — LMDB backend, designed for scale | **Good** — hash table in memory, tested on large collections | **Excellent** — designed for millions of tracks (AcoustID DB) | **Poor-Medium** — MySQL backend, slower at scale |
| **Storage/index** | LMDB key-value store (hash → track_id, time) | LMDB key-value store | In-memory hash table (pickle file) | SQLite or external DB | MySQL database |
| **Python integration** | **CFFI wrapper** — `pip install`-able, calls compiled C | **Subprocess** — must shell out to Java JAR | **Native Python** — direct import | **`pyacoustid` bindings** — `pip install pyacoustid` | **Native Python** — direct import |
| **FastAPI friendliness** | Medium — need to compile C, but CFFI is async-compatible | Low — JVM subprocess adds latency and complexity | **High** — pure Python, integrates directly | High — `pyacoustid` is simple pip install | Medium — MySQL dependency adds infrastructure |
| **License** | **AGPL-3.0** (copyleft) | **AGPL-3.0** (copyleft) | **MIT** | **LGPL-2.1** (Chromaprint) / service TOS (AcoustID) | **MIT** |
| **Last commit** | Active in 2025 (copyright extends to 2025) | Active — GitHub issues from Apr 2025 and Oct 2024 | **Stale** — last significant updates ~2017, Python 2.7 era | Chromaprint: sporadic commits 2023-2024 | **Inactive** — last meaningful update ~2020, open issues unanswered |
| **Maintenance status** | **Active** — Joren Six (academic, UGent) | **Active** — same maintainer as Olaf | **Abandoned** — Dan Ellis (Google), no longer maintained | **Low maintenance** — Lukáš Lalinský, infrequent updates | **Abandoned** — worldveil, community forks exist |

---

### Detailed Analysis per Candidate

#### Olaf (Overly Lightweight Acoustic Fingerprinting)

- **Repo**: [github.com/JorenSix/Olaf](https://github.com/JorenSix/Olaf)
- **Author**: Joren Six (Ghent University, IPEM)
- **Paper**: ISMIR 2020 Late-Breaking Demo — [PDF](https://archives.ismir.net/ismir2020/latebreaking/000001.pdf)
- **Architecture**: Spectral peak constellation → hash pairs → LMDB inverted index
- **Key strength**: Designed explicitly for **short audio fragments** in large archives. Runs on ESP32 and Arduino — extremely lightweight.
- **Python wrapper**: Available via CFFI ([blog post](https://0110.be/posts/A_Python_wrapper_for_Olaf_-_Acoustic_fingerprinting_in_Python)). Requires compilation step per platform but works well once built.
- **WASM support**: Compiles to WebAssembly via Emscripten — could theoretically run fingerprinting in the browser (v2 feature).
- **Concerns**: AGPL-3.0 license means any server using it must release source (acceptable for our internal project, but limits commercial reuse). CFFI compilation adds a build step.
- **Storage**: LMDB is embedded (no external DB dependency), fast, and proven.

#### Panako

- **Repo**: [github.com/JorenSix/Panako](https://github.com/JorenSix/Panako)
- **Author**: Joren Six (same as Olaf)
- **Paper**: ISMIR 2014, updated in Panako 2.0 (ISMIR 2021) — [PDF](https://archives.ismir.net/ismir2021/latebreaking/000039.pdf)
- **Architecture**: Frequency-domain landmarks with **time-scale and pitch modification handling**
- **Key strength**: Can identify tracks even when they've been sped up, slowed down, or pitch-shifted. Achieves **89.33% accuracy on recorded (mic) audio** in comparative benchmarks.
- **Java dependency**: Requires JRE 11+. No native Python API — must invoke via `subprocess` or build a REST wrapper.
- **Docker support**: Panako 2.1 includes Docker support, which helps deployment.
- **Concerns**: JVM startup latency (~1–2s) hurts query responsiveness. Subprocess wrapping is fragile. No pip-installable package.
- **Storage**: LMDB backend (same as Olaf).

#### audfprint (Dan Ellis / LabROSA)

- **Repo**: [github.com/dpwe/audfprint](https://github.com/dpwe/audfprint)
- **Author**: Dan Ellis (Columbia/Google)
- **Architecture**: Classic Shazam-style landmark fingerprinting, heavily inspired by Wang (2003)
- **Key strength**: Pure Python, well-documented algorithm, direct librosa integration.
- **Critical concern**: **Effectively abandoned.** Written for Python 2.7, last significant update ~2017. Dependencies on old librosa versions. Would require significant porting effort for Python 3.12+.
- **Performance**: Good for its era, but the in-memory hash table doesn't scale well compared to LMDB-backed solutions.
- **Verdict**: **Not recommended** — too much maintenance risk. Would need to fork and modernize.

#### Chromaprint / AcoustID

- **Repo**: [github.com/acoustid/chromaprint](https://github.com/acoustid/chromaprint)
- **Python bindings**: [pyacoustid](https://github.com/beetbox/pyacoustid) — `pip install pyacoustid`
- **Author**: Lukáš Lalinský
- **Architecture**: Chroma-based fingerprint using first 120 seconds of audio. Designed for **whole-track identification**.
- **Critical limitation for our use case**: Comparative research shows **0% accuracy on microphone-recorded audio** and requires queries from the **beginning of the recording**. Cannot detect audio when query is from the middle of a track.
- **Strengths**: Excellent for full-track dedup, large ecosystem (AcoustID web service with millions of tracks), easy `pip install`.
- **Still useful for**: Duplicate detection during ingestion (§5.3 of the research prompt), not for query-time search.
- **Verdict**: **Not suitable for 5s mic query** — but keep for duplicate detection in the ingestion pipeline.

#### Dejavu

- **Repo**: [github.com/worldveil/dejavu](https://github.com/worldveil/dejavu)
- **Author**: Will Drevo
- **Architecture**: Shazam-style landmark fingerprinting with MySQL backend
- **Key strength**: **Pure Python**, well-documented, achieves **100% recall at 5 seconds** from clean audio, ~96% at 2 seconds.
- **Offset detection**: Yes — uses hash offset alignment to determine time position.
- **Critical concerns**:
  - **Inactive maintenance** — last meaningful update ~2020, open issues from 2024 going unanswered
  - **MySQL dependency** — requires running MySQL, adds infrastructure complexity
  - **Scalability** — MySQL-based hash lookups are slower than LMDB at scale
  - **Python 2 legacy** — some code paths have Python 2 patterns
- **Community forks**: Several maintained forks exist ([denis-stepanov](https://github.com/denis-stepanov/dejavu), [yunpengn](https://github.com/yunpengn/dejavu)) with improvements.
- **Verdict**: Viable as Plan B if Olaf's C compilation proves problematic. The MySQL dependency is the biggest drawback.

---

## 2.2 Recommendation: Olaf for v1

### Primary Choice: **Olaf**

**Justification:**

1. **Designed for short fragments** — This is Olaf's explicit use case. It's not a "works okay with short clips" afterthought; the entire system is optimized for finding short audio fragments in large archives.

2. **Python integration via CFFI** — The Python wrapper exists and is documented. CFFI is compatible with async Python (FastAPI/uvicorn). The compilation step is a one-time build.

3. **LMDB backend** — No external database dependency. LMDB is embedded, fast, and well-tested. At 20K tracks, the entire index fits in memory easily.

4. **Active maintenance** — Joren Six at Ghent University actively maintains both Olaf and Panako. Academic maintenance with institutional backing is more reliable than hobbyist projects.

5. **Offset estimation** — Returns the time position of the match, which is a key UX feature.

6. **Lightweight** — Runs on ESP32. Our server will have no trouble.

7. **WASM compilation** — Future path to browser-side fingerprinting (reduce upload latency).

**Trade-offs accepted:**
- AGPL-3.0 license (acceptable for internal/open-source project)
- C compilation step (one-time, documented)
- CFFI wrapper is less ergonomic than pure Python

### Plan B: **Dejavu** (community fork)

If Olaf's C compilation or CFFI wrapper proves unreliable in our Docker/CI environment:

1. Use the [denis-stepanov fork](https://github.com/denis-stepanov/dejavu) which has more recent updates.
2. Replace MySQL with **SQLite** (or PostgreSQL, which we already run) to eliminate the MySQL dependency.
3. Accept slightly lower scalability (hash lookups via SQL vs. LMDB).
4. Benefit from pure Python — zero compilation issues.

### Why not Panako?

Panako is technically excellent (handles pitch/speed changes) but the **Java dependency** is a dealbreaker for a Python/FastAPI stack. Subprocess invocation adds 1–2s JVM startup latency per query, making the <5s latency target harder to meet. If we ever need pitch-invariant matching (DJ mixes, speed-altered rips), we can revisit Panako as a v2 option.

---

## 2.3 Parameter Guidance

### Audio Preprocessing

| Parameter | Value | Rationale |
|---|---|---|
| **Resample rate** | **16000 Hz** (Olaf default) | Olaf's default configuration expects 16kHz monophonic 32-bit float audio ([Source: Olaf JOSS Paper](https://www.theoj.org/joss-papers/joss.05459/10.21105.joss.05459.pdf)). Previous version of this document incorrectly stated 8000 Hz. |
| **Channels** | **Mono** | Stereo provides no benefit for fingerprinting. Mix down with equal weighting. |
| **Bit depth** | **32-bit float** (f32le) | Olaf expects 32-bit float. Convert from s16le if needed. |
| **Normalization** | **Peak normalize to -3 dBFS** | Ensures consistent peak detection across quiet and loud recordings. |
| **High-pass filter** | **80 Hz cutoff** (mic recordings only) | Removes rumble/handling noise from phone mics. Not needed for clean MP3 clips. |

### WebM/Opus Decoding (Browser Audio)

```bash
# Decode WebM/Opus to fingerprint-ready PCM
ffmpeg -i input.webm \
  -vn \                        # no video
  -acodec pcm_s16le \          # 16-bit PCM
  -ar 16000 \                  # resample to 16kHz (Olaf default)
  -ac 1 \                      # mono
  -af "highpass=f=80" \        # remove mic rumble
  -f wav \                     # WAV output
  pipe:1                       # stream to stdout (for in-memory processing)
```

For MP4/AAC (Safari):
```bash
ffmpeg -i input.mp4 \
  -vn -acodec pcm_s16le -ar 16000 -ac 1 \
  -af "highpass=f=80" \
  -f wav pipe:1
```

### Query Strategy for 5s Mic Recordings

**Approach: Overlapping Sub-Windows with Consensus**

For a 5-second mic recording, reliability improves by querying multiple overlapping sub-windows and requiring consensus:

1. **Split the 5s clip into overlapping windows:**
   - Window 1: 0.0s – 3.5s
   - Window 2: 0.75s – 4.25s
   - Window 3: 1.5s – 5.0s

2. **Query each window independently** against the Olaf index.

3. **Consensus scoring:**
   - If 2+ windows return the same `(track_id, offset±tolerance)`, confidence is HIGH.
   - If 1 window returns a match and others return nothing, confidence is LOW.
   - If windows return different tracks, treat as AMBIGUOUS.

4. **Offset reconciliation:**
   - Align the offsets from multiple windows (they should differ by the hop amount).
   - Final offset = median of reconciled offsets.

**Why sub-windows?**

A 5s mic recording may have a transient noise event (cough, door slam) that corrupts one portion. By querying overlapping sub-windows, we increase the chance that at least one window captures clean fingerprint-worthy audio.

### Confidence Thresholds

| Level | Aligned Hashes | Action |
|---|---|---|
| **Strong match** | ≥ 20 aligned hashes (across sub-windows) | Return as top-1 exact match with high confidence |
| **Probable match** | 8–19 aligned hashes | Return as candidate, flag for user verification |
| **Weak/spurious** | < 8 aligned hashes | Discard — likely false positive from random collisions |
| **No match** | 0 aligned hashes | Return empty exact-match result; rely on vibe lane |

These thresholds are starting points and must be calibrated during the evaluation phase (§2.4).

### Optional: Denoising

For very noisy environments, apply lightweight denoising before fingerprinting:

- **Option A**: `ffmpeg` `-af "afftdn"` (FFT-based denoiser, built into ffmpeg) — adds ~100ms latency
- **Option B**: `noisereduce` Python package (spectral gating) — ~200ms for 5s clip
- **Recommendation**: Start without denoising. Add it only if evaluation shows significant accuracy gains on noisy mic recordings. Denoising can remove musical content too.

---

## 2.4 Exact ID Evaluation Plan

### Test Dataset Construction

| Set | Count | Source | Purpose |
|---|---|---|---|
| **Clean clips** | 200 | Extract random 10s clips from library MP3s (known track + offset) | Baseline accuracy on ideal input |
| **Mic recordings** | 200 | Play library tracks through speakers, record 5s with phone/laptop mic in varied environments (quiet room, office, café, street) | Real-world mic accuracy |
| **Browser WebM** | 50 | Record via the actual SvelteKit UI using Chrome/Firefox/Safari MediaRecorder API | End-to-end browser pipeline validation |
| **Negative controls** | 50 | Recordings of tracks NOT in the library | False positive rate measurement |

### Recording Environment Matrix (for mic set)

| Environment | Count | Expected Difficulty |
|---|---|---|
| Quiet room, 1m from speaker | 50 | Easy |
| Office with HVAC + keyboard noise | 50 | Medium |
| Café / restaurant background | 50 | Hard |
| Outdoor / street noise | 50 | Very hard |

### Metrics

| Metric | Target | How to Measure |
|---|---|---|
| **Top-1 accuracy** (clean clips) | ≥ 98% | Fraction of clean clips correctly identified as rank-1 |
| **Top-1 accuracy** (mic recordings) | ≥ 75% | Fraction of mic recordings correctly identified |
| **Top-1 accuracy** (browser WebM) | ≥ 70% | Fraction of WebM recordings correctly identified |
| **Top-5 accuracy** (mic recordings) | ≥ 85% | Correct track appears in top-5 results |
| **Offset error** | median < 0.5s | Absolute difference between estimated and true offset |
| **False positive rate** | < 2% | Fraction of negative controls returned as matches |
| **Query latency** | < 2s (p95) | Time from PCM ready to results returned (leaves 3s for preprocessing + vibe lane) |
| **Latency breakdown** | Measure separately | Decode time + fingerprint extraction + index lookup + consensus scoring |

### Iteration Protocol

1. **Baseline run**: Default Olaf parameters, no denoising, no sub-windowing.
2. **Add sub-windowing**: Measure improvement on mic recordings.
3. **Add denoising**: Measure improvement on café/outdoor recordings.
4. **Tune confidence thresholds**: Use ROC curve (true positive rate vs. false positive rate) to find optimal threshold.
5. **Browser-specific testing**: Run WebM recordings through the full pipeline to validate codec handling.
6. **Document parameter sensitivity**: Which parameters have the biggest impact on which recording conditions?

### Tooling

- **Ground truth labels**: CSV mapping `(recording_id, true_track_id, true_offset_sec, environment, device, codec)`
- **Automated evaluation script**: `make eval-fingerprint` target that runs all recordings through the pipeline and computes metrics
- **Results dashboard**: Markdown table auto-generated from eval script output

---

## References

- Olaf: [GitHub](https://github.com/JorenSix/Olaf) | [Paper](https://archives.ismir.net/ismir2020/latebreaking/000001.pdf) | [Python wrapper blog](https://0110.be/posts/A_Python_wrapper_for_Olaf_-_Acoustic_fingerprinting_in_Python)
- Panako: [GitHub](https://github.com/JorenSix/Panako) | [Paper (2014)](https://archives.ismir.net/ismir2014/paper/000122.pdf) | [Panako 2.0 (2021)](https://archives.ismir.net/ismir2021/latebreaking/000039.pdf)
- audfprint: [GitHub](https://github.com/dpwe/audfprint)
- Chromaprint: [GitHub](https://github.com/acoustid/chromaprint) | [AcoustID](https://acoustid.org/chromaprint)
- pyacoustid: [GitHub](https://github.com/beetbox/pyacoustid)
- Dejavu: [GitHub](https://github.com/worldveil/dejavu) | [Blog](https://willdrevo.com/fingerprinting-and-audio-recognition-with-python/)
- Wang, A. (2003). "An Industrial-Strength Audio Search Algorithm." [PDF](https://www.ee.columbia.edu/~dpwe/papers/Wang03-shazam.pdf)
- Comparative analysis of fingerprinting algorithms: [IJCSET Paper](https://www.ijcset.com/docs/IJCSET17-08-05-021.pdf)
