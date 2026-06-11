# ADR 0002 — CLAP embeddings from the start

**Status:** Accepted (supersedes the original "engineered embedding first" plan)
**Date:** 2026-06-01

## Context

Vibe similarity search requires audio embeddings. The original scaffold shipped
an **engineered 28-d vector** (BPM, key, energy, MFCCs packed by hand) as a
stopgap, deferring **CLAP** (Contrastive Language-Audio Pretraining — 512-d
learned audio+text embeddings) to a later "Phase 5."

The engineered vector had two structural problems:
1. **No semantics.** It encoded signal statistics, not "dreamy / nocturnal /
   driving." Mood prompts couldn't work.
2. **Tangled concerns.** It crammed BPM and key *into* the embedding, mixing the
   hard mixing constraints with the soft vibe signal.

## Decision

Use **CLAP from the start** (`laion/larger_clap_music`, 512-d). Remove the
engineered `embed.py`. Store **two representations** per track: the CLAP vector
for semantics, and plain structured columns for the hard mixing constraints.

## Rationale

- **Semantic + text→audio search on day one.** CLAP embeds audio and text into
  one space, so `nearest_to_text("dreamy nocturnal")` works as soon as the
  library is ingested — the original blocker that justified CLAP.
- **Cleaner separation.** Semantics live in the vector; BPM/key/energy live in
  SQL columns the Selector filters on. No more packing tempo into the embedding.
- **Cost is acceptable and one-time.** CLAP runs locally on Apple Silicon (MPS),
  ~1–3 s/track. That's an *ingestion* cost; queries stay sub-millisecond. The
  user explicitly accepted the ingestion hit to build the real thing once.
- **No throwaway work.** Building the engineered vector first would mean writing
  code we'd delete. Going straight to CLAP avoids that.

## Trade-offs accepted

- **Heavier deps**: `torch` + `transformers` (~hundreds of MB) and a one-time
  ~1.5 GB model download.
- **Slower ingestion** than the engineered vector (~0.1 s/track) — but only at
  ingestion, and the user is fine with it.
- **Tests for CLAP are slow** (model download/inference) → marked `slow` and
  deselected from the default `pytest` run.

## Consequences

- `VIBE_DIM = 512`; `schema.sql` uses `vector(512)`.
- `vibe/clap.py` is the semantic core; `audio/analyze.py` is slimmed to
  structured features only (no MFCC/spectral).
- A metadata layer (`metadata.py`, `mutagen`) provides a cheap keyword-tag
  filter that complements CLAP.
