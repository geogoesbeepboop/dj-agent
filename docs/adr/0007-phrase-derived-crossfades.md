# ADR 0007 — Phrase-derived crossfade length

**Status:** Accepted
**Date:** 2026-06-04
**Relates to:** `0004-structure-aware-sections.md`, `0005-calibrated-beats-and-energy.md`

## Context

The Mixer (`dj/mixer.py`) crossfades out of one track's section into the next.
The open question (`docs/phases.md`) was whether the crossfade length should be a
**fixed number of bars** (e.g. always 8) or **computed from the section's phrase
grid**.

A fixed length is wrong at both ends: an 8-bar crossfade *swallows* a 4-bar outro
(you're fading into the next track before the current section even establishes),
and it *underuses* a 32-bar breakdown (a long, luxurious blend was available and
we cut it short). Because Phase 1 already detects sections with downbeat-anchored
bounds (`ADR 0004/0005`), the bar count of each section is known for free.

## Decision

**Derive the crossfade length from the outgoing section's bar grid.** The overlap
is **half the outgoing section's bars, clamped to [1, 8] bars** (`crossfade_bars`),
then converted to seconds at the transition's target tempo (`crossfade_seconds`):

```
bars     = section_bars(outgoing_len_s, bpm)          # length in bars (4/4)
xfade    = clamp(bars // 2, 1, 8)                      # half the section, capped
seconds  = xfade * 4 * 60 / target_bpm                 # bars → seconds at tempo
```

The cap (8 bars) keeps even a long breakdown from dragging; the floor (1 bar)
keeps a tiny section from producing a click-cut. When the section length is
unknown (no cue points), it falls back to a safe 4-bar default.

## Rationale

- **A blend should live inside one phrase.** Halving the section keeps the
  crossfade proportional to the musical unit it sits in, so the mix lands on the
  phrase regardless of whether the part is 4 or 32 bars.
- **The data is already there.** Section bounds are downbeat-anchored at ingest, so
  "how many bars is this part" is a column lookup, not new analysis.
- **Tempo-correct by construction.** Converting bars → seconds at the *target*
  tempo (the tempo B is beatmatched to) means the overlap is exactly N bars of the
  playing track, not N bars of B's original tempo.

## Trade-offs accepted

- **Half-the-section is a heuristic, not a musical truth.** Some transitions want a
  quick cut, others a long roll; "half, capped at 8" is a sane default, not the
  optimum. A future per-transition policy (driven by section labels — quick out of
  a `drop`, long out of a `break`) can refine it. The constant `MAX_XFADE_BARS`
  centralizes the cap for tuning.
- **Ignores B's section length.** We size the crossfade from A's outgoing section
  only. If B's mix-in section is shorter than the overlap, the render clamps to the
  available audio (`min(cross, len(mix), len(seg))`), so it's safe but can be
  shorter than planned.

## Consequences

- `dj/mixer.py`: `crossfade_bars` / `crossfade_seconds` / `section_bars` are pure
  and unit-tested (`test_mixer.py`); `plan_transitions` uses them per adjacent
  pair, sizing each crossfade from the outgoing slot's cue length and the arc's
  target tempo.
- The fixed-vs-phrase open question in `docs/phases.md` is resolved (phrase-derived).
