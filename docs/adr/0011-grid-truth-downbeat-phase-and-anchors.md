# ADR 0011 — Grid truth: downbeat phase, grid-snapped bounds, and exported beat-grid anchors

**Status:** Accepted
**Date:** 2026-06-10
**Relates to:** `0004-structure-aware-sections.md`, `0005-calibrated-beats-and-energy.md`,
`0007-phrase-derived-crossfades.md`, `0010-rekordbox-export-two-modes.md` (refines its
"no TEMPO element" consequence; closes backlog **E2**)

## Context

Everything downstream of segmentation — MIX IN/OUT cue marks in the rekordbox
XML (`ADR 0010`), the Mixer's splice offsets (`ADR 0007`) — is expressed as
coordinates on the downbeat grid. Three things made that grid quietly untrustworthy:

1. **The librosa fallback guessed downbeat *phase*.** `audio/segment.py` took
   every 4th beat from `beat_track` as the downbeats — right tempo, but nothing
   ensured the chosen beat was actually the bar's "1". When it picked beat 3,
   every cue point landed a beat or two off the true bar start.
2. **Section labels were pure energy + position heuristics.** First section =
   "intro", last = "outro", always. A track that opens cold on the hook got its
   loudest bar labeled as a clean mix-in point.
3. **The export deliberately omitted `TEMPO`** (`ADR 0010`'s trade-off): we never
   stored each track's first-downbeat offset, so a grid anchored at `0.000`
   would have been confidently wrong, and rekordbox *trusts* an imported grid.

Net effect: sets could start on the wrong part of a song, or a beat or two late —
exactly the unpolished feel the whole pipeline exists to avoid. The real fix is
`allin1` (the `ADR 0005` target detector), but it remains a heavy, uncertain
install — and the fallback runs *whenever* allin1 isn't installed or fails, so
the fallback couldn't stay naive.

## Decision

**Estimate the downbeat *phase* from musical accents in the librosa fallback,
snap every section boundary to the downbeat grid in both detector paths, and
export a real rekordbox beat-grid anchor (`tracks.first_downbeat_s`) whenever —
and only when — the grid is actually known.**

Concretely:

1. **Downbeat phase estimation** (`audio/segment.py`, pure functions):
   `beat_accents` scores each beat's musical accent — normalized sum of onset
   strength at the beat, <150 Hz bass energy at the beat, and harmonic change
   *into* the beat (chroma distance between adjacent inter-beat spans).
   `estimate_downbeat_phase` averages accent per candidate phase 0..3 and takes
   the argmax; `beats_to_downbeats(beats, beats_per_bar, phase)` now takes that
   phase instead of assuming 0.
2. **Grid-snapped section bounds, both paths:** `snap_bounds_to_downbeats`
   quantizes interior boundaries to the nearest downbeat within a 2-beat
   tolerance (track edges stay; edges that collapse merge their sections) for
   the librosa path; `snap_labeled_segments_to_grid` does the same for allin1's
   labeled segments (monotonic — a snapped start clamps to the previous snapped
   end, so sections can never overlap; collapsed segments are absorbed by their
   neighbors). Labels also
   got honester: `section_recurrence` (max non-adjacent cosine similarity of
   per-section mean chroma+MFCC features) feeds `label_sections` so repeating
   material reads as "chorus" even at mid energy; a first/last section at
   near-peak normalized energy is labeled a cold open / hot ending ("chorus")
   instead of intro/outro (only when ≥4 sections — min-max normalization
   degenerates below that); and `assign_mix_flags` guarantees ≥1 mix-in and
   ≥1 mix-out per track (first-in/last-out fallback, the way a DJ would treat
   such a record anyway).
3. **Real beat-grid anchors in the export:** new nullable column
   `tracks.first_downbeat_s` (additive migration in `schema.sql`); the Curator
   stores `structure.downbeats[0]` only when `bpm > 0` (the single-section
   fallback reports `downbeats=[0.0]` with bpm 0 — a `0.000` anchor on an
   unknown grid stays the confidently-wrong case). The value threads
   `store.get_cards` → `TrackCard` → `Slot.first_downbeat_s` →
   `write_rekordbox_xml`, which emits
   `<TEMPO Inizio Bpm Metro="4/4" Battito="1">` only when the anchor is known
   **and** `bpm > 0`; otherwise `TEMPO` is still omitted and rekordbox analyzes,
   as before. `.claude/agents/rekordbox-validator.md` checks the new invariant.

## Rationale

- **In 4/4 dance music, the "1" announces itself.** The bar start carries the
  kick, the bass re-entry, and the chord change — exactly the three accent
  features `beat_accents` sums. Voting across only four candidate phases is a
  small, well-posed problem; the fallback didn't need a model, it needed to
  stop assuming phase 0.
- **Section bounds *are* the cue points.** Everything that consumes a boundary
  (`POSITION_MARK`s, mixer splices) treats it as a bar-1; snapping at the source
  puts every downstream cue exactly on one, in both detector paths, instead of
  fixing it per consumer.
- **The no-TEMPO objection expired.** `ADR 0010` omitted the grid because we
  couldn't *place* it, and itself flagged a real anchor as the natural
  refinement (backlog E2). We now store the anchor, so the same safety property
  survives as a gate (known anchor + `bpm > 0`) rather than a blanket omission.
- **Alternatives considered:** *madmom's `DBNDownBeatTracker`* (the
  academic-grade downbeat tracker) — rejected: heavy dependency with
  numpy-compat install pain, and `allin1` is already the designated real-model
  target; the fallback stays librosa-only, the same call `ADR 0005` made when
  it dropped msaf. *Just waiting for allin1* — rejected: the fallback runs
  whenever allin1 is absent, and shipping phase-shifted grids in the meantime
  makes every set sound amateur. *Keeping no-TEMPO forever* — rejected: the
  original objection (an anchor we can't place) no longer holds.

## Trade-offs accepted

- **Phase estimation is a heuristic.** A genre with consistently off-beat bass
  accents could fool the vote; the accent weights are tunable constants.
  Validation is by ear and by the rekordbox waveform, not by a labeled dataset.
- **Snapping moves boundaries by up to 2 beats** from where the energy/novelty
  signal put them. That's the point — but it means the stored bounds are
  grid-true rather than signal-true.
- **The anchor gate trades coverage for safety:** tracks analyzed by the
  single-section fallback still get no `TEMPO` and rely on rekordbox's own
  analysis. Correct by design, but it means the export's grid quality is uneven
  across the library until everything has a real structure pass.

## Consequences

- Old DB rows need a **re-ingest** to pick up `first_downbeat_s` and snapped
  section bounds (the migration is additive — no data loss, just NULL anchors
  until re-ingested).
- **Live verification still on George:** confirm on a real rekordbox import
  that a supplied `TEMPO` anchor wins over rekordbox's re-analysis
  (`docs/your-todo.md` has the step).
- **Backlog A1 is *not* solved.** Both sides of a splice now start on a true
  "1", but sample-accurate phase-lock through the crossfade (and seam tempo
  match) remains real DSP work in the renderer.
- 30 new fast tests cover the pure functions (`beat_accents`,
  `estimate_downbeat_phase`, `snap_bounds_to_downbeats`, `snap_time_to_grid`,
  `snap_labeled_segments_to_grid`, `section_recurrence`, label/flag logic, the
  TEMPO gate) — 227 total, still offline.

---

## ELI5 / what I learned

The beat grid is the coordinate system; every cue point and splice is just a
coordinate in it. So if the origin is off by one beat, every "perfect" cue is
wrong by exactly that beat — the errors don't average out, they all inherit the
same offset. I also learned the difference between knowing a clock's tick rate
and knowing which tick is noon: librosa's beat tracker gave us the tempo
(rate) but we were guessing the phase (which beat is the "1"). The fix worked
because in dance music the "1" announces itself three ways at once — the kick
hits, the bass comes back, the chord changes — so you can just score all four
candidate phases and let them vote. And the export lesson: ADR 0010's "no
TEMPO" rule was never "don't export a grid", it was "don't export a grid you
can't place." Once we stored the one number that places it, the same safety
rule flips from blocking the feature to gating it — which is what a good
constraint should do when the facts change.
