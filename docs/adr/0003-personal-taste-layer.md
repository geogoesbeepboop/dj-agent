# ADR 0003 — Personal taste layer (notes + light tags, blended with CLAP)

**Status:** Accepted
**Date:** 2026-06-03
**Extends:** `0002-clap-from-the-start.md`

## Context

This is a **personal** DJ agent — for the author and friends, not a product.
The original plan had only one personal signal: an `is_favorite` boolean derived
from a folder name. CLAP gives a *generic* acoustic embedding ("what does this
sound like to a model trained on the internet"), which knows nothing about the
author's taste. Two tracks the author experiences as completely different vibes
can sit adjacent in CLAP space because they're acoustically similar.

For a personal agent, "what *I* think of this track" is the whole point, and it
was missing. Collaborative filtering (Spotify's approach) is unavailable — there
is no cross-user interaction data, only one person's library and reactions.

## Decision

Add a **personal taste layer** alongside the CLAP acoustic vector:

1. **Input = notes + light structured tags.** For tracks the author cares about,
   capture a free-text **note** ("hands-in-the-air drop, great sunset opener"),
   a **rating** (1–5), and a **role** (`warmup` / `peak` / `closer` / …). Notes
   are expressive and in the author's own words; tags are cheap filters.
2. **Two vectors, one row.** Each `tracks` row carries the **CLAP acoustic
   vector** (`embedding`, 512-d, general) *and* a **taste vector** (`taste_vec`,
   384-d, the note embedded with `sentence-transformers`). They are separate
   columns, not a single fused vector, so we can search either alone or blended.
   We do **not** stand up a second "me" database — the separation lives in the
   columns, with no sync burden.
3. **Blended scoring.** Retrieval ranks by
   `score = α·acoustic_sim + β·taste_sim + γ·rating`, with α/β tunable per query
   (pure-discovery vs pure-taste vs balanced).
4. **Label propagation / active learning.** The author labels ~150 favorites,
   not the whole library. Untagged tracks receive a *provisional* taste vector
   from their tagged CLAP-neighbors; the agent surfaces the tracks where it is
   most uncertain (or where taste diverges from acoustics) as the next worth
   labeling. CLAP's job becomes *spreading sparse labels across the library.*

## Rationale

- **It makes the sets mine.** The taste vector is the only thing that
  distinguishes this from "a worse Spotify recommender."
- **Two vectors > one fused vector.** Keeping acoustic and taste separate lets
  us answer "acoustically similar" and "matches my taste" as distinct questions,
  and dial the blend. Fusing them throws that away.
- **Two columns > two databases.** A separate vector store doubles infra and
  forces an application-side join with sync lag, for no benefit at this scale
  (reaffirms `ADR 0001`).
- **Notes > tags-only.** Free text captures idiosyncratic taste a fixed tag
  vocabulary can't, and embeds into a comparable space. Tags remain for hard
  filtering (role, rating).
- **Active learning makes labeling tractable.** Tagging 2,000 tracks is a chore
  nobody finishes; tagging ~150 high-signal ones and propagating is realistic.

## Trade-offs accepted

- **A second embedding space** (384-d text) alongside CLAP's 512-d. Manageable:
  it's a small local model, and propagation bridges the two via acoustic
  neighborhood, not a shared space.
- **Cold start.** Until ~50 tracks are tagged, taste ranking ≈ acoustic ranking.
  Acceptable; α/β defaults lean acoustic early, taste-heavy as labels accumulate.
- **Manual effort up front.** Mitigated by the vibe-tagging skill (interview UX)
  and active-learning prioritization.

## Consequences

- `config`: add `TASTE_DIM = 384` and `TASTE_MODEL` (default
  `sentence-transformers/all-MiniLM-L6-v2`). `VIBE_DIM = 512` unchanged.
- `schema.sql`: add `taste_note TEXT`, `taste_vec vector(384)` (nullable),
  `taste_source TEXT`, `rating SMALLINT`, `role TEXT` to `tracks`, plus an HNSW
  index on `taste_vec`. See `docs/database.md`.
- New `dj/taste/` module: note embedding, propagation, blended scoring, and the
  tagging entry point. See `docs/taste.md`.
- The Taste loop is **Phase 2** (the next build), not Phase 5.
