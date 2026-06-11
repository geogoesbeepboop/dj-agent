# ADR 0005 — Calibrated beats/downbeats and cross-track energy

**Status:** Accepted
**Date:** 2026-06-03

## Context

Two structured-feature gaps undermine the core promises (beatmatching and a
planned energy arc), independent of the embeddings:

1. **BPM from a single `librosa.beat.beat_track` estimate octave-errors** —
   it reports 70 for a 140-BPM track, 62 for 124, etc. Beatmatching lives or
   dies on tempo *and* downbeat phase; one global scalar with no downbeats isn't
   enough.
2. **Energy is normalized per track.** `analyze._downsample_normalized` min-max
   scales each track's RMS curve to 0–1, so a whisper-quiet ambient track and a
   slamming techno track both span 0→1. That encodes *shape only* — you cannot
   compare absolute energy across tracks. But "plan an energy arc *across a set*"
   is fundamentally a cross-track comparison. The feature as built can't support
   the feature it exists for.

## Decision

1. **Beats/downbeats/structure from a structure-aware detector.** Target
   **`allin1`** (All-In-One Music Structure Analyzer), which returns beats,
   **downbeats**, functional **segment labels**, and **tempo** in one pass —
   serving both `ADR 0004` (segmentation) and accurate BPM. Fallback if `allin1`
   won't install cleanly: `librosa` beat tracking with octave correction +
   `msaf` for boundaries + heuristic labels. **Verify installability on first
   run; keep the fallback wired.**
2. **Energy = cross-track-comparable loudness (LUFS).** Use `pyloudnorm` for
   **integrated** loudness per track (`tracks.loudness_lufs`) and **short-term**
   loudness per section (`sections.energy_lufs`). The arc is planned and scored
   in LUFS. Optionally keep a within-track normalized contour for display, but
   the *comparable* measure is what the Selector and the Energy-arc-RMSE eval use.

## Rationale

- **Downbeats are non-negotiable for phrase-aligned mixing.** Crossfading on the
  downbeat grid is the difference between a beatmatch and a train wreck. A
  detector that emits downbeats + segments + tempo together is strictly better
  than stitching three librosa calls.
- **LUFS is the standard, perceptual, cross-track loudness unit.** It's what
  mastering and broadcast use; it makes "warm-up at −18 LUFS, peak at −9" a
  meaningful, schedulable target. Per-track min-max never could.
- **One detector, two ADRs served.** `allin1` gives both the segmentation
  (`ADR 0004`) and the calibrated BPM here — less code, one source of truth for
  the beat grid that the Mixer also reuses.

## Trade-offs accepted

- **`allin1` install risk.** It pulls heavier deps (madmom/NATTEN-class) that can
  fight the torch/Python pins. Mitigation: the librosa + msaf fallback path, and
  a first-run verification step. This is the one dependency flagged as "confirm
  before relying on it."
- **Slower ingestion.** Segment + downbeat detection adds seconds per track on
  top of CLAP. It's an ingestion-time cost only (queries stay sub-ms), and the
  author already accepted the one-time ingest hit (`ADR 0002`).
- **`pyloudnorm` needs the decoded waveform** — already loaded for analysis, so
  near-free.

## Consequences

- Stack adds `allin1` (or `librosa`+`msaf` fallback) and `pyloudnorm`.
- `audio/analyze.py`: BPM/downbeats from the detector; replace per-track RMS
  normalization with integrated LUFS; expose the beat grid for the Mixer.
- `audio/segment.py` (new): consumes the detector's segment output (`ADR 0004`).
- `schema.sql`: `tracks.loudness_lufs REAL`, `sections.energy_lufs REAL`,
  `sections.start_beat INT`. The old `energy_mean`/`energy_curve` are replaced.
- Eval **Energy-arc RMSE** is computed in LUFS; **BPM continuity** uses
  downbeat-derived tempo.
