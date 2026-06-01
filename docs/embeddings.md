# Embeddings

## What a "vibe vector" is

Every track in the library is represented as a single point in a 28-dimensional
space. Two tracks that are close in that space (low cosine distance) feel
similar to listen to back-to-back. That's the entire abstraction — the Selector
just asks "who are the nearest neighbors to this track?" and gets candidates
that fit the vibe.

---

## v1: Engineered feature vector (`src/dj/vibe/embed.py`)

No ML model. Pure signal processing via librosa → hand-crafted 28-d vector.

### Layout

| Slot | Feature | Scaling |
|---|---|---|
| 0 | BPM | clipped to [60, 200], then 0..1 |
| 1 | Energy mean | RMS × 5, clipped 0..1 |
| 2 | Spectral centroid | scaled 0..8000 Hz → 0..1 |
| 3 | Spectral rolloff | scaled 0..11025 Hz → 0..1 |
| 4 | Zero-crossing rate | raw, already ≈ 0..1 |
| 5–17 | 13 MFCCs | tanh(x / 50) → squashed –1..1 |
| 18–25 | 8-pt energy curve | already 0..1 per-track |
| 26 | Camelot number | 1–12 → divide by 12 |
| 27 | Camelot letter | A=0, B=1 |

The final vector is **L2-normalized** (`v / ‖v‖`), making the pgvector `<=>`
(cosine distance) operator the right similarity metric.

### Why this works as a first cut

- BPM + Camelot encode the two hardest DJ constraints (tempo + key) directly
  into the vector — "vibe" queries naturally return candidates that will mix.
- MFCCs capture timbre (warm/bright, acoustic/electronic, etc.).
- The energy curve encodes whether a track builds, drops, or stays flat — the
  Architect's arc planning lives here.

### Limitations

- No learned semantics: the vector doesn't know "dreamy" vs "aggressive" as
  concepts; it only sees signal statistics.
- 28 dimensions is low. Distinct vibes may not separate cleanly for large
  libraries (>5k tracks).
- Key estimation (Krumhansl-Schmuckler) is approximate; tracks with ambiguous
  tonality may land in the wrong Camelot bucket.

---

## How tracks are analyzed (`src/dj/audio/analyze.py`)

```
librosa.load(path, sr=22050, mono=True)
  → y (waveform array), sr (sample rate)

librosa.beat.beat_track(y, sr)          → BPM
librosa.feature.chroma_cqt(y, sr)      → pitch class → Camelot (Krumhansl)
librosa.feature.rms(y)                 → energy mean + 8-point curve
librosa.feature.spectral_centroid(y)   → brightness
librosa.feature.spectral_rolloff(y)    → high-freq rolloff
librosa.feature.zero_crossing_rate(y)  → percussiveness proxy
librosa.feature.mfcc(y, n_mfcc=13)    → timbre fingerprint
```

Output: `TrackFeatures` dataclass — the contract between analysis and embedding.
`embed()` consumes `TrackFeatures`; `store.upsert_track()` consumes the result.

---

## Cosine similarity in pgvector

pgvector's `<=>` operator is cosine distance (0 = identical, 2 = opposite).

```sql
SELECT path, bpm, camelot, embedding <=> %s AS distance
FROM tracks
ORDER BY distance
LIMIT 10;
```

Because the vectors are L2-normalized, cosine distance equals Euclidean
distance (up to a constant factor) — either would give the same ranking.

The HNSW index (`vector_cosine_ops`) makes this query approximate but fast.
For a 10k-track library, approximate is fine; for evals you can force exact.

---

## v5: CLAP learned embeddings (Phase 5)

**CLAP** (Contrastive Language-Audio Pretraining) maps audio and text into the
same embedding space. This enables queries like:

> "Find me something dreamy and nocturnal"

…without any label or metadata — just the audio.

### What changes

- `VIBE_DIM` → 512 (CLAP's output dim)
- `embed.py` is replaced by a CLAP inference call
- `schema.sql` needs a migration (`ALTER COLUMN embedding TYPE vector(512)`)
- Everything else (store, curator, selector) is unchanged

### Why we defer it

CLAP requires a GPU or slow CPU inference (~2–5s/track). The engineered v1
vector takes ~0.1s/track on CPU. For building and testing the full pipeline
(including Phase 2–4 agents and mixer), v1 is fast enough and teaches the
same vector-DB concepts. CLAP is strictly an upgrade, not a prerequisite.

---

## Open questions

- **CLAP model choice**: `laion/clap-htsat-fused` (general) or
  `laion/larger_clap_music` (music-specific)? The music model will cluster
  genres more tightly; the general model handles text prompts better. Decision
  can wait until Phase 5.
- **Normalization of MFCCs**: the `/50.0` constant is a rough calibration for
  typical speech/music values. Worth measuring the actual MFCC range on your
  library and tuning if vibe search feels wrong.
