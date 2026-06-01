# dj-agent — Claude Code project guide

## What this is

A DJ agent that analyzes a music library into a vibe vector DB and renders real
beatmatched DJ sets with a planned energy arc. See `BUILD_PLAN.md` for the full
pitch and `docs/` for deep reference.

## Quick orientation

```
src/dj/
├── config.py           # Settings (DATABASE_URL, sample rate, HITL_LEVEL)
├── audio/
│   ├── analyze.py      # librosa → TrackFeatures
│   └── camelot.py      # Camelot wheel (pure logic, no audio deps)
├── vibe/
│   ├── schema.sql      # pgvector DDL (tracks table + HNSW index)
│   ├── embed.py        # TrackFeatures → 28-d L2-normalized vibe vector
│   └── store.py        # upsert + cosine KNN (nearest())
├── curator.py          # ingestion pipeline: folder → analyze → embed → DB
└── agents/             # Phase 2: Architect + Selector (Claude Agent SDK)
tests/                  # camelot, embed, analyze (9 tests, no DB needed)
docs/                   # architecture, database, embeddings, phases, ADRs
```

## Running the tests

```bash
uv run pytest -q          # all 9 pass; no DATABASE_URL needed
```

## Current state: Phase 0 complete, Phase 1 ready to start

All scaffold code is in place. Phase 1 (Curator + live vibe DB) only needs:
1. `DATABASE_URL` in `.env` (Supabase Postgres + pgvector)
2. A folder of audio files to ingest

## Starting a session

Tell Claude: *"Building the DJ agent, plan in BUILD_PLAN.md, on Phase [X].
Check docs/phases.md for open questions."*

## Key invariants

- `config.VIBE_DIM = 28` must match the `vector(28)` column in `schema.sql`
  and the layout in `embed.py`. Change all three together.
- DB ops are skipped when `DATABASE_URL` is empty — unit tests always run clean.
- The Curator is idempotent: re-ingesting a file does an upsert, not a duplicate.
- Camelot compatibility is a hard constraint the Selector enforces; vibe
  similarity is the soft ranking within compatible candidates.

## Stack

| Concern | Library |
|---|---|
| Audio analysis | `librosa` + `soundfile` |
| Vibe embedding (v1) | numpy (engineered vector) |
| Vector store | `pgvector` via `psycopg2` + Supabase |
| Agent harness | `claude-agent-sdk` (Phase 2+) |
| Substrate | `agent-core` (editable dep at `../agent-core`) |
| Mixer | `pydub` + `pyrubberband` (Phase 3+) |
| Tracing | `langfuse` via agent-core |
| Linter | `ruff` |
| Tests | `pytest` |

## agent-core

Sibling repo at `../agent-core`, installed as an editable dep. Do not fork or
copy it. Use `agent_core.tracing.trace` for span instrumentation in the
Curator and future agents.
