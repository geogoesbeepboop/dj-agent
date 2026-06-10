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

The Selector (Phase 3) is an LLM agent (on agent-core `complete()`; a Claude Agent
SDK MCP server can wrap the toolbelt later) that calls `query_vibe_db` (blended
acoustic+taste) and `nearest_section` as tools. This is the right split:

- **LLM**: interprets the vibe prompt ("sunset rooftop, slow build to peak"),
  decides how to weight exploration vs exploitation, reasons about set narrative
- **Vector search**: handles the fuzzy retrieval that the LLM can't do efficiently
  in-weights ("which 128-BPM track in 8A has the highest energy in the back half?")

The agent earns its API cost for the genuinely ambiguous planning problem. The
vector DB earns its infrastructure cost for the search problem. Neither
substitutes for the other.
