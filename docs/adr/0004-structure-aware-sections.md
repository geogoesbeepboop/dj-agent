# ADR 0004 — Structure-aware embeddings (sections as first-class rows)

**Status:** Accepted
**Date:** 2026-06-03
**Extends:** `0002-clap-from-the-start.md`

## Context

`vibe/clap.py` windows each track into ~10 s segments, embeds each, and
**mean-pools them into a single 512-d vector per track.** That single averaged
point is fine for discovery ("tracks like this") but wrong for two things a DJ
agent actually needs:

1. **Transitions are between *parts*, not tracks.** What matters at a mix is the
   *outro of A* and the *intro of B* — not the track averages. Averaging a
   6-minute track with an ambient intro and a peak drop yields a mushy midpoint
   that represents neither.
2. **DJs rarely play whole tracks.** A set might use only the bridge and the
   chorus of one track, or the chorus into the outro of another. A track-average
   vector can't express "I want *this part*."

The author's framing: *"for some mixes we aren't even going to use the entire
song — maybe just the bridge and the chorus. If we segment the full song, we can
take that into account."*

## Decision

Make **musical sections first-class rows.** During ingestion, segment each track
into functional sections (`intro` / `verse` / `build` / `chorus` / `drop` /
`break` / `bridge` / `outro`) and store **one `sections` row per section**, each
with:

- `label`, order `idx`, time bounds (`start_s`, `end_s`), `start_beat`, `bars`;
- per-section **CLAP vector** (the vibe of *that part*);
- per-section **LUFS energy** (cross-track comparable, see `ADR 0005`);
- mixability flags: `is_mixin`, `is_mixout`, `loopable`.

Keep a **track-level CLAP vector too** (mean-pooled, on `tracks.embedding`) for
discovery and for spreading taste labels. So a track is: one discovery vector +
N section vectors + structured columns.

The Selector then chooses **tracks AND which sections to use AND cue points**;
the Mixer aligns transitions on section boundaries (phrase-aligned via
downbeats).

## Rationale

- **Right unit for the right job.** Track vector for discovery; section vectors
  for transition matching. Each query hits the representation that fits it.
- **Enables partial-track sets** — the capability that motivated the decision.
- **Structure-aware, not time-aware.** Segmenting by *musical function* beats
  both the old mean-pool (loses structure) and naive fixed 10 s chunks (a
  section boundary rarely lands on a 10 s tick).
- **Better cue points for free.** Section boundaries are exactly the cue points
  the Mixer needs; computing them once at ingest serves both Selector and Mixer.

## Trade-offs accepted

- **More rows and more embeddings** (≈5–10 sections/track → ~5–10× section
  vectors). Still trivial at library scale: 2k tracks × ~8 sections × 512-d ≈
  ~30 MB. HNSW stays sub-ms.
- **Segmentation quality varies.** Boundary detection is good; *labeling* a
  section "chorus" vs "drop" is heuristic. We accept approximate labels in v1
  (energy + position + repetition heuristics over detected boundaries) and refine
  with a dedicated model later. See `ADR 0005` for the detector choice.
- **Schema and Selector complexity.** A `sections` table + section-aware
  selection logic. Worth it — partial-track mixing is a headline capability.

## Consequences

- `schema.sql`: new `sections` table (FK to `tracks`, per-section vector + bounds
  + flags), HNSW index on `sections.embedding`. See `docs/database.md`.
- `vibe/clap.py`: add `embed_sections(path, boundaries)` returning a vector per
  section; keep `embed_audio` (track-level mean pool) and `embed_text`.
- New `audio/segment.py`: track → list of `(label, start_s, end_s, beat, bars)`.
- `store.py`: `nearest_section(...)` (KNN over section vectors with mix-flag and
  Camelot/BPM filters) alongside the existing track-level `nearest`.
- `curator.py`: per track → segment → analyze + embed track + embed each section.
