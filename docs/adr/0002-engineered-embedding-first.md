# ADR 0002 — Engineered v1 embedding before CLAP

**Status:** Accepted  
**Date:** 2026-06-01

## Context

Vibe similarity search requires audio embeddings. Two options:

1. **v1 (chosen):** Engineered 28-d feature vector: BPM, Camelot, energy curve,
   MFCCs, timbre statistics. Pure numpy, no ML model.
2. **CLAP (Phase 5):** Contrastive Language-Audio Pretraining — 512-d learned
   embedding that maps audio and text into the same space.

## Decision

Ship **v1 engineered embedding** first; upgrade to CLAP in Phase 5.

## Rationale

- **No GPU dependency.** CLAP inference is 2–5s/track on CPU, 0.1s with a GPU.
  The engineered vector runs in ~0.1s on CPU regardless.
- **Faster feedback loop.** We can build and test the entire pipeline
  (Curator → Vibe DB → Architect → Selector → Mixer → Evals) without waiting
  on ML infrastructure.
- **Stable interface.** `embed.py` takes a `TrackFeatures` and returns a
  `(VIBE_DIM,) float32` array. That contract is unchanged by the CLAP swap;
  only `embed.py` and `VIBE_DIM` change.
- **Still teaches the right concepts.** The v1 vector lives in pgvector and is
  searched by cosine similarity — the same workflow as CLAP.

## Trade-offs accepted

- v1 has no learned semantics: "dreamy," "aggressive," "nostalgic" are not
  concepts it encodes. Vibe queries work by proximity in signal-statistics
  space, not semantic space.
- Key estimation (Krumhansl-Schmuckler) is approximate for ambiguous tonality.
- 28-d may not separate vibes cleanly for libraries > 5k tracks.

All three are fixed by the Phase 5 CLAP upgrade.
