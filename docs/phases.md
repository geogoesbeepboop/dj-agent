# Phases & Work Items

A sequenced breakdown of what's done, what's next, and what needs decisions
from you before the next session.

---

## Phase 0 — Scaffold ✅ complete

**What exists:**
- `src/dj/config.py` — settings dataclass, env loading, `VIBE_DIM = 28`
- `src/dj/audio/analyze.py` — `TrackFeatures`, librosa-based `analyze()`, unit-testable `_features_from_signal()`
- `src/dj/audio/camelot.py` — full Camelot wheel, `compatible()`, `distance()`
- `src/dj/vibe/embed.py` — 28-d engineered vibe vector, L2-normalized
- `src/dj/vibe/store.py` — pgvector upsert + cosine KNN (`nearest()`)
- `src/dj/vibe/schema.sql` — tracks table + HNSW index DDL
- `src/dj/curator.py` — folder ingestion pipeline
- `tests/` — 9 passing tests (camelot, embed, analyze)
- `pyproject.toml`, `.env.example`, `.gitignore`
- `docs/` — architecture, database, embeddings, why-vibe-vectors, phases (this file)

**Verify:** `uv run pytest -q` → 9 passed

---

## Phase 1 — Curator + Vibe DB live (the "gold mine")

**Goal:** Point the Curator at a real audio folder, ingest it, and run a vibe
query that returns sensible neighbors.

**What's already done:** All code is in place. Needs a real database + audio.

**Remaining work:**
1. Set up Supabase project and add `DATABASE_URL` to `.env`
2. `store.ensure_schema()` (runs automatically on first Curator run)
3. Run `uv run python -m dj.curator ~/Music/your-folder`
4. Run a sanity-check query: `store.nearest(embed(analyze("some-track.mp3")), k=10)`
5. Print BPM/key alongside distances to verify the neighbors make sense

**Decisions needed from you:**
- Do you have a Supabase project? If yes, paste the `DATABASE_URL`. If not,
  we create one (free tier is fine for this scale).
- Which audio folder do you want to ingest first? A small folder (20-50 tracks)
  is a good first test before pointing at a full library.
- How do you want to mark favorites? (see `database.md` for options)

---

## Phase 2 — Architect + Selector agents (Claude Agent SDK)

**Goal:** Vibe prompt → coherent tracklist whose BPM/energy follows the
requested arc. No audio rendered yet.

**Work items:**
1. `src/dj/agents/tools.py` — define MCP tools: `query_vibe_db`, `get_track_features`, `check_harmonic_compat`
2. `src/dj/agents/architect.py` — Claude SDK agent: vibe prompt → arc spec (JSON)
3. `src/dj/agents/selector.py` — Claude SDK agent: arc spec → ordered tracklist
4. `src/dj/agents/hitl.py` — present tracklist + arc → stdin approve/nudge
5. Wire up `uv run python -m dj.agents.generate "2-hr sunset rooftop, slow build"`
6. Add Phase 2 tests: arc JSON validates, tracklist BPM follows arc direction

**Decisions needed from you:**
- `claude-agent-sdk` is listed in `pyproject.toml` optional extras but not
  installed yet. Is `claude-agent-sdk` the package name on PyPI, or is this a
  private/beta SDK? (Need to confirm before `uv sync --extra agents`.)
- Should the Architect and Selector be separate agents (two SDK loops) or one
  agent with two tool groups? Two agents mirrors the "plan then execute" pattern
  but adds latency.

---

## Phase 3 — Mixer (the wow)

**Goal:** Play the rendered mix — transitions are beatmatched, not hard cuts.

**Work items:**
1. `src/dj/audio/cuepoints.py` — detect outro/intro boundaries from librosa onset/structure
2. `src/dj/mixer.py` — time-stretch B to A's BPM via pyrubberband, phrase-align, crossfade + EQ swap
3. Render to `DJ_OUTPUT_DIR` (`.wav` first, then optional MP3 via pydub)

**Decisions needed from you:**
- `pyrubberband` requires the `rubberband-cli` system binary. On macOS:
  `brew install rubberband`. Can you confirm that's available or installable?
- Crossfade length: 8 bars is a standard DJ convention but depends on BPM and
  track structure. Do you want this fixed, or computed from the detected phrase
  grid?

---

## Phase 4 — Eval scorecard + demo

**Goal:** Set quality in CI; A/B vs naive playlist; agent explains its arc.

**Work items:**
1. `evals/runner.py` — implement the six metrics from `BUILD_PLAN.md`
2. Integrate with agent-core's `EvalRunner`
3. Add a `--explain` flag: Architect narrates its arc choices in plain English
4. CI job that fails if BPM continuity or harmonic-compat % regresses

---

## Phase 5 — CLAP + Taste/Memory

**Goal:** Text→audio search ("find me something dreamy and nocturnal");
learn from skips/replays.

**Work items:**
1. `src/dj/vibe/clap_embed.py` — CLAP inference, same interface as `embed.py`
2. Schema migration: `vector(28)` → `vector(512)`; re-ingest library
3. Update `VIBE_DIM = 512` in `config.py`
4. `src/dj/memory.py` — store accepted transitions + skip events; feed back into Selector scoring

---

## Lingering open questions (not phase-specific)

| Question | Who decides | Blocking? |
|---|---|---|
| Supabase project / DATABASE_URL | You | Phase 1 |
| Favorites marking strategy | You | Phase 1 |
| Audio root folder | You | Phase 1 |
| `claude-agent-sdk` package name/availability | You | Phase 2 |
| Architect + Selector: 1 agent or 2? | Architecture call | Phase 2 |
| `rubberband` CLI available? | You (brew install) | Phase 3 |
| CLAP model variant (general vs music) | You | Phase 5 |
