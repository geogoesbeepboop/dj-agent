# Your TODO — the live edges only you can run

Everything through Phase 4 plus the Phase 5 scorecard/`--explain`/persistence is
**built and unit-tested offline** (103 fast tests). What's left needs your
machine, your library, your accounts, or your ears. Work top-to-bottom.

## One-time setup

- [ ] **`uv sync`** — base deps. Add extras as you need them:
      `uv sync --extra segment` (allin1 detector; the librosa fallback runs without it),
      `uv sync --extra mixer` (time-stretch + EQ render),
      `uv sync --extra agents` (only if you wrap the toolbelt in the Claude Agent SDK).
- [ ] **`brew install rubberband`** — the CLI `pyrubberband` shells out to for
      beat-matching time-stretch. Without it the Mixer plays at native tempo (no crash).
- [ ] **Supabase / Postgres**: create the project, **enable the `vector` extension**
      (Dashboard → Database → Extensions, or it's created by `schema.sql` if your role
      may create extensions), put the connection string in `.env` as `DATABASE_URL`.
- [ ] **`cp .env.example .env`** and fill it in. `ANTHROPIC_API_KEY` is only needed
      for **live** (non-`--offline`) set generation; ingest/tag/eval never call the LLM.
- [ ] Apply the schema: `psql "$DATABASE_URL" -f src/dj/vibe/schema.sql`
      (or just run the Curator once — it self-applies).

## First real run (Phases 1–2)

- [ ] **Ingest** 20–50 tracks: `uv run python -m dj.curator ~/Music/<folder>`.
      Confirm whether `allin1` installed or the librosa fallback ran — the detector
      name is on the trace span (`detector`), and a file that fell all the way to the
      single-section fallback now records *why* in its provenance.
- [ ] **Sanity-check retrieval**: in a REPL, `from dj.vibe import store;
      store.nearest_to_text("dreamy nocturnal")` → do the neighbors feel right?
      `store.nearest_section(...)` → sensible *parts*?
- [ ] **Tag ~50 favorites**: `uv run python -m dj.taste.tag` (walks the
      active-learning queue), then `uv run python -m dj.taste.tag --propagate`.
      Smoke-test `--queue` surfaces sensible next labels.

## Generate, eval, render (Phases 3–5)

- [ ] **Generate a set** (offline first): 
      `uv run python -m dj.agents.generate "2-hr sunset rooftop, deep→melodic, slow build" --offline --explain`
      — track count is now derived from `--minutes` (~3.5 min/track) unless you pass
      `--tracks`; `--explain` narrates the arc + section picks; every run is logged to
      `renders/set_history.jsonl` and recently-played tracks are skipped (`--allow-repeats`
      to override).
- [ ] **Eval A/B**: `uv run python -m dj.evals.runner "<same brief>"` — confirm the
      blended set's **taste-match beats the CLAP-only baseline** (the Phase 3 goal).
      Early on (few tags) the delta may be ~0; it should grow as you tag more.
- [ ] **Render + LISTEN**: re-run generate with `--render` (needs the `mixer` extra
      + rubberband).

## Validate — needs your ears (I couldn't hear any of this)

- [ ] **Beat phase-lock** on the first render: are transitions actually beat-aligned?
      The renderer does tempo-glide equal-power crossfades but does **not** yet
      sample-accurately phase-lock downbeats or match seam tempo. If transitions sound
      off-grid, that's expected → prioritize **backlog A1** and we'll tune it together.
- [ ] **Section labels**: do the detected `drop`/`break`/`intro`/`bridge` labels match
      reality on your tracks? If the librosa fallback mislabels, that tells us whether
      `allin1` is worth fighting to install.

## Tune on real data

- [ ] **Blend weights** α/β/γ (`DJ_W_ACOUSTIC` / `DJ_W_TASTE` / `DJ_W_RATING`) — lean
      taste-heavier as your label count grows.
- [ ] **Greedy cost weights + Critic thresholds** (`src/dj/agents/selector.py`,
      `src/dj/critic.py`) — once you've seen a few sets.
- [ ] **Tempo band** if your library has unusual tempos (the octave fold now keeps
      DnB/footwork at full tempo, but check it on your edge cases).

## Decisions I'm waiting on (see `docs/backlog.md`)

- [ ] **B1** — authorize Spotify playlist export (publishes to your account).
- [ ] **C1** — allow extended (energy-boost) harmonic moves through the key gate?
- [ ] **C2** — when/how to add friends' taste ("we").
- [ ] **Security** — rotate any real secret that's been in `.env` if this repo or
      machine is shared (`.env` is gitignored and untracked — no leak found).
