# Embeddings

Two distinct embedding signals, plus a per-section variant. Don't conflate them:

| Signal | Model | Dim | Answers |
|---|---|---|---|
| **Acoustic vibe** (general) | CLAP `larger_clap_music` | 512 | "what does this sound like?" |
| **Section vibe** (general) | CLAP, per section | 512 | "what does *this part* sound like?" |
| **Taste** (personal) | sentence-transformer on my notes | 384 | "what do *I* think of this?" |

CLAP is the **acoustic backbone** — necessary, not sufficient. The **taste**
signal (`docs/taste.md`, `ADR 0003`) is what makes the sets mine. The hard mixing
facts (BPM, key, LUFS, section bounds) are stored as plain columns, never in a
vector (`docs/database.md`).

---

## CLAP: one space for audio *and* text

**CLAP** (Contrastive Language-Audio Pretraining) is trained on millions of
(audio clip, text caption) pairs; the objective pulls a clip and its caption
together and pushes mismatches apart. The payoff:

> An audio clip and a text description of it land near each other.

That gives two search modes against the same stored vectors:

- **audio → audio** (`store.nearest`): "tracks like this seed track."
- **text → audio** (`store.nearest_to_text`): "tracks that feel like *this
  phrase*" — e.g. `nearest_to_text("dreamy nocturnal deep house")`.

Text→audio works **on day one** because the semantics are baked into the learned
space. We use **`laion/larger_clap_music`** (the music variant) for tighter
musical clustering.

### Track vector vs section vectors

CLAP was trained on short (~10 s) clips, so we window the audio and embed each
window. We then store CLAP at **two granularities** (`ADR 0004`):

- **Track-level** (`tracks.embedding`): mean-pool *all* windows → one vector.
  Good for **discovery** ("tracks like this") and for **spreading taste labels**
  to acoustic neighbors. A track average is the right unit *for those jobs.*
- **Section-level** (`sections.embedding`): mean-pool only the windows inside a
  detected section → one vector per intro/chorus/drop/outro/…. This is the right
  unit for **transitions** (the outro of A vs the intro of B) and for using *part*
  of a track. A whole-track average is the *wrong* unit here — it blends an
  ambient intro with a peak drop into a meaningless midpoint.

```
load(path, sr=48000)
  → detect section boundaries (audio/segment.py, ADR 0005)
  → window into ~10 s clips
  → CLAP.get_audio_features(window) per window  (512-d each)
  → mean-pool ALL windows           → tracks.embedding
  → mean-pool per-section windows   → sections.embedding[i]
  → L2-normalize everything         → cosine is the right metric
```

`path` is whatever the Curator hands over — a file from a local folder *or* one
the link ingester just downloaded (`dj/ingest`: Spotify/YouTube URL → yt-dlp →
flac). Embedding doesn't care about provenance, with one honest caveat:
YouTube-sourced audio tops out at YouTube's quality (~128–160 kbps opus,
re-encoded into a flac container). That's fine for CLAP analysis and listening;
buy the file for anything precious.

`embed_text(prompt)` is the same idea without windowing:
`ClapProcessor(text=...) → get_text_features → L2-normalize`.

---

## The taste vector (personal)

The note I write about a track (`"hands-in-the-air drop, sunset opener"`) is
embedded by a small local **sentence-transformer** (`all-MiniLM-L6-v2`, 384-d)
into `tracks.taste_vec`. This is a *different space* from CLAP — it captures my
words, not the audio. Untagged tracks get a provisional taste vector propagated
from tagged CLAP-neighbors. Full mechanics, blended scoring (`α·acoustic +
β·taste + γ·rating`), and active learning live in `docs/taste.md`.

Why a separate text embedder instead of CLAP's text encoder? CLAP's text tower is
tuned for *caption-like* phrases ("a recording of deep house with a warm pad"),
not idiosyncratic personal notes, and is a weaker general text embedder.
Note-to-note taste similarity is better served by a dedicated sentence model. We
keep CLAP's text encoder for its actual strength: text→audio search.

---

## Running locally (Apple Silicon)

`clap.py` picks the device automatically: **MPS** (Metal) on Apple Silicon, CUDA
if present, else CPU. The sentence-transformer is tiny and runs anywhere.

- **First run** downloads ~1.5 GB of CLAP weights (one-time, HF-cached) and a
  small (~90 MB) sentence-transformer.
- **Per-track encoding** on an M1 Pro: ~1–3 s for CLAP (now a bit more with
  per-section pooling + segmentation). This is an *ingestion* cost only; every
  later query is a sub-millisecond pgvector lookup.

---

## Cosine similarity in pgvector

pgvector's `<=>` operator is cosine distance (0 = identical, 2 = opposite).
Vectors are L2-normalized, so cosine and Euclidean give the same ranking. HNSW
(`vector_cosine_ops`) makes search approximate but sub-ms at library scale; drop
the index for exact results only in evals.

---

## Metadata tags: a cheap complementary signal

`metadata.py` reads ID3 tags (genre, mood, comment) via `mutagen` into
`tracks.tags`. These are sparse and inconsistent, so they **complement** CLAP and
taste as a keyword filter — they don't replace either. Link-ingested files are
sparser still: a downloaded track carries only what the ingester stamps
(artist/title/album, plus ISRC when it came via Spotify — no genre or mood), so
for that growing slice of the library the embeddings are doing *all* of the
semantic work.

---

## The embedding-model option space

Why CLAP stays the acoustic backbone, and what we add around it:

| Model | Text→audio? | Local/free | Verdict |
|---|---|---|---|
| **CLAP** (`larger_clap_music`) | ✅ shared space | ✅ | **Keep** — best open text→audio |
| MERT | ❌ audio-only | ✅ | Stronger acoustic repr, but loses day-one text search |
| OpenL3 / VGGish | ❌ | ✅ | Older, weaker semantics |
| Essentia mood/genre models | tags, not vectors | ✅ | Optional **add** — structured danceability/mood signal |
| sentence-transformer on my notes | ✅ (text-text) | ✅ | **Add** — this is the taste vector (`ADR 0003`) |

Net: CLAP (acoustic, general) + sentence-transformer (taste, mine) + optional
Essentia tags (structured mood) = hybrid retrieval, the current best practice.

This table was deliberately re-validated on 2026-06-10 after the link-ingestion
rescope; the full argument — alternatives weighed, what embeddings *don't* do,
and the fallback ladder if text→audio neighbors disappoint — is in
`why-vibe-vectors.md` ("Reassessed 2026-06-10").

---

## Open questions

- **CLAP variant**: `larger_clap_music` is default. If text prompts feel weak,
  try prompt expansion first (several descriptor sentences, averaged probes —
  see the 2026-06-10 reassessment in `why-vibe-vectors.md`), then
  `larger_clap_music_and_speech`, which handles language better at some cost to
  music clustering. Swap via `CLAP_MODEL`.
- **Section pooling**: mean-pool per section for v1. A salience-weighted pool
  (emphasize the densest bars) could sharpen the drop/chorus vectors later.
- **Taste model**: `all-MiniLM-L6-v2` (384-d) for speed. A larger model
  (`bge-base`, etc.) if note similarity feels coarse. Swap via `TASTE_MODEL`.
