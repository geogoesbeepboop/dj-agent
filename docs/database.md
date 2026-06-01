# Database

## Provider

Supabase (managed Postgres + pgvector). Connection via `DATABASE_URL` in `.env`.
DB ops are **skipped when `DATABASE_URL` is empty** — all unit tests run without
a live database.

---

## Schema (`src/dj/vibe/schema.sql`)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE tracks (
    id            BIGSERIAL PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,   -- absolute path, the natural key
    duration_s    REAL NOT NULL,
    bpm           REAL NOT NULL,
    camelot       TEXT NOT NULL,          -- e.g. "8B", "11A"
    energy_mean   REAL NOT NULL,
    is_favorite   BOOLEAN NOT NULL DEFAULT FALSE,
    embedding     vector(28) NOT NULL,    -- must match config.VIBE_DIM
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX tracks_embedding_idx
    ON tracks USING hnsw (embedding vector_cosine_ops);
```

### Why `path` as the unique key

Audio files are identified by their filesystem path. A re-ingest of the same
file does an `ON CONFLICT (path) DO UPDATE` upsert — safe to run the Curator
repeatedly without duplicating rows.

### The embedding column

`vector(28)` must stay in sync with `config.VIBE_DIM = 28`. If you change the
embedding scheme (e.g. Phase 5 CLAP → 512 dims), you must also run a migration
to resize the column and re-ingest the library.

---

## HNSW index

`vector_cosine_ops` means the index accelerates cosine-distance queries, which
is what `store.nearest()` runs (`embedding <=> query_vec`). At library scale
(~10k tracks) HNSW gives sub-millisecond ANN search.

For exact results (e.g. evals) you can drop the index and pgvector falls back
to a sequential scan. Don't do this in production queries.

---

## Access layer (`src/dj/vibe/store.py`)

| Function | Purpose |
|---|---|
| `ensure_schema()` | Creates extension + table + index if absent. Safe to call repeatedly. |
| `upsert_track(features, embedding, is_favorite)` | Insert or update one track. |
| `nearest(embedding, k, exclude_path)` | Cosine KNN search — the core "vibe query". Returns `Neighbor(path, bpm, camelot, distance)`. |
| `count()` | Row count — used by the Curator's progress report. |

All functions open a fresh connection per call (psycopg2). Fine for the
Curator's batch use; Phase 2 may want a connection pool if the Selector loops
tight.

---

## Applying the schema

Two ways:

```bash
# Option 1: psql (one-time or CI)
psql "$DATABASE_URL" -f src/dj/vibe/schema.sql

# Option 2: Curator auto-applies on first run
uv run python -m dj.curator ~/Music/some-folder
```

`store.ensure_schema()` wraps the SQL in `CREATE ... IF NOT EXISTS`, so it's
idempotent.

---

## Phase 5 migration notes

CLAP embeddings are 512-d. The migration will need to:
1. `ALTER TABLE tracks ALTER COLUMN embedding TYPE vector(512);`
2. Drop and recreate `tracks_embedding_idx` for the new dimension.
3. Re-run the Curator over the full library.
4. Update `config.VIBE_DIM = 512`.

This is intentionally a breaking schema change — old 28-d vectors and new 512-d
vectors are not comparable.

---

## Open questions / decisions needed from you

- **Supabase project**: do you have one already, or do we need to create it?
  (`DATABASE_URL` in `.env` is the only config needed once it exists.)
- **`is_favorite` population**: the schema column is there; the Curator always
  writes `False` unless you pass `is_favorite=True` to `ingest_file`. How do you
  want to mark favorites — a flag file, a separate CLI flag, or a sidecar JSON?
- **Audio root**: the Curator walks a single folder. Do you have a canonical
  music root (`~/Music`?) or do you mix paths from multiple sources?
