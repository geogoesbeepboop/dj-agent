# Embeddings (CLAP)

## What a "vibe vector" is

Every track is a point in a 512-dimensional space. Two tracks close in that
space (low cosine distance) feel similar to hear back-to-back. The vector
captures **semantics** — "dreamy," "driving," "warm," "aggressive" — not raw
signal statistics. The hard mixing facts (exact BPM, key, energy arc) are stored
*separately* as plain columns (see `database.md`); the vector is purely "what
does this feel like."

---

## CLAP: one space for audio *and* text

**CLAP** (Contrastive Language-Audio Pretraining) is a neural network trained on
millions of (audio clip, text caption) pairs. The training objective pulls a
clip and its caption *together* in the embedding space and pushes mismatched
pairs apart. The payoff:

> An audio clip and a text description of it land near each other.

That single property gives us two search modes against the same stored vectors:

- **audio → audio** (`store.nearest`): "tracks like this seed track."
- **text → audio** (`store.nearest_to_text`): "tracks that feel like *this
  phrase*" — e.g. `nearest_to_text("dreamy nocturnal deep house")`.

This is why the "won't work without semantics" prompts work **on day one** — the
semantics are baked into the learned space, not computed from DSP stats.

We use **`laion/larger_clap_music`** (the music-specific variant) because it
clusters musical semantics more tightly than the general CLAP.

---

## How a track becomes a vector (`src/dj/vibe/clap.py`)

**No LLM call, no API.** The model weights run locally; "embedding" just means
"run the audio through the network and take the output vector."

```
1. librosa.load(path, sr=48000)        # CLAP's required input rate
2. window the waveform into ~10 s segments   (CLAP was trained on short clips)
3. ClapModel.get_audio_features(window)  → a 512-d vector per window
4. mean-pool the windows                 → one vector for the whole track
5. L2-normalize                          → cosine distance is the right metric
```

Mean-pooling windowed embeddings is the standard way to turn a model trained on
short clips into a whole-song embedding. We cap the number of windows
(`_MAX_WINDOWS`) so very long tracks don't blow up ingestion time.

`embed_text(prompt)` is the same idea without windowing:
`ClapProcessor(text=...) → get_text_features → L2-normalize`.

---

## Running locally (Apple Silicon)

`clap.py` picks the device automatically: **MPS** (Metal) on Apple Silicon,
CUDA if present, else CPU.

- **First run** downloads ~1.5 GB of weights (one-time, cached by HuggingFace).
- **Per-track encoding** on an M1 Pro is roughly **1–3 s** — this is an
  *ingestion-time* cost only. You embed each track once; the vector is stored,
  and every later query is a sub-millisecond pgvector lookup.
- So a 2,000-track library is a ~1-hour background ingest, then instant forever.

---

## Cosine similarity in pgvector

pgvector's `<=>` operator is cosine distance (0 = identical, 2 = opposite).
Because vectors are L2-normalized, cosine and Euclidean give the same ranking.

```sql
SELECT path, bpm, camelot, embedding <=> :q AS distance
FROM tracks
ORDER BY distance
LIMIT 10;
```

The HNSW index (`vector_cosine_ops`) makes this approximate but fast — fine at
library scale; force exact (drop the index) only for evals.

---

## Metadata tags: a cheap complementary signal

`metadata.py` reads ID3 tags (genre, mood, comment) via `mutagen` and stores
normalized keywords in `tags TEXT[]`. The Selector can keyword-filter on these
("dreamy" matches if a human/source *tagged* it dreamy). Tags are sparse and
inconsistent, so they **complement** CLAP rather than replace it — CLAP infers
semantics from the audio even for untagged files. Remote sources (Jamendo) can
supply richer mood tags that merge with file tags at ingest.

---

## Why not just sort by BPM and key?

BPM + Camelot compatibility is necessary but not sufficient. Two tracks can be
tempo- and key-compatible yet clash texturally (a hard techno kick vs a soft
ambient pad). CLAP captures that texture; BPM/Camelot become a hard *filter*
applied on top of the vibe ranking. See `why-vibe-vectors.md`.

---

## Open questions

- **Model variant**: `laion/larger_clap_music` (music) is the default. If text
  prompts feel weak, `laion/larger_clap_music_and_speech` handles language a bit
  better at some cost to music clustering. Swap via `CLAP_MODEL` env var.
- **Windowing strategy**: we sample evenly-spaced windows. A structure-aware
  variant (weight the drop/chorus) could improve fidelity later.
