# Phases & Work Items

A sequenced breakdown of what's done, what's next, and what needs decisions.
**Re-sequenced (2026-06-03):** the taste loop moved from Phase 5 to Phase 2 — a
personal DJ that doesn't know my taste is just a worse Spotify. See the
decisions in `docs/adr/0003`–`0005`.

---

## Phase 0 — Scaffold ✅ complete

Camelot wheel, project layout, config, test harness.

---

## Phase 1 — Curator + Vibe DB ✅ (revision code landed 2026-06-04)

**Goal:** Ingest a real audio folder into the vibe DB — tracks *and* sections —
and run semantic (text→audio), seed (audio→audio), and section-level vibe
queries that return sensible neighbors.

**Built + unit-tested (no DB, no model download):**
1. ✅ `audio/segment.py` — `segment(path) → Structure` (bpm, beats, downbeats,
   sections). `allin1` target → `librosa` fallback (octave-corrected beats +
   agglomerative boundaries, **no msaf dependency**) → single-section last resort.
   Pure heuristics (`label_sections`, `assign_mix_flags`, `octave_correct`,
   `build_sections`) tested in `test_segment.py`. `ADR 0004/0005`.
2. ✅ `audio/analyze.py` — BPM passed through from the detector (downbeat-derived);
   per-track RMS replaced with **integrated LUFS** (`pyloudnorm`, RMS-dBFS
   fallback); `measure_sections()` gives per-section short-term LUFS. A normalized
   `energy_curve` is kept for *display* only.
3. ✅ `vibe/clap.py` — `embed_track_and_sections(path, bounds)` (one audio load →
   track vector + one vector per section); `embed_sections` wraps it.
4. ✅ `vibe/schema.sql` — **schema v2**: `tracks` (acoustic vec + taste cols +
   `loudness_lufs`) + `sections` (per-section vec + bounds + beat + mix flags),
   self-migrating ALTERs. `docs/database.md`.
5. ✅ `vibe/store.py` — `upsert_track` writes track + sections in one idempotent
   txn; added `nearest_section`, `get_sections`, `get_cards` (alongside the
   existing `nearest_taste` / `ranked`).
6. ✅ `curator.py` — per track: segment → analyze + embed track + embed sections +
   per-section LUFS → upsert. Stays idempotent.

**Remaining (needs the live DB + a `uv sync`):** the actual ingest run — confirm
`allin1` installs against the torch pins (else the fallback runs automatically),
then ingest 20–50 tracks. *(George's morning task, shared with Phase 2.)*

**Verify:** ingest 20–50 tracks → `nearest_to_text("dreamy nocturnal")` returns
sensible *tracks*, and `nearest_section(...)` returns sensible *parts*; print
title/section/BPM/Camelot/LUFS/distance.

---

## Phase 2 — Taste loop 🎯 (in progress)

**Goal:** After tagging ~50 favorites, "more like the ones I love" returns
neighbors that match my taste (not just acoustics), and the active-learning queue
surfaces sensible next labels.

**Decision (resolved):** `vibe-tagging` is a **plain CLI first**; wrap it as a
Claude skill once the flow feels right.

**Built 2026-06-04 (unit-tested without a DB or model download):**
- ✅ `config.py` — `TASTE_DIM = 384`, `TASTE_MODEL`, default α/β/γ (`TASTE_WEIGHTS`).
- ✅ `vibe/schema.sql` — taste columns added **additively** to `tracks`
  (`taste_note`, `taste_vec vector(384)`, `taste_source`, `rating`, `role`) + HNSW
  index. Won't break the current ingest; they sit NULL until tagged.
- ✅ `vibe/store.py` — `get_track`, `set_taste`, `set_propagated_taste`,
  `tagged_corpus`, `untagged`, `nearest_taste`, `ranked` (blended).
- ✅ `taste/score.py` — blended `score = α·acoustic + β·(confidence·taste) +
  γ·rating`; manual > propagated; cold-start falls back to acoustic. (`test_score.py`)
- ✅ `taste/propagate.py` — provisional `taste_vec` from tagged CLAP-neighbors +
  active-learning `uncertainty` / `labeling_queue`. (`test_propagate.py`)
- ✅ `taste/embed.py` — note → 384-d `taste_vec` (lazy `sentence-transformers`).
  (`test_taste_embed.py`, slow)
- ✅ `taste/tag.py` — the **vibe-tagging CLI** (`python -m dj.taste.tag`).
- ✅ **Parked-review capture (`ADR 0008`)** — capture taste *before* I own the
  file. `taste/pending.py` (confident-only ISRC→name+duration matcher, pure +
  unit-tested), `taste/review.py` CLI (manual entry + `--pending` want-list +
  `--apply` resolve), `metadata.isrc`, Curator drains parked reviews at ingest,
  `agents.tools.save_review`/`list_pending_reviews`, `vibe-review` skill (Spotify
  now-playing + chat). New `pending_taste` table. (`tests/test_pending.py`)

**Remaining (needs the live DB + a `uv sync`):**
1. **Ingest my real library** (a few hundred tracks) — also gut-checks CLAP and
   calibrates how heavy the taste layer must be. *(morning task)*
2. `uv sync` to install `sentence-transformers` (first note embed downloads ~90 MB).
3. Tag ~50 favorites via the CLI → `python -m dj.taste.tag --propagate` →
   smoke-test `store.ranked(...)` and `python -m dj.taste.tag --queue`.

See `docs/taste.md`.

---

## Phase 3 — Architect + Selector agents ✅ (code landed 2026-06-04)

**Goal:** Vibe prompt → coherent tracklist (with chosen sections + cue points)
whose BPM/energy follow the arc and whose taste-match beats a CLAP-only baseline.

**Decision (resolved, `ADR 0006`):** **one planning flow with the arc as an
explicit artifact** + a **deterministic Critic** as the Selector's verifier; built
on agent-core `complete()` behind an injectable model seam (a Claude Agent SDK
MCP server can wrap `agents/tools.py` later). Deterministic fallbacks run offline.

**Built + unit-tested (no DB, model, or audio):**
1. ✅ `arc.py` — the `Arc` artifact: control points + interpolation + named shapes
   (`build`/`peak`/`wave`/`down`/`flat`) + `shape_from_brief`. (`test_arc.py`)
2. ✅ `plan.py` — `Slot` / `SetPlan` (the HITL-approved data artifact).
3. ✅ `agents/tools.py` — the toolbelt: `query_vibe_db` (blended), `get_sections`,
   `check_harmonic_compat`, `score_transition` (the future MCP-server surface).
4. ✅ `agents/architect.py` — brief → arc (LLM JSON + deterministic fallback).
   (`test_architect.py`)
5. ✅ `agents/selector.py` — **generate → verify → revise**: blended pool → LLM
   order → Critic verify → revise; `greedy_select` baseline; `pick_section`
   assigns the right *part* per slot; `parse_selection`. (`test_selector.py`)
6. ✅ `critic.py` — deterministic verifier + eval core: Camelot, BPM jumps,
   energy-arc RMSE, artist spacing → `SetReport`. (`test_critic.py`)
7. ✅ `agents/hitl.py` — `render_plan` (tracks + sections + arc fit + flagged
   transitions) + `confirm` gated on `HITL_LEVEL`. (`test_hitl.py`)
8. ✅ `python -m dj.agents.generate "<brief>" [--offline] [--render]`.

**Remaining (needs the live DB):** a real generation run on the ingested library
to tune the greedy cost weights + Critic thresholds and confirm taste-match beats
a CLAP-only baseline. See `docs/set-generation.md`.

---

## Phase 4 — Mixer ✅ (code landed 2026-06-04)

**Goal:** Play the rendered mix — phrase-aligned, beatmatched transitions using
the *parts of each track* the Selector chose.

**Decision (resolved, `ADR 0007`):** crossfade length is **phrase-derived** —
half the outgoing section's bars, capped at 8 — not a fixed wall-clock time.

**Built + unit-tested (pure planning; audio render is lazy/guarded):**
1. ✅ `mixer.py` — `plan_transitions` cues on **section boundaries**
   (downbeat-anchored), `crossfade_bars`/`crossfade_seconds`/`section_bars`/
   `stretch_ratio` (pure, `test_mixer.py`); `render_set` time-stretches to the arc
   tempo (`pyrubberband`), equal-power crossfades with an optional `scipy` bass/EQ
   swap, peak-normalizes, writes to `DJ_OUTPUT_DIR`. Degrades gracefully when the
   stretch/EQ deps are absent.

**Remaining (needs real audio + the `mixer` extra):** render an approved set and
listen — confirm transitions land on the phrase and beatmatch.

---

## Phase 5 — Eval scorecard + Critic + demo 🎯 (core code landed 2026-06-04)

**Built + unit-tested (no DB/model/audio):**
1. ✅ `evals/runner.py` — the scorecard: **taste-match** (set vs my labeled
   favorites' centroid) + **discovery ratio** layered on the Critic's BPM
   continuity / harmonic-compat / **energy-arc RMSE (LUFS)** / artist spacing; plus
   `compare_to_acoustic_baseline()` and a CLI (`python -m dj.evals.runner "<brief>"`)
   that A/Bs the blended Selector vs a CLAP-only baseline (the Phase 3 verify goal).
   Pure metric core unit-tested; the live A/B needs the ingested library. *(There is
   no `agent-core EvalRunner` — the scorecard is a standalone dj-agent module reusing
   the Critic.)*
2. ✅ `critic.py` — already the Selector's verifier since Phase 3; this pass added
   windowed **artist spacing** + **key-monotony** signals to its report.
3. ✅ `agents/explain.py` — `--explain` narrates the arc, key moves (incl.
   energy-boost lifts), and the section choices, deterministically (no model needed).
4. ✅ `persist.py` — every generated set is logged to `renders/set_history.jsonl`
   (the **set-acceptance** record) and feeds recently-played dedup.

**Remaining:**
- Audio-level **transition smoothness** (spectral/loudness discontinuity at the
  *actual* mix point) — a post-render add-on once A1 (beat-lock) is validated.
- The **set-acceptance** rate + the blind A/B listen test — accumulate as you
  approve/reject real sets (the log is now there to compute them from).
- CI gate on BPM-continuity / harmonic-compat regressions.

---

## Phase 6 — Memory (learned taste) + sharing

**Work items:**
1. `memory.py` — store accepted transitions + skip/replay events; refine the
   taste vectors / scoring on top of the labeled warm start.
2. Spotify-playlist export of approved tracklists (Spotify MCP) to share with
   friends.

---

## 2026-06-04 review pass — fixes + additions landed

A multi-agent review (7 dimensions, adversarially verified) ran over the whole
codebase. Everything safe + offline-verifiable was auto-applied; everything needing
your decision/ears went to `docs/backlog.md`. Auto-applied:

- **Correctness bugs:** the played **section's** LUFS now drives arc scoring (not
  the whole-track average); custom blend weights actually reach the card score;
  octave-correction no longer folds DnB/footwork to half-tempo; a too-short first
  section now merges; the `bridge` label is reachable; per-section energy uses
  **ungated short-term** LUFS; the LUFS short-signal guard matches pyloudnorm's
  400 ms block; the Architect clamps arc positions to 0..1; CLAP guards empty audio;
  parked reviews dedupe on re-capture; propagation persists a **real** confidence.
- **Quality additions:** Phase 5 eval scorecard + `--explain` + plan persistence +
  recently-played dedup (above); windowed **artist spacing** + **key-monotony**
  signals; richer Camelot primitives (`energy_boost`, `grade`, smoother `distance`)
  + a harmonic-smoothness tiebreak in the greedy; **track count derived from
  minutes**; robust mix normalization (one transient can't crush the set);
  estimated set length shown at the HITL gate.
- **Doc drift:** removed dead Jamendo/CC vars, corrected "Claude Agent SDK" →
  agent-core, fixed the msaf-fallback / `pydub` / `sections.energy_lufs` claims,
  documented the rubberband CLI + pgvector-extension setup, test count 79 → 103.

103 fast tests green; `ruff` clean.

## Lingering open questions

| Question | Who decides | Blocking |
|---|---|---|
| First audio folder to ingest | You | Phase 1/2 live run (`docs/your-todo.md`) |
| `allin1` installs cleanly, or use fallback | Verify on first run (fallback auto-runs) | Phase 1 |
| Default α/β/γ blend weights | Tune on real data | Phase 2 |
| Greedy cost weights + Critic thresholds | Tune on real data | Phase 3 |
| Spotify MCP `get_currently_playing` exposes ISRC (else name+duration fallback) | Verify on first capture | Parked-review capture (ADR 0008) |
| **Mixer beat-phase-lock + seam tempo** (needs your ears) | You + me on real audio | `docs/backlog.md` A1 |
| **Extended (energy-boost) harmonic gate** — how adventurous? | You (taste) | `docs/backlog.md` C1 |
| **Multi-user "we" taste** — when + how tastes combine | You (design) | `docs/backlog.md` C2 |
| **Spotify playlist export** — authorize publishing | You | `docs/backlog.md` B1 |
| ~~`vibe-tagging`: skill now vs CLI-first~~ | ✅ resolved: CLI-first | — |
| ~~Architect + Selector: 1 agent or 2~~ | ✅ resolved: one flow + arc artifact (`ADR 0006`) | — |
| ~~Crossfade length: fixed vs phrase-derived~~ | ✅ resolved: phrase-derived (`ADR 0007`) | — |
| Rotate any leaked credentials | You | security (`.env` is gitignored + untracked) |
| CLAP / taste model variants if quality feels weak | You | tuning |
