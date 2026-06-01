# Architecture

## The pitch

Spotify DJ is a recommender in a costume — it legally can't touch audio, so no
beatmatching, no arc. **dj-agent** analyzes your library into a vibe vector DB
and renders real continuous sets with a planned energy arc, surfacing discovery
picks you didn't know fit.

---

## Three-layer model

```
agent-core  (substrate — reused, unchanged)
├── tracing (Langfuse)  · LLM provider  · budget/cost tracking
├── task queue          · eval runner   · sandbox isolation

dj-agent  (the product)
├── Curator        # background pipeline: file → analyze → embed → vibe DB
├── Vibe Vector DB # pgvector: each track = a point in "vibe space"
├── Architect      # LLM agent: vibe prompt → target energy/BPM arc
├── Selector       # constraint search: order tracks to arc + discovery ratio
├── Mixer          # cue points, beatmatch (time-stretch), EQ, render mix
├── Critic         # scores transitions; rejects bad ones
└── Taste/Memory   # learns from skips/replays (Phase 5)
```

**agent-core** is a sibling repo at `../agent-core`. It is installed as an
editable dep (`pip install -e ../agent-core`). dj-agent never forks it.

**Architect + Selector** are Claude Agent SDK agents — they call tools
(`query_vibe_db`, `check_harmonic_compat`, etc.) in a loop until they produce a
tracklist. The rest of the system (Curator, Mixer, Critic) is deterministic
Python — no LLM involved.

---

## Data flow

```
Audio files on disk
       │
       ▼
  [Curator] analyze.py
       │  librosa: BPM · key → Camelot · energy curve · MFCCs · timbre
       │
       ▼
  [embed.py] → 28-d vibe vector (L2-normalized)
       │
       ▼
  [pgvector / Supabase]  tracks table
       │
       ╔══════════════════════════════╗
       ║  SET GENERATION (Phase 2+)  ║
       ╠══════════════════════════════╣
       ║  [Architect]                ║
       ║    vibe prompt → arc target ║
       ║         ↓                   ║
       ║  [Selector]                 ║
       ║    cosine search + BPM/key  ║
       ║    constraints → tracklist  ║
       ║         ↓                   ║
       ║  [HITL gate] approve/nudge  ║
       ╚══════════════════════════════╝
       │
       ▼
  [Mixer] cue points · beatmatch · crossfade → rendered .wav/.mp3
       │
       ▼
  [Critic] scores transition smoothness → accept/retry
```

---

## Component responsibilities

| Component | Phase | Type | Key dep |
|---|---|---|---|
| `dj.audio.analyze` | 0 | Deterministic | librosa |
| `dj.audio.camelot` | 0 | Pure logic | — |
| `dj.vibe.embed` | 0 | Deterministic | numpy |
| `dj.vibe.store` | 0 | I/O | psycopg2 + pgvector |
| `dj.curator` | 1 | Orchestrator | all of the above + agent-core tracing |
| `dj.agents.architect` | 2 | LLM agent | claude-agent-sdk |
| `dj.agents.selector` | 2 | LLM agent | claude-agent-sdk |
| `dj.mixer` | 3 | Deterministic | pydub + pyrubberband |
| `dj.critic` | 4 | Deterministic/heuristic | numpy |
| `dj.memory` | 5 | ML | — |

---

## HITL gate

One high-value human-in-the-loop checkpoint: **approve the set before
rendering.** The Selector proposes a tracklist + arc + transition plan; the
user approves (or nudges the prompt) before the Mixer spends compute rendering.

Controlled by `HITL_LEVEL` in `.env`:
- `full` — always pause before render (default)
- `none` — skip (useful for automated eval runs)

"Set acceptance rate" (approved / proposed) becomes a product metric tracked
via agent-core's eval runner.

---

## Key design decisions

See `docs/adr/` for full records. Short summary:

- **pgvector over a specialized vector DB** — we already have Supabase; avoiding
  a second infra dependency. HNSW index gives sub-millisecond ANN search at
  library scale (~10k tracks). See `adr/0001-pgvector-over-pinecone.md`.

- **Engineered v1 embedding, CLAP in Phase 5** — lets us ship a working vibe DB
  without a GPU model dependency. The embedding interface is stable (28-d float32
  array), so swapping to CLAP only touches `embed.py` + `VIBE_DIM`.

- **Deterministic Curator, LLM only in Architect/Selector** — audio analysis has
  no ambiguity; LLM overhead would add cost with no benefit. The agents earn their
  cost for the genuinely fuzzy "does this vibe fit?" and arc-planning problems.

- **agent-core as substrate, not forked** — tracing, budgets, evals, and the
  task queue are cross-cutting concerns reused across projects. Forking would
  create divergence debt.
