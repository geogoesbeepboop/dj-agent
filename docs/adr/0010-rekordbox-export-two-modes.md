# ADR 0010 — Every approved set ships in two playable forms (rekordbox export)

**Status:** Accepted
**Date:** 2026-06-10
**Relates to:** `0004-structure-aware-sections.md`, `0006-one-planning-agent-and-verifier.md`, `0007-phrase-derived-crossfades.md`

## Context

An approved `SetPlan` used to have exactly one playable form: the Phase 4 Mixer's
rendered WAV (`--render`). That gated all of the planning stack's value behind the
one component I trust *least* right now — the Mixer still doesn't sample-accurately
phase-lock downbeats at seams (`docs/backlog.md` A1) and hasn't been validated by
ear. Meanwhile the plan itself is already the hard part done: the Architect/Selector/
Critic loop (`ADR 0006`) produces track order, Camelot keys, downbeat-derived BPMs,
and per-slot section cue points (`ADR 0004`) — everything a human DJ needs to
perform the set on real decks. Without an export, that plan was a JSON log and a
printout; I'd be re-typing cue points into rekordbox by hand, which is exactly the
kind of friction that kills a loop (same lesson as `ADR 0008`).

The forces: (1) I want to *play* these sets, on gear DJs actually use, before the
DSP is proven; (2) the section bounds the Selector chose are already the mix
points — they just live in my DB instead of on the deck; (3) rekordbox XML is the
de-facto interchange format that rekordbox (and via it, CDJs/USB export)
understands, and it carries collection metadata, cue marks, and playlist order in
one file.

## Decision

**Every approved `SetPlan` ships in two playable forms: *manual* — a
`rekordbox.xml` + `.m3u8` where I perform the transitions but the order, keys,
BPMs, and MIX IN / MIX OUT cues are pre-set — and *automatic* — the Phase 4
rendered mix; the manual form is always written, never gated behind a flag.**

Concretely (`dj/export/rekordbox.py`, pure stdlib; wired in
`dj/agents/generate.py`):

- `write_rekordbox_xml(plan, out, durations=, playlist_name=)` emits a
  `DJ_PLAYLISTS` 1.0.0 document: a `COLLECTION` deduped by file path (a track
  filling several slots appears once; the **first slot wins** for cue data) with
  Name / Artist / `file://localhost`-style Location / AverageBpm / Tonality
  (`camelot.key_name`: `'8A'` → `'Am'`) / TotalTime, plus a `PLAYLISTS` node
  named **"dj-agent — \<arc name\>"** listing every slot in set order (repeats
  allowed).
- The slot's section cue points (`ADR 0004`) become `POSITION_MARK`s: **MIX IN**
  (hot cue 0) at `cue_start_s` and **MIX OUT** (hot cue 1) at `cue_end_s`, each
  duplicated as a memory cue (`Num=-1`) so they survive on players/configs
  without hot-cue access. The mark names carry the section label
  ("MIX OUT — outro").
- `write_m3u8(plan, out)` is the lowest-common-denominator fallback: set order +
  durations, no cues, readable by anything.
- `agents/generate.py` exports **both** files to `DJ_OUTPUT_DIR` on every
  approved set (`--rekordbox <path>` overrides the XML location) and prints the
  import steps (Preferences → Advanced → Database → rekordbox xml). `--render`
  remains automatic mode. New `store.track_durations()` feeds TotalTime with
  real file lengths.

## Rationale

- **Decouples plan validation from DSP validation.** I can judge the Selector's
  choices — order, key path, energy arc, *and* whether "mix out of the outro at
  4:12" actually works — by performing the transitions myself, before trusting
  the Mixer's renders. The planning artifact is useful *today*, with backlog A1
  still open.
- **The section data was already the answer.** `ADR 0004` stored section bounds
  as "the cue points the Mixer needs"; this is the same data with a second
  consumer. MIX IN/OUT markers on the deck are the human-facing form of
  `cue_start_s`/`cue_end_s` — zero new analysis, the whole pipeline's value
  lands in a tool DJs already use.
- **rekordbox XML is the right wire format.** It's the one import path
  rekordbox exposes for third-party libraries, and one file carries collection
  + metadata + cues + playlist, so the set arrives ready to play rather than as
  a list to reconstruct.
- **Always-write, no flag.** Two small text files cost milliseconds and nothing
  external (no DB writes, no network, no audio decode at export time — durations
  come from columns). Gating cheap artifacts behind a flag just creates a way to
  forget them.
- **Hot + memory cue duplication is deliberate redundancy** for the two ways
  players surface cues; writing both is free in the XML.

## Trade-offs accepted

- **No beat grid is exported.** The XML carries **no `TEMPO` element at all** —
  rekordbox *trusts* an imported grid, and we don't know each track's
  first-downbeat offset, so a grid anchored at `0.000` would be confidently
  wrong on every track. Omitting it makes rekordbox analyze the grid itself;
  `AverageBpm` and the second-based cue marks carry regardless. Ingest *does*
  know the downbeats (`ADR 0005`), so exporting a real anchor is the natural
  refinement (backlog **E2**), not built today.
- **Cue placement depends on TotalTime.** rekordbox scales marker positions
  against the declared track length, so the fallback chain (real duration →
  `cue_end_s` → 0) matters; `store.track_durations()` supplies real values for
  ingested tracks, but a missing duration means approximate markers.
- **One cue pair per track, not per slot.** The COLLECTION entry is per file, so
  if a track fills two slots only the first slot's cues are written. Acceptable:
  repeats in one set are rare and the playlist order is still correct.
- **My BPM/key vs rekordbox's.** I write *my* downbeat-derived BPM and Camelot
  key; rekordbox's own analysis may disagree on import. That's a feature for
  auditing my analysis, but it can look like a conflict in the UI.
- **Validated by parsing, not by a booth.** The XML is unit-tested by parsing it
  back (fast suite, 197 tests, offline); a real rekordbox import and a played
  set are live edges still on me. `write_m3u8` hedges against any dialect
  quirk I haven't hit yet.
- **I now maintain a niche XML dialect** that AlphaTheta could deprecate. The
  surface is small (one writer, pure stdlib) and m3u8 is the escape hatch.

## Consequences

- New `dj/export/rekordbox.py` — pure file-building (ElementTree + string
  formatting), no DB/model/audio/network, fully testable offline.
- `dj/agents/generate.py`: approval now always produces three things — the
  persisted plan record (`persist.save_plan`, Phase 5), the manual-mode pair
  (`rekordbox.xml` + `.m3u8`), and optionally the automatic render. Not
  approved → nothing exported.
- Supporting additions: `camelot.key_name(code)` (Tonality),
  `store.track_durations(paths)` (TotalTime/EXTINF), `settings.output_dir`
  already in place.
- The honest framing of Phase 4 improves: instead of "the mix is the product
  and it's unvalidated", the product is the **plan**, with two renderers — a
  human (validated since the dawn of DJing) and the Mixer (backlog A1).

---

## ELI5 / what I learned

The agent's real product isn't an audio file — it's a *decision*: these tracks,
in this order, mixing out of *this part* into *that part*. A rendered mix is one
way to perform that decision; a rekordbox playlist with cue markers is another,
and it's the one that works today because it borrows a human's hands and ears
instead of my still-unvalidated DSP. The part that surprised me: I didn't need
any new analysis to do this. The section boundaries I stored so the *Mixer*
could cut on phrases turn out to be exactly the markers a DJ wants on the deck —
same rows, second consumer. And the meta-lesson is about meeting tools where
they are: exporting into the format the incumbent already imports is how a
personal project's output ends up on real decks, instead of staying a JSON file
only my own code can play.
