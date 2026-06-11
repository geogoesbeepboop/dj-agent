# Why vibe vectors genuinely belong here

The build plan references this doc for the "both-halves-true" case for
embeddings/pgvector. Here's the argument in full.

---

## The two tests for whether RAG/vector search is appropriate

**Test 1 — Is relevance fuzzy?**
If you can write a deterministic rule that answers "does this fit?", you don't
need embeddings. If the answer is "it depends on feel," you do.

For a DJ set: "does this track fit the vibe?" is definitionally fuzzy.
BPM range and Camelot compatibility are necessary but not sufficient. Two tracks
at 124 BPM in 8A can have completely different energy textures (a rolling tech
house groove vs a sparse indie vocal). The vector space captures that difference
where a rule list can't.

✓ **Relevance is fuzzy.**

**Test 2 — Is the corpus large and open-ended?**
If you're ranking 5 hand-curated options, nearest-neighbor search is overkill.
If you're searching an unbounded collection, it earns its cost.

A music library is large (thousands of tracks) and open (new files are ingested
continuously). Exhaustive pairwise comparison at set-generation time would be
O(n²). The HNSW index makes it O(log n) with no precision loss at library scale.

✓ **Corpus is large and open.**

Both halves are true → embeddings + vector search are the right tool.

---

## Why not a traditional recommender?

Classic collaborative filtering (Spotify's core approach) requires:
- User interaction data at scale (plays, skips, likes across millions of users)
- A shared item catalog

You have neither — you have *your* library and *your* taste. So the personal
signal comes from **explicit labels** (notes + ratings on the tracks you care
about), embedded into a taste vector and propagated across the library via CLAP
neighbors (`adr/0003-personal-taste-layer.md`, `taste.md`). CLAP supplies the
acoustic backbone from the signal itself; your labels supply the "me." No
cross-user data needed.

## Text→audio is a day-one capability

Because CLAP embeds audio and text into the *same* space, a text prompt
("dreamy and nocturnal") can be encoded and compared directly against the stored
audio vectors (`store.nearest_to_text`). So semantic vibe search isn't a future
upgrade — it's available as soon as the library is ingested. See
`embeddings.md`.

The other thing Spotify can't do: **audio manipulation**. Their licensing
prevents time-stretching, EQ, or crossfading. That's the hardware moat this
project has that no streaming service can replicate.

---

## Why not just sort by BPM and key?

BPM + Camelot compatibility is a necessary constraint, not a sufficient
similarity metric. Two tracks can be BPM-compatible and Camelot-compatible and
still clash texturally (one is a hard techno kick, one is a soft ambient pad).

CLAP encodes timbre, instrumentation, and overall feel learned from millions of
(audio, caption) pairs — *not* hand-picked DSP stats. Nearest-neighbor in that
space returns candidates that are texturally similar; the personal **taste
vector** then re-ranks them toward what *I* actually like (`taste.md`).

BPM/Camelot compatibility is a hard filter the Selector applies *after* the vibe
search narrows the candidate pool — you get the best of both. And because
sections are embedded too (`adr/0004-structure-aware-sections.md`), the same
logic applies at the *part* level (the outro of A vs the intro of B), not just
whole tracks.

---

## The agent-specific case

The Selector (Phase 3) is an LLM agent (on `dj.llm` `complete()`; a Claude Agent
SDK MCP server can wrap the toolbelt later) that calls `query_vibe_db` (blended
acoustic+taste) and `nearest_section` as tools. This is the right split:

- **LLM**: interprets the vibe prompt ("sunset rooftop, slow build to peak"),
  decides how to weight exploration vs exploitation, reasons about set narrative
- **Vector search**: handles the fuzzy retrieval that the LLM can't do efficiently
  in-weights ("which 128-BPM track in 8A has the highest energy in the back half?")

The agent earns its API cost for the genuinely ambiguous planning problem. The
vector DB earns its infrastructure cost for the search problem. Neither
substitutes for the other.

---

## Reassessed 2026-06-10 — are embeddings still the right call?

The project just rescoped around two new edges: the library now grows by
pasting links (`dj/ingest`: a Spotify or YouTube URL → resolved metadata →
yt-dlp download → the same Curator path as a local folder), and every approved
set now ships in two playable forms — rekordbox XML for me to perform, rendered
mix to press play (ADR 0010). A good moment to re-ask the foundational question
deliberately instead of assuming the original answer still holds.

**Verdict: keep CLAP. The embeddings are the load-bearing mechanism, not
decoration.**

The product's input is a sentence — "dreamy and nostalgic, bedroom set vibes" —
and its output is an ordered set drawn from *my* audio files. CLAP's shared
text↔audio space is the only piece in the stack that turns that sentence into a
ranking over my files without me hand-tagging every track first. And it isn't a
side feature: the brief drives retrieval end-to-end today —

```
brief → clap.embed_text (acoustic probe) + taste embed_note (taste probe)
      → store.ranked (α·acoustic + β·taste + γ·rating blend)
      → arc-ordered slots → verify → render / export
```

Remove the embeddings and that pipeline has no first step.

### Alternatives weighed (and why they lose *today*)

1. **An LLM over metadata/genre tags.** The LLM can't hear the files. "Dreamy"
   and "nostalgic" rarely live in ID3 tags, and link ingestion makes tags
   *thinner*, not richer — a downloaded file carries only what the ingester
   stamps (artist/title/album, ISRC when Spotify-resolved), no mood field at
   all. An LLM ranking on those tags would be guessing from artist names, which
   is exactly the hand-waving this project exists to avoid.
2. **Supervised mood classifiers.** They need labeled training data I don't
   have, and they impose a fixed vocabulary. My briefs aren't drawn from a
   fixed vocabulary — free text is the point.
3. **Audio-only embedding models (MERT etc.).** Arguably stronger acoustic
   representations, but no text tower → no text→audio search → the brief can't
   reach the library. MERT is a candidate *addition*, not a replacement: if
   CLAP's musical resolution disappoints, a second acoustic space is one more
   column on the same row — the exact pattern `taste_vec` already established
   (ADR 0003) — with no architectural change.

### What embeddings do NOT do here

Worth being honest about, because it's easy to over-credit the vectors:

- **Hard mixing math.** BPM, Camelot, LUFS, and section cue points are plain
  columns (ADR 0005); the Critic and Mixer never consult a vector for them.
- **My judgment.** That's the 384-d taste vector — embeddings of *my words*,
  not of audio, propagated across CLAP neighbors (ADR 0003). CLAP knows what a
  track sounds like; only my notes know whether I'd open a set with it.

Semantics in vectors, mixing math in columns. The reassessment didn't move that
line; it confirmed it.

### Known weaknesses, and the test that could kill this

- **~10 s training windows.** CLAP saw short clips, so we window and mean-pool
  (ADR 0004). Section vectors mitigate the worst of it, but a track vector is
  still an average.
- **Short prompts are under-specified.** "dreamy and nostalgic" is a thin probe
  for a 512-d space. Expanding the brief into several descriptor sentences and
  averaging the text probes is a cheap, likely win — now in `backlog.md`.
- **The whole bet is measurable.** The kill-or-keep test is `evals.runner`'s
  A/B (blended vs CLAP-only) after I've tagged ~50 favorites. If text→audio
  neighbors feel wrong on the real library, the fallback ladder is: prompt
  expansion → blend-weight tuning → a second acoustic space (MERT) — *not*
  abandoning vectors. Each rung is additive; none reopens the schema or the
  agent loop.
