# Phases & Work Items

A sequenced breakdown of what's done, what's next, and what needs decisions.

---

## Phase 0 — Scaffold ✅ complete

Camelot wheel, project layout, config, test harness. Superseded in part by the
CLAP-first re-scope below.

---

## Phase 1 — Curator + Vibe DB (CLAP-first) ✅ code complete

**Goal:** Ingest a real audio folder into the vibe DB and run semantic
(text→audio) and seed (audio→audio) vibe queries that return sensible neighbors.

**What's built:**
- `config.py` — `VIBE_DIM = 512`, `CLAP_MODEL`, `CLAP_SAMPLE_RATE`
- `audio/analyze.py` — slimmed to structured mixing features (BPM, key→Camelot,
  energy arc, duration); no MFCC/spectral (CLAP covers timbre)
- `vibe/clap.py` — CLAP encoder: `embed_audio` (windowed + mean-pooled) and
  `embed_text`, both 512-d L2-normalized, MPS/CUDA/CPU auto-select
- `metadata.py` — `mutagen` tag reader (genre, artist, mood → normalized tags)
- `sources/` — `SourceProvider` protocol + `LocalFolderProvider`
- `vibe/schema.sql` — structured cols + metadata + `vector(512)` + indexes
- `vibe/store.py` — `upsert_track`, `nearest(filters)`, `nearest_to_text`
- `curator.py` — drives from a `SourceProvider`; analyze + CLAP + tags → upsert
- Tests: camelot, analyze, metadata (fast); clap (slow, `-m slow`)

**Remaining (needs your inputs / a live run):**
1. `DATABASE_URL` is set in `.env` — confirm the Supabase project is reachable.
2. Run `uv run python -m dj.curator <small-folder>` (20–50 tracks) — first run
   downloads CLAP weights (~1.5 GB) and populates the DB.
3. Smoke-test: `store.nearest_to_text("dreamy nocturnal", k=10)` → eyeball the
   neighbors (print title/genre/bpm/camelot/distance).

**Decisions needed:**
- First audio folder to ingest?
- Favorites: auto-detected via a `favorites/` path segment, or use
  `--favorites <folder>`. Good enough, or do you want a sidecar manifest?

---

## Phase 2 — Architect + Selector agents (Claude Agent SDK)

**Goal:** Vibe prompt → coherent tracklist whose BPM/energy follows the arc.

**Work items:**
1. `agents/tools.py` — MCP tools: `query_vibe_db` (wraps `nearest`/
   `nearest_to_text`), `get_track_features`, `check_harmonic_compat`
2. `agents/architect.py` — prompt → arc spec (JSON curve over set position)
3. `agents/selector.py` — arc spec → ordered tracklist (vibe rank + BPM/Camelot
   filters + artist spacing + favorites/discovery mix)
4. `agents/hitl.py` — present tracklist + arc → approve/nudge
5. `python -m dj.agents.generate "2-hr sunset rooftop, slow build"`

**Note:** `claude-agent-sdk` is confirmed = Anthropic Claude Agent SDK; install
with `uv sync --extra agents`.

**Decision:** Architect + Selector as two SDK loops (plan→execute) or one agent
with two tool groups?

---

## Phase 3 — Mixer (the wow)

**Goal:** Play the rendered mix — beatmatched transitions, not hard cuts.

**Work items:**
1. `audio/cuepoints.py` — outro/intro boundaries from onset/structure
2. `mixer.py` — time-stretch B to A's BPM (`pyrubberband`), phrase-align,
   crossfade + EQ swap, render to `DJ_OUTPUT_DIR`

`rubberband` CLI is confirmed installed.

**Decision:** crossfade length fixed (e.g. 8 bars) or computed from the detected
phrase grid?

---

## Phase 4 — Eval scorecard + Critic + HITL

**Work items:**
1. `evals/runner.py` — the six metrics from `BUILD_PLAN.md`, via agent-core's
   `EvalRunner`
2. `critic.py` — deterministic transition scoring (beat-alignment error,
   loudness/spectral discontinuity, key clash, tempo jump) → accept/retry
3. `--explain` flag: Architect narrates its arc choices
4. CI gate on BPM continuity / harmonic-compat regressions

---

## Phase 5 — Remote sources + Taste/Memory

**Work items:**
1. `sources/jamendo.py` (and/or FMA) — CC-licensed downloadable audio behind the
   existing `SourceProvider` protocol, so the agent never runs out of tracks
2. `memory.py` — store accepted transitions + skip events; feed back into
   Selector scoring

(CLAP is no longer a Phase 5 item — it's the Phase 1 foundation.)

---

## Lingering open questions

| Question | Who decides | Blocking |
|---|---|---|
| First audio folder to ingest | You | Phase 1 live run |
| Favorites strategy (folder vs manifest) | You | Phase 1 (current default works) |
| Rotate the leaked credentials | You | security (do now) |
| Architect + Selector: 1 agent or 2 | Architecture call | Phase 2 |
| Crossfade length: fixed vs phrase-derived | You | Phase 3 |
| CLAP variant if text prompts feel weak | You | tuning |
