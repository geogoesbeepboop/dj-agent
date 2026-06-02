# Database

## Provider

Supabase (managed Postgres + pgvector). Connection via `DATABASE_URL` in `.env`.
DB ops are **skipped when `DATABASE_URL` is empty** — all unit tests run without
a live database.

---

## Schema (`src/dj/vibe/schema.sql`)

Each track is one row holding **both representations** — the CLAP semantic
vector *and* the structured mixing columns — plus metadata:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE tracks (
    id            BIGSERIAL PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,   -- the natural key (local file path)
    source        TEXT NOT NULL DEFAULT 'local',

    -- structured mixing constraints (the Selector filters on these)
    duration_s    REAL NOT NULL,
    bpm           REAL NOT NULL,
    camelot       TEXT NOT NULL,          -- e.g. "8B", "11A"
    energy_mean   REAL NOT NULL,
    energy_curve  REAL[] NOT NULL,        -- 8-point normalized energy arc

    -- metadata (cheap semantic bridge + display)
    title         TEXT,
    artist        TEXT,
    genre         TEXT,
    tags          TEXT[],                 -- normalized descriptor keywords
    is_favorite   BOOLEAN NOT NULL DEFAULT FALSE,

    -- semantic vibe vector (CLAP; shares space with text)
    embedding     vector(512) NOT NULL,   -- must match config.VIBE_DIM
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX tracks_embedding_idx ON tracks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX tracks_bpm_idx       ON tracks (bpm);
CREATE INDEX tracks_camelot_idx   ON tracks (camelot);
```

### Why both in one row

A vibe query is "find candidates that *feel* right (vector) **and** *will mix*
(BPM/key columns)." Keeping both in one row means one SQL statement does it:

```sql
SELECT path, bpm, camelot, embedding <=> :q AS distance
FROM tracks
WHERE bpm BETWEEN 122 AND 126 AND camelot = ANY('{8A,9A}')
ORDER BY distance
LIMIT 10;
```

A separate vector DB (Pinecone) would split this into two systems you'd have to
keep in sync and join in application code. See `adr/0001-pgvector-over-pinecone.md`.

### Why `path` as the unique key

Audio files are identified by their filesystem path. A re-ingest does an
`ON CONFLICT (path) DO UPDATE` upsert — safe to run the Curator repeatedly.

### The embedding column

`vector(512)` must stay in sync with `config.VIBE_DIM = 512` (CLAP's output
dim). Changing the embedding model to a different dimension means an
`ALTER TABLE` + index rebuild + full re-ingest.

---

## Indexes

- **`tracks_embedding_idx` (HNSW, cosine)** — accelerates the `embedding <=> q`
  vibe search. At ~10k tracks it's sub-millisecond. Drop it for exact results in
  evals; don't drop it in production.
- **`tracks_bpm_idx`, `tracks_camelot_idx` (B-tree)** — back the hard
  mixing-constraint filters so they stay cheap alongside the vector scan.

---

## Access layer (`src/dj/vibe/store.py`)

| Function | Purpose |
|---|---|
| `ensure_schema()` | Create extension + table + indexes if absent. Idempotent. |
| `upsert_track(features, tags, embedding, source, is_favorite)` | Insert/update one track (all columns). |
| `nearest(embedding, k, exclude_path, filters)` | Cosine KNN from a seed vector + optional SQL filters. |
| `nearest_to_text(text, k, filters)` | **Text→audio** vibe search: CLAP-encodes the prompt, then `nearest()`. |
| `count()` | Row count — used by the Curator's progress report. |

`filters` is a dict: `{"bpm": (122, 126), "camelot": {"8A", "9A"}, "is_favorite": True}`.
Python lists for `energy_curve` and `tags` are adapted to Postgres arrays by
psycopg2 directly; the embedding is serialized to pgvector's `'[...]'` string.

---

## Applying the schema

```bash
# Option 1: psql (one-time or CI)
psql "$DATABASE_URL" -f src/dj/vibe/schema.sql

# Option 2: the Curator auto-applies on first run
uv run python -m dj.curator ~/Music/some-folder
```

---

## Open questions / decisions

- **`is_favorite`**: marked automatically when a path contains a `favorites/`
  segment, or for a whole folder via `--favorites <folder>`. Switch strategies
  by changing `LocalFolderProvider`.
- **Audio root**: the Curator walks one folder per provider invocation. Multiple
  roots = multiple runs (idempotent, so safe).
