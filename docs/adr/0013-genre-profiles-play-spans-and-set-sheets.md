# ADR 0013 — Genre profiles as data, entry→core→exit play spans, and the printable set sheet

**Status:** Accepted
**Date:** 2026-06-11
**Relates to:** `0004-structure-aware-sections.md`, `0006-one-planning-agent-and-verifier.md`,
`0007-phrase-derived-crossfades.md`, `0010-rekordbox-export-two-modes.md` (adds the third
export artifact + a third cue), `0011-grid-truth-downbeat-phase-and-anchors.md` (the mix
flags the play spans stand on)

## Context

Set generation treated every genre identically: one set of Critic thresholds
(max 6 BPM jump, 70% harmonic compat), a fixed 8-bar crossfade cap, unlimited
tempo stretching toward the arc, and each slot playing a **single section**
(~30–90 s) chosen by energy fit. But a house blend and a hip-hop cut are
different crafts: house/techno ride 16-bar EQ blends at ±8% tempo bend under
strict harmonic mixing; hip-hop/pop sets live on 1–2 bar cuts where key clashes
barely matter, vocals warble beyond ±3% stretch, and a track airs ~2.5 min;
latin nights span reggaeton/dembow/bachata tempo islands bridged by cuts. The
old constants graded every one of those rooms on house rules — and a
single-section slot meant the "set" was a sprint of 45-second snippets in any
genre. Meanwhile nothing carried the *transition plan* into the booth: the XML
puts cues on the deck (`ADR 0010`), but "blend 16 bars here, cut hard there,
watch the key clash at 7→8" lived only in my head.

## Decision

**Genre becomes a first-class, persisted input to set generation: one data
table of `GenreProfile`s that every layer reads the same way — Critic
thresholds, entry→core→exit play spans sized to the genre's airtime,
phrase-quantized and genre-capped transitions — and every approved set ships a
third artifact, a printable set sheet.**

Concretely:

1. **Genre profiles as data** (`src/dj/profiles.py`): a frozen `GenreProfile`
   dataclass (name, brief keywords, `bpm_range`, `lufs_range`, default arc
   shape, `max_bpm_jump`, `min_harmonic_compat`, `max_stretch`,
   `max_xfade_bars`, `min/max_play_s`, `avg_slot_minutes`) and a `PROFILES`
   table ordered most-specific-first: afro, techno, trance, house, dnb, hiphop,
   latin, pop, downtempo, plus an "open" `DEFAULT` identical to the old
   behavior. `detect(brief)` returns the first keyword hit ("afro house" lands
   on afro, not house); `get(name)` resolves the persisted name; `--genre` on
   generate forces one. Every layer reads the same answer: the **Architect**
   seeds arc ranges/shape, the **Selector/Critic** grade on
   `profile.thresholds()`, the **Selector** plans airtime inside
   `min/max_play_s`, the **Mixer** caps crossfade bars and clamps tempo
   stretch. `SetPlan` gained a persisted `genre` field so the Mixer reads the
   profile back at render time.
2. **Play spans instead of single sections** (`selector.plan_play_span`): a
   slot's playable span now runs entry→core→exit — the **core** is the arc's
   energy fit (as before), the **entry** a mix-in-flagged section at or before
   the core, the **exit** a mix-out-flagged section at or after it; the pair is
   chosen to best fill the genre's airtime window (longest within
   `[min_play_s, max_play_s]`, least-bad overflow otherwise; falls back to the
   core alone). `Slot` gained `mixin_label`, `mixout_label`, `core_start_s`;
   the slot's Critic-scored LUFS stays the **core's** energy (the arc moment);
   `store.get_sections` now returns the `is_mixin`/`is_mixout`/`loopable`
   flags (`ADR 0011`).
3. **Phrase-quantized, genre-capped transitions** (`mixer.py`): crossfade bar
   counts quantize **down** to powers of two (`quantize_phrase_bars`:
   1/2/4/8/16/32), capped by `profile.max_xfade_bars`; `clamp_target_bpm`
   bounds how far a track is bent toward the arc tempo (`profile.max_stretch`)
   in both `plan_transitions` and `render_set`. `Transition` gained a `bars`
   field for the set sheet.
4. **The set sheet** (`src/dj/export/setsheet.py`, third artifact alongside
   `rekordbox.xml` + `.m3u8` from `ADR 0010`): every approved set writes
   `<name>.setsheet.md` — a printable cue card: a track table (order, key,
   BPM, cue in/out as mm:ss, played span as `intro→drop→outro`),
   per-transition notes (bars + style: long blend / blend / short blend /
   quick cut, ~seconds at target BPM, which sections it exits/enters on), with
   Critic warnings — graded on the **genre's** thresholds — inline at the
   right transitions. The rekordbox export also gained a third hot cue: the
   core section's hit point (e.g. "DROP", Num 2) alongside MIX IN (entry
   label) and MIX OUT (exit label), each doubled as a memory cue.

## Rationale

- **"Correct" is genre-relative, so the bar has to be a parameter.** The same
  3-track set with a 7-BPM jump and one key clash *passes* as hip-hop and
  *fails* as house — that exact case is a unit test. One Thresholds default
  meant the Critic was silently grading every brief as a house set.
- **One table, many readers.** The Architect, Selector, Critic, Mixer, and set
  sheet all need the same genre answer; encoding it once as data means they
  can't drift apart. *Alternatives rejected:* keyword heuristics scattered per
  layer (the old `_bpm_range` in `architect.py` was already this, and it's how
  layers diverge), and an LLM judging genre per set (non-deterministic,
  untestable, unnecessary — substring matching on the brief is enough for a
  personal library, and `--genre` overrides it).
- **A slot is airtime, not a snapshot.** `ADR 0004` made sections selectable,
  but playing *only* the energy-fit section confused "the moment the arc
  targeted" with "everything the track plays". entry→core→exit keeps the
  arc's anchor (the core still carries the Critic-scored LUFS) while the slot
  airs minutes — and both render modes mix on flagged musical boundaries
  because the span's ends *are* mix-in/mix-out sections.
- **Blends live on phrases.** Dance music phrases in powers of two; a 7-bar
  blend ends mid-phrase and feels like a stumble even when every beat lined
  up. Quantizing down + capping per genre is two integers of policy, not DSP.
- **The set sheet is the plan's third renderer.** Same lesson as `ADR 0010`:
  the plan is the product, and `plan_transitions` + the Critic already knew
  the bars, target BPMs, and warnings — the sheet is pure string building over
  data that existed, finally put where a DJ looks mid-set (paper, not a DB row).

## Trade-offs accepted

- **Profile values are my priors, not learned.** Nine genres' worth of numbers
  in one file, set from craft knowledge and tunable — but unvalidated until I
  play them. Explicit-and-wrong beats implicit-and-wrong; the eval scorecard
  can now A/B them.
- **Detection is first-keyword-hit substring matching.** A brief that spans
  genres ("disco into techno") gets whichever keyword's profile comes first;
  no fuzzy matching, no per-slot genre. `--genre` is the escape hatch, and the
  open default means a miss degrades to exactly the old behavior.
- **`clamp_target_bpm` makes the arc advisory where genre physics forbids
  it.** A hip-hop track asked to bend 8% will only bend 3%, so the rendered
  set can sit off the arc's tempo curve at that seam. The Critic already
  flagged the jump at plan time; warbling the vocal would be worse.
- **Quantizing bars *down* is conservative** — a 15-bar opportunity becomes an
  8-bar blend. Acceptable: ending mid-phrase is the audible failure, leaving
  phrase headroom isn't.
- **The Critic scores the core's LUFS, not the span's average** — the arc-fit
  metric measures the moment that matters, but a long span's entry/exit can
  sit well below it. That's how DJs think (the drop is the energy statement),
  but it's a modeling choice, not a measurement.
- **A third artifact to keep coherent with the other two.** The sheet restates
  what the XML encodes; if cue semantics change, two writers change. Both are
  pure renderers over the same `SetPlan`, so the sync cost is one plan walk.

## Consequences

- New `src/dj/profiles.py` — pure data + string matching, no audio/DB/models;
  every magic mixing constant now has a name, a genre, and one home.
- **Old persisted plans load fine:** `genre` is `None` → `get(None)` returns
  the open profile, which *is* the old behavior. No migration.
- `Slot` carries `mixin_label`/`mixout_label`/`core_start_s` through
  persistence and both exports; `Transition.bars` feeds the sheet;
  `store.get_sections` surfaces the `ADR 0011` mix flags to the Selector.
- Every approval now writes **three** files (`rekordbox.xml`, `.m3u8`,
  `.setsheet.md`), and the XML carries up to three cue pairs per track
  (MIX IN / MIX OUT / core hit, hot + memory).
- 29 new unit tests (256 total, still offline): profile detection/resolution,
  the hip-hop-passes/house-fails case, span selection, phrase quantization,
  stretch clamping, and sheet rendering by string inspection.
- The eval scorecard can A/B profiles — "same brief, hip-hop vs open rules" is
  now a one-flag experiment.

---

## ELI5 / what I learned

The biggest shift was realizing "is this a good transition?" has no
genre-free answer. A 7-BPM jump with a key clash is a failed house set and a
perfectly normal hip-hop set — so correctness can't be a constant, it has to
be a parameter. And once something is a parameter, the question becomes *where
it lives*: I'd already started growing genre if-statements inside one layer,
which is how the Architect and the Critic end up believing different things
about the same set. Putting all of it in one data table that every layer reads
means the system can't disagree with itself, and I can test taste like logic —
"this exact set passes as hip-hop and fails as house" is literally a unit
test now. The second lesson: I'd confused a *moment* with a *performance*. The
arc picks the moment (the drop the room needs at minute 40), but a slot is the
whole guest spot — you enter clean, ride to the moment, exit clean — so the
span got three points where there used to be one. And the smallest decision
taught me the most: blends quantize to powers of two because music breathes in
phrases — a 7-bar blend is wrong even when every individual beat lines up,
which is a neat reminder that locally correct and structurally correct are
different things. The set sheet is just ADR 0010's lesson again: the plan is
the product, and a piece of paper taped to the mixer is a render target too.
