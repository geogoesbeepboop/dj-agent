# dj-agent — Build Plan

> Open in a fresh Claude Code session inside `~/dev/dj-agent/`. Phases run
> top-to-bottom; each ends with something you can verify (often something you
> can *hear*). Deep "why" notes live in `docs/`.

The pitch: **"Spotify DJ is a recommender in a costume — it legally can't touch
the audio, so no beatmatch, no arc. Mine analyzes your library into a vibe
vector DB and renders real continuous sets with a planned energy arc, surfacing
discovery picks you didn't know fit."**

---

## Architecture (three layers — same substrate as the migration agent)

```
agent-core  (substrate — reused, unchanged)
├── model providers · tracing · evals · queueing · budgets · sandbox

dj-agent  (the product)
├── Curator        # background: ingest any audio → analyze → embed → vibe DB
├── Vibe Vector DB # pgvector: each track = a point in "vibe space"
├── Architect      # LLM agent: vibe prompt → target energy/BPM arc
├── Selector       # constraint search: order tracks to the arc + discovery
├── Mixer          # cue points, beatmatch (time-stretch), EQ, render transitions
├── Critic         # scores flow; rejects bad transitions
└── Taste/Memory   # learns from skips/replays (Phase 5)

Agent harness = Claude Agent SDK  (Architect + Selector are agent-centric)
```

**Why embeddings/pgvector genuinely belong HERE** (unlike the migration agent):
relevance is *fuzzy* ("does this fit the vibe?") and the corpus is *large/open*
(your whole library). That's the exact both-halves-true case RAG/vector search is
for. See `docs/why-vibe-vectors.md`.

---

## Audio scope

Works on **your actual audio files from any source** — build sets around
favorites + discover tracks you didn't know fit. Flip on **rights-clean mode**
(local-owned / Creative Commons / stems) for any public/portfolio demo. The
differentiator vs Spotify is the **audio-manipulation layer** (real beatmatched
transitions), which they can't do for licensing reasons.

---

## Stack

| Concern | Library |
|---|---|
| Agent harness (Architect/Selector) | `claude-agent-sdk` |
| Substrate (tracing/budget/queue) | `agent-core[anthropic]` (local editable) |
| Audio analysis (BPM/key/structure/energy) | `librosa` (+ `soundfile`); `madmom`/`essentia` later for better beats |
| Vibe embedding (v1) | engineered feature vector (numpy) → pgvector |
| Vibe embedding (Phase 5 upgrade) | **CLAP** learned audio embeddings (text↔audio search) |
| Vector store | `pgvector` via `psycopg2` + Supabase |
| Mixer / rendering | `pydub` + `pyrubberband` (time-stretch); stem separation later |
| Harmonic mixing | Camelot wheel (pure logic, `dj/audio/camelot.py`) |
| Tracing / evals | `langfuse` via agent-core |

---

## Eval scorecard (DJ-quality has semi-objective oracles)

Put set quality **in CI**, mirroring the migration agent's rigor:

| Metric | What it measures |
|---|---|
| **BPM continuity** | max/avg BPM jump between adjacent tracks (smaller = smoother) |
| **Harmonic-compat %** | % transitions that are Camelot-compatible |
| **Energy-arc RMSE** | distance between the actual energy curve and the requested arc |
| **Transition smoothness** | spectral/loudness discontinuity at mix points |
| **Discovery ratio** | % of set that's non-favorite tracks the user kept |
| **Blind listen test** | human A/B vs a naive recommender playlist (the north star) |

---

## HITL gate (mimic production, removable later)

One high-value gate (toggle via `HITL_LEVEL`): **approve the set before rendering.**
The agent proposes the tracklist + arc + transition plan; you approve (or nudge)
before it spends time rendering audio. Becomes a "set acceptance rate" metric.

---

## Phases

### Phase 0 — Scaffold ✅ (done in this scaffolding pass)
```
src/dj/
├── config.py          # settings: DATABASE_URL, audio dirs, embedding dim
├── audio/
│   ├── analyze.py      # librosa → TrackFeatures (BPM, key, energy, spectral)
│   └── camelot.py      # musical key → Camelot code + compatibility (pure logic)
├── vibe/
│   ├── schema.sql      # pgvector table: tracks + embedding
│   ├── embed.py        # TrackFeatures → normalized vibe vector
│   └── store.py        # upsert + cosine-KNN query (semantic vibe search)
├── curator.py          # pipeline: file → analyze → embed → store
└── agents/             # Phase 2: Architect + Selector (Claude Agent SDK)
tests/                  # camelot logic, embedding shape, synthetic-tone analysis
```
**Verify:** `uv run pytest -q` green; `uv run python -m dj.audio.camelot` prints a wheel.

### Phase 1 — Curator + Vibe DB (the "gold mine")
*Concept (ask `tutor`): embeddings, cosine similarity, pgvector.*
- Flesh out `analyze.py`: BPM (beat tracking), key→Camelot, duration, an **energy
  curve** (RMS over time), spectral features, structural segments (intro/outro
  for cue points later).
- `embed.py`: assemble a fixed-length normalized vector from those features.
- `store.py`: create the pgvector table in Supabase; upsert analyzed tracks;
  expose `nearest(vector, k)` cosine search.
- `curator.py`: walk an audio folder, analyze+embed+store each track, idempotently.
**Verify:** ingest a folder → run a vibe query ("most similar to track X") →
get sensible neighbors. Print BPM/key alongside to sanity-check.

### Phase 2 — Architect + Selector (Claude Agent SDK)
*Concept (ask `tutor`): agent harness, tool calling, the Claude Agent SDK loop.*
- Define tools the agent can call (`query_vibe_db`, `get_track_features`,
  `check_harmonic_compat`) as an in-process SDK MCP server.
- **Architect**: vibe prompt ("2-hr sunset rooftop, deep→melodic, slow build") →
  a target energy/BPM arc (a curve over set position).
- **Selector**: constraint search over the vibe DB to order tracks to the arc —
  smooth BPM ramp, Camelot compatibility, artist spacing, no repeats, blending
  favorites + discovery. Returns a tracklist.
- HITL gate: present tracklist + arc → approve before Phase 3 renders it.
**Verify:** prompt → a coherent tracklist whose BPM/energy actually follow the
requested arc (check with the eval metrics, no audio yet).

### Phase 3 — Mixer (the wow)
- Detect cue points (outro of A / intro of B) from structural segments.
- Beatmatch: time-stretch B to A's BPM (`pyrubberband`); align phrase grid.
- Crossfade + EQ swap; render a continuous mix to a file.
**Verify:** play the rendered mix — transitions are beatmatched, not hard cuts.

### Phase 4 — Eval scorecard + demo
- Implement the scorecard (`evals/runner.py`, agent-core `EvalRunner`).
- A/B vs a naive "similar songs" playlist on the same library.
- Demo: vibe prompt → rendered mix → the agent **explains its arc**
  ("held 122 BPM through warm-up, 8A→9A for lift, peak at track 9").

### Phase 5 — CLAP embeddings + Taste/Memory
- Swap the engineered vector for **CLAP** learned embeddings → enables
  *text→audio* search ("find me something dreamy and nocturnal").
- Learn taste from skips/replays; store accepted transitions to improve over time.

---

## Quickstart (first session)
```bash
cd ~/dev/dj-agent
uv sync                      # installs from the pyproject scaffolded here
cp .env.example .env         # ANTHROPIC_API_KEY, DATABASE_URL (Supabase), LANGFUSE_*
# Phase 1: point the curator at a folder of audio and ingest
uv run python -m dj.curator ~/Music/some-folder
```
Tell Claude Code: *"Building the DJ agent, plan in BUILD_PLAN.md, on Phase [X].
agent-core is at ../agent-core (installed). Use the `planner` subagent first."*
