# Section 9 — Reality Check

> Last Verified: 2026-02-14

This section provides honest, experience-informed expectations for what the v1 system will and won't achieve.

---

## 9.1 Realistic Hit Rates for 5s Mic Recordings

### Expected Accuracy by Environment

| Environment | Exact ID (Fingerprint) | Vibe Match (Top-5 "playlist-worthy") | Notes |
|---|---|---|---|
| **Quiet room, good speaker, 1m distance** | **85–95%** | **70–85%** | Near-ideal conditions. Failures mainly from very quiet passages or tracks with sparse instrumentation (few spectral peaks). |
| **Office with HVAC + typing** | **65–80%** | **60–75%** | HVAC adds broadband noise that raises noise floor. Typing creates transient peaks that confuse fingerprinting. |
| **Café / restaurant** | **40–60%** | **50–65%** | Conversations create competing spectral content. Clinking, music from other sources. Fingerprinting degrades significantly. |
| **Outdoor / street** | **20–40%** | **40–55%** | Wind noise, traffic, and unpredictable transients. Fingerprinting struggles. Embeddings are more robust because they capture overall timbral character. |
| **Very loud venue (concert, bar)** | **10–25%** | **30–50%** | Near-field music from speakers drowns the target. Fingerprinting is nearly impossible. Embedding quality also degrades. |

**Key insight**: Embeddings degrade more gracefully than fingerprints in noisy conditions. This is because embeddings capture high-level features that survive noise, while fingerprint hashes depend on precise spectral peak positions that get displaced by noise.

### Why These Numbers, Not Higher

Shazam achieves ~90%+ in most conditions because:
1. They have **billions of fingerprints** with massive redundancy
2. Their algorithm has been tuned over 20+ years with billions of queries
3. They use **proprietary noise-cancellation preprocessing**
4. Users typically hold the phone **close to the speaker**

Our system will have:
1. 20K tracks (not billions) — less redundancy in the hash space
2. A v1 implementation with limited tuning
3. Open-source preprocessing (ffmpeg filters)
4. Varied recording distances and devices

**Realistic expectation**: 70–80% exact ID accuracy across all conditions, with 85%+ achievable in good conditions after parameter tuning.

---

## 9.2 Browser WebM/Opus Specific Hit Rates

### Expected Accuracy for Browser-Captured Audio

| Browser | Codec | Expected Exact ID | Expected Vibe Match | Notes |
|---|---|---|---|---|
| **Chrome** (desktop) | WebM/Opus | **75–85%** | **65–80%** | Best-case browser. Good Opus encoder, consistent behavior. |
| **Firefox** (desktop) | WebM/Opus | **70–80%** | **60–75%** | Slightly different Opus parameters than Chrome. Generally comparable. |
| **Safari** (desktop) | MP4/AAC | **70–80%** | **60–75%** | AAC is well-understood. Safari's MediaRecorder is newer but functional. |
| **Chrome** (Android) | WebM/Opus | **60–75%** | **55–70%** | Phone mics have significant EQ coloration, lower quality than desktop mics. |
| **Safari** (iOS) | MP4/AAC | **55–70%** | **50–65%** | iOS mic processing adds noise suppression that can remove musical content. iOS Safari MediaRecorder support was added later and may have quirks. |

### Browser-Specific Challenges

1. **Variable sample rate**: Browsers may default to 44.1kHz or 48kHz depending on device. Always resample server-side.
2. **Opus lossy compression**: At low browser bitrates (24–64kbps), Opus removes spectral detail that fingerprinting relies on. We use 128kbps to mitigate this (see Section 10).
3. **iOS audio processing**: iOS applies aggressive noise suppression and automatic gain control that can distort musical content. Consider requesting raw audio if possible.
4. **MediaRecorder API inconsistencies**: Not all browsers support all codecs equally. Safari's MediaRecorder support is still relatively new.
5. **Latency**: Audio upload time adds to total query latency. A 5s WebM/Opus clip at 128kbps is ~80KB — upload should take <1s on any reasonable connection.

### Recommendation

Request **WebM/Opus at 128kbps** (Chrome/Firefox) or **MP4/AAC at 128kbps** (Safari) from the MediaRecorder API. Higher bitrate preserves more spectral detail for fingerprinting.

```javascript
// MediaRecorder configuration
const mediaRecorder = new MediaRecorder(stream, {
  mimeType: 'audio/webm;codecs=opus', // Chrome/Firefox
  audioBitsPerSecond: 128000, // 128kbps — higher quality for fingerprinting
});
```

---

## 9.3 Common Exact ID Failure Reasons

| # | Failure Reason | Frequency | Mitigation |
|---|---|---|---|
| 1 | **Background noise exceeds signal** | Very common (noisy environments) | Suggest quiet recording environment in UI; add optional denoising |
| 2 | **Recording too quiet / far from speaker** | Common | Add audio level metering in UI; warn user if input is too quiet |
| 3 | **Spectral peaks displaced by mic EQ** | Common (phone mics) | High-pass filter + Olaf's noise-robust peak detection |
| 4 | **Track not in library** | Common (user expectation mismatch) | Clearly communicate library size; fallback to vibe search |
| 5 | **Recording during quiet passage** | Moderate | Few spectral peaks → few hashes → insufficient evidence. UI guidance: "record during a distinctive part of the song" |
| 6 | **Clipping / distortion** | Moderate (loud environments) | Clipping creates harmonics that generate false peaks. Add gain limiter in browser recording |
| 7 | **Codec artifacts from lossy compression** | Low-moderate | Opus at low bitrate removes spectral detail. Request 128kbps minimum |
| 8 | **Different master / remix in library** | Low | A remastered version has different dynamics than the original playing in the room |
| 9 | **Very short recording (<3s)** | Low | Insufficient hashes for reliable matching. Enforce minimum 3s in UI |
| 10 | **Olaf CFFI binding issue / platform incompatibility** | Low (one-time) | Comprehensive CI testing; Docker build ensures consistent compilation |

---

## 9.4 Common Vibe Similarity Disappointment Reasons

| # | Disappointment | Why It Happens | Mitigation |
|---|---|---|---|
| 1 | **"Similar" tracks are same genre but wrong mood** | Model captures genre features strongly but mood is more nuanced | Fine-tune or use multi-embedding approach (v2). Add mood/energy filtering. |
| 2 | **Results biased toward popular genres** | Training data imbalance in CLAP (more pop/rock than niche genres) | Monitor per-genre quality; consider fine-tuning on underrepresented genres |
| 3 | **Cross-genre matches seem random** | Timbral similarity across genres isn't always perceptually meaningful | Add genre filter option in UI |
| 4 | **All results from same artist** | Multiple tracks by one artist cluster together (similar production style) | Add diversity constraint: max 2–3 results per artist |
| 5 | **Vocals dominate embeddings** | CLAP trained on audio-text pairs where vocals/lyrics strongly correlate with text | For instrumental similarity, vocal separation (v2) would help |
| 6 | **Short query captures unrepresentative moment** | 5s from an intro sounds different from the chorus | UI guidance: "record a distinctive/characteristic part" |
| 7 | **Slow/fast tempo mismatch** | A slow ballad and fast dance track can have similar timbral qualities | Add BPM proximity filter as post-processing |
| 8 | **"It just doesn't *feel* similar"** | Subjective — vibe is deeply personal | Provide feedback mechanism (thumbs up/down) for future ranking improvements |

---

## 9.5 Biggest Improvement Levers

Ranked by **impact vs. effort**:

| # | Lever | Impact | Effort | When to Apply |
|---|---|---|---|---|
| 1 | **UI recording guidance** ("hold phone near speaker", audio level meter, quiet environment prompt) | **HIGH** | Low | v1 — improves raw input quality, affects both lanes |
| 2 | **Confidence threshold tuning** with real evaluation data | **HIGH** | Low | After initial evaluation — reduces false positives and improves perceived accuracy |
| 3 | **Denoising preprocessing** (ffmpeg `afftdn` or `noisereduce`) | **MEDIUM-HIGH** | Low | After baseline evaluation shows noise as primary failure mode |
| 4 | **Higher bitrate browser recording** (128kbps Opus/AAC) | **MEDIUM** | Very Low | v1 — simple MediaRecorder config change |
| 5 | **Multi-window query strategy** for fingerprinting | **MEDIUM** | Medium | v1 if single-window accuracy is <70% |
| 6 | **Genre/BPM/energy post-filtering** for vibe results | **MEDIUM** | Medium | v1 — improves perceived vibe quality significantly |
| 7 | **CLAP fine-tuning** on your library | **HIGH** (long-term) | High | v2 — requires labeled similar pairs, significant ML engineering |
| 8 | **Vocal separation** (Demucs/HTDemucs) as preprocessing for vibe | **MEDIUM-HIGH** | High | v2 — separate vibe matching for vocals vs. instrumentals |
| 9 | **User feedback loop** (thumbs up/down) → re-ranking model | **HIGH** (long-term) | Very High | v3 — requires significant data collection and ML pipeline |
| 10 | **MERT model upgrade** for vibe embeddings | **MEDIUM** | Medium | v2 — music-specific model may capture vibe better than CLAP |

---

## 9.6 Maintenance / Deprecation Risk Assessment

### Risk Matrix for Recommended v1 Stack

| Component | Risk Level | Rationale | Mitigation |
|---|---|---|---|
| **Olaf** (fingerprinting) | **MEDIUM** | Single academic maintainer (Joren Six). Active but small community. No corporate backing. | Olaf's algorithm is simple and well-documented. Could fork and maintain internally if abandoned. Dejavu as Plan B. |
| **LAION CLAP** (embeddings) | **LOW-MEDIUM** | LAION-AI is a community-driven org. Model weights are archived on HuggingFace and won't disappear. Package (`laion-clap`) could become unmaintained but HuggingFace Transformers integration provides alternative. | Use HuggingFace Transformers as the integration path (more stable than LAION's pip package). Model weights are immutable once downloaded. |
| **Qdrant** (vector DB) | **LOW** | Backed by VC-funded company (Qdrant Solutions GmbH). Active development, frequent releases. Growing market (vector DBs). | Even if company fails, Qdrant is open-source (Apache-2.0). Data is exportable via snapshots. Could migrate to pgvector or Milvus. |
| **FFmpeg** (audio processing) | **VERY LOW** | Industry standard, massive community, 20+ years of development. | No realistic risk of abandonment. |
| **PyTorch** (ML runtime) | **VERY LOW** | Meta-backed, dominant ML framework. | No realistic risk. |
| **Python / FastAPI** | **VERY LOW** | Python is dominant language for ML. FastAPI is widely adopted. | No realistic risk in v1 timeframe. |

### Specific Deprecation Scenarios

**Scenario 1: Olaf CFFI wrapper breaks on new Python version**
- **Likelihood**: Moderate (CFFI is stable but compiler toolchains change)
- **Impact**: Can't fingerprint
- **Mitigation**: Pin Python version in Docker. Maintain fork of CFFI wrapper. Dejavu as fallback.

**Scenario 2: LAION-CLAP pip package becomes incompatible with new PyTorch**
- **Likelihood**: Moderate (LAION is community-maintained)
- **Impact**: Can't generate embeddings
- **Mitigation**: Use HuggingFace Transformers integration instead of laion-clap package directly. Transformers is maintained by a well-funded company (Hugging Face Inc.).

**Scenario 3: Qdrant introduces breaking API changes**
- **Likelihood**: Low (they maintain backwards compatibility)
- **Impact**: Client code needs updates
- **Mitigation**: Pin qdrant-client version. Qdrant has good migration documentation.

**Scenario 4: A significantly better embedding model emerges (2025-2026)**
- **Likelihood**: High (the field moves fast)
- **Impact**: Opportunity, not risk. Our architecture supports model swapping.
- **Mitigation**: Design the embedding pipeline to be model-agnostic. Re-ingesting 20K tracks takes hours, not days.

### Long-Term Stack Stability Assessment

The biggest risk is not component failure but **architecture drift** — the field of audio ML moves quickly, and the v1 embedding model may look outdated within 1–2 years. This is mitigated by:

1. **Model-agnostic vector storage** — Qdrant stores vectors regardless of source model. Swap models by re-ingesting.
2. **Chunking strategy is model-independent** — same audio chunks, different embeddings.
3. **Fingerprinting is algorithmically stable** — the Shazam-style landmark approach hasn't fundamentally changed since 2003. Olaf implements a mature algorithm.

---

## 9.7 Summary: What to Tell Stakeholders

> "With 20,000 tracks and 5-second mic recordings, expect **~70–80% exact identification accuracy** in typical environments (room, office), dropping to **~40–60%** in noisy settings (café, outdoor). Browser recordings via WebM/Opus perform **5–10% lower** than direct mic due to lossy compression.
>
> Vibe similarity will return **recognizably relevant results** in 60–80% of queries, but "vibe" is subjective — expect some disappointments. The system improves significantly with **user guidance** (record in quiet environments, hold phone near speaker) and **parameter tuning** based on real evaluation data.
>
> The recommended stack (Olaf + CLAP + Qdrant) is practical, well-documented, and maintainable. The biggest improvement levers are **recording quality guidance**, **confidence threshold tuning**, and **genre/energy post-filtering** — all low-effort, high-impact changes that can be applied iteratively."

---

## References

- Shazam Algorithm: Wang, A. (2003). [PDF](https://www.ee.columbia.edu/~dpwe/papers/Wang03-shazam.pdf)
- Chromaprint Comparative Benchmark: [IJCSET Paper](https://www.ijcset.com/docs/IJCSET17-08-05-021.pdf)
- Dejavu Accuracy Claims: [Blog](https://willdrevo.com/fingerprinting-and-audio-recognition-with-python/)
- CLAP Music Evaluation: [FAD Benchmark 2025](https://arxiv.org/html/2506.19085v1)
- Pex Audio Fingerprinting Benchmark: [Blog](https://pex.com/blog/evaluating-the-leaders-in-audio-matching-introducing-pexs-audio-fingerprinting-benchmark-toolkit/)
- Audio Fingerprinting Short Snippet Accuracy: [Springer](https://link.springer.com/article/10.1007/s11042-023-14787-2)
