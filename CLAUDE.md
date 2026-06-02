# dj-agent — Claude Code project guide

## What this is

A DJ agent that analyzes a music library into a vibe vector DB and renders real
beatmatched DJ sets with a planned energy arc. See `BUILD_PLAN.md` for the full
pitch and `docs/` for deep reference.

## Quick orientation

```
src/dj/
├── config.py           # Settings (DATABASE_URL, CLAP_MODEL, sample rates, HITL_LEVEL)
├── audio/
│   ├── analyze.py      # librosa → TrackFeatures (structured mixing features)
│   └── camelot.py      # Camelot wheel (pure logic, no audio deps)
├── vibe/
│   ├── schema.sql      # pgvector DDL (tracks: cols + metadata + vector(512))
│   ├── clap.py         # CLAP encoder: embed_audio + embed_text → 512-d vector
│   └── store.py        # upsert + cosine KNN (nearest, nearest_to_text)
├── metadata.py         # mutagen → ID3 tags (genre, mood) as keyword filter
├── sources/            # SourceProvider seam: LocalFolderProvider (remote later)
├── curator.py          # pipeline: source → analyze + CLAP + tags → DB
└── agents/             # Phase 2: Architect + Selector (Claude Agent SDK)
tests/                  # camelot, analyze, metadata (fast); clap (slow)
docs/                   # architecture, database, embeddings, phases, ADRs
```

## Two representations per track

- **CLAP vector (512-d)** — semantics ("dreamy, driving"); powers vibe ranking
  and **text→audio search** (`store.nearest_to_text`).
- **Structured columns** — exact BPM, Camelot key, energy arc; the hard mixing
  constraints the Selector filters on via SQL.

Semantics go in the vector, mixing math goes in columns — never mixed.

## Running the tests

```bash
uv run pytest -q          # fast suite (camelot, analyze, metadata); no DB needed
uv run pytest -m slow     # CLAP tests (downloads ~1.5 GB weights, runs inference)
```

## Current state: Phase 1 code complete (CLAP-first)

All Phase 1 code is in place. To go live:
1. `DATABASE_URL` in `.env` (Supabase Postgres + pgvector) — already set
2. `uv run python -m dj.curator <folder>` — first run downloads CLAP weights
3. Smoke-test `store.nearest_to_text("dreamy nocturnal")`

## Starting a session

Tell Claude: *"Building the DJ agent, plan in BUILD_PLAN.md, on Phase [X].
Check docs/phases.md for open questions."*

## Key invariants

- `config.VIBE_DIM = 512` must match the `vector(512)` column in `schema.sql`
  (CLAP's output dim). Change both together (+ re-ingest).
- DB ops are skipped when `DATABASE_URL` is empty — unit tests always run clean.
- `clap.embed_audio` and `clap.embed_text` return the *same* 512-d space — that
  shared space is what makes text→audio search work.
- The Curator is idempotent: re-ingesting a file does an upsert, not a duplicate.
- Camelot compatibility is a hard constraint the Selector enforces; vibe
  similarity is the soft ranking within compatible candidates.
- Streaming sources are excluded by design — beatmatching needs the raw file.

## Stack

| Concern | Library |
|---|---|
| Audio analysis (structured) | `librosa` + `soundfile` |
| Vibe embedding (CLAP, 512-d) | `torch` + `transformers` (`laion/larger_clap_music`) |
| Metadata tags | `mutagen` |
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
