# Database

## Provider

Supabase (managed Postgres + pgvector). Connection via `DATABASE_URL` in `.env`.
DB ops are **skipped when `DATABASE_URL` is empty** — all unit tests run without
a live database.

---

## Schema v2 (`src/dj/vibe/schema.sql`)

> **Status (2026-06-04):** **schema v2 is live** in `schema.sql` — the `tracks`
> taste columns, `tracks.loudness_lufs`, and the full `sections` table (per-section
> vector + bounds + downbeat + LUFS + mix flags) are all present. The DDL
> self-migrates a v1 table (adds the new columns, drops the old `energy_mean`);
> `energy_curve` is **kept on `tracks` for display** (the comparable energy measure
> is `loudness_lufs`). The remaining step is the live **ingest run** to populate it.

Two tables. **`tracks`** is one row per audio file (global features + the
acoustic vector + the personal taste columns). **`sections`** is many rows per
track (the functional parts, each with its own vibe vector) — this is what lets a
set use *part* of a track. See `ADR 0003` (taste) and `ADR 0004` (sections).

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- One row per audio file.
CREATE TABLE tracks (
    id            BIGSERIAL PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,        -- natural key (local file path)
    source        TEXT NOT NULL DEFAULT 'local',

    -- global structured features (hard mixing constraints)
    duration_s    REAL NOT NULL,
    bpm           REAL NOT NULL,               -- downbeat-derived (ADR 0005), not raw librosa
    camelot       TEXT NOT NULL,               -- e.g. "8B", "11A"
    loudness_lufs REAL NOT NULL,               -- integrated LUFS, cross-track comparable
    energy_curve  REAL[] NOT NULL,             -- 8-pt normalized arc, display only (not comparable)

    -- display metadata + cheap keyword filter
    title         TEXT,
    artist        TEXT,
    genre         TEXT,
    tags          TEXT[],                      -- file ID3 tags, normalized
    is_favorite   BOOLEAN NOT NULL DEFAULT FALSE,

    -- GENERAL vibe: CLAP acoustic vector (shares space with text → text→audio)
    embedding     vector(512) NOT NULL,        -- must match config.VIBE_DIM

    -- ME: personal taste (ADR 0003). Nullable until tagged/propagated.
    taste_note    TEXT,
    taste_vec     vector(384),                 -- note embedded; must match config.TASTE_DIM
    taste_source  TEXT,                         -- 'manual' | 'propagated' | NULL
    taste_confidence REAL,                       -- propagation confidence 0..1 (propagated rows, #7)
    rating        SMALLINT,                     -- 1..5 | NULL
    role          TEXT,                         -- 'warmup'|'peak'|'closer'|... | NULL

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Many rows per track: functional sections (ADR 0004).
CREATE TABLE sections (
    id          BIGSERIAL PRIMARY KEY,
    track_id    BIGINT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    idx         SMALLINT NOT NULL,             -- order within the track (0-based)
    label       TEXT NOT NULL,                 -- intro|verse|build|chorus|drop|break|bridge|outro
    start_s     REAL NOT NULL,
    end_s       REAL NOT NULL,
    start_beat  INT,                           -- downbeat index → phrase-aligned cueing
    bars        INT,
    energy_lufs REAL,                          -- section short-term LUFS (comparable); NULL until measured
    camelot     TEXT,                          -- local key if it differs from the track
    is_mixin    BOOLEAN NOT NULL DEFAULT FALSE,-- clean entry point
    is_mixout   BOOLEAN NOT NULL DEFAULT FALSE,-- clean exit point
    loopable    BOOLEAN NOT NULL DEFAULT FALSE,
    embedding   vector(512) NOT NULL,          -- CLAP vibe of THIS section
    UNIQUE (track_id, idx)
);

-- ANN indexes for vibe search (cosine).
CREATE INDEX tracks_embedding_idx   ON tracks   USING hnsw (embedding vector_cosine_ops);
CREATE INDEX tracks_taste_idx       ON tracks   USING hnsw (taste_vec vector_cosine_ops);
CREATE INDEX sections_embedding_idx ON sections USING hnsw (embedding vector_cosine_ops);
-- B-tree indexes for the hard mixing-constraint filters.
CREATE INDEX tracks_bpm_idx         ON tracks   (bpm);
CREATE INDEX tracks_camelot_idx     ON tracks   (camelot);
CREATE INDEX sections_track_idx     ON sections (track_id);
CREATE INDEX sections_label_idx     ON sections (label);
```

### Why two tables

A track row answers "is this the right *song*?" (discovery, taste, global
key/tempo). A section row answers "is this the right *part* to mix?" (the
outro of A vs the intro of B). DJs use parts, not just whole tracks, so the part
is a first-class object with its own vector and its own energy. `ON DELETE
CASCADE` keeps sections tied to their track on re-ingest.

### Why both representations live in `tracks`

The acoustic vector (general) and taste vector (me) sit in the same row so a
blended query is one statement — no second store, no app-side join, no sync lag
(`ADR 0001`, `ADR 0003`). `taste_vec` is nullable; HNSW simply skips NULLs until
a track is tagged or propagated.

### Why `path` is the unique key

Files are identified by filesystem path; a re-ingest does `ON CONFLICT (path) DO
UPDATE` on `tracks` and replaces its `sections`. Safe to run the Curator
repeatedly.

---

## The queries that matter

**Blended track ranking** (the Selector's candidate retrieval) — acoustic +
taste + rating, behind the hard mixing filters:

```sql
SELECT t.path, t.bpm, t.camelot,
       0.5 * (1 - (t.embedding <=> :q_acoustic))
     + 0.4 * (1 - (t.taste_vec <=> :q_taste))
     + 0.1 * (COALESCE(t.rating, 0) / 5.0)        AS score
FROM tracks t
WHERE t.bpm BETWEEN :lo AND :hi
  AND t.camelot = ANY(:keys)
ORDER BY score DESC
LIMIT :k;
```

**Section transition match** — "what mixes cleanly out of A's outro?": find
mix-in-capable sections in compatible keys/tempo, ranked by vibe proximity to A's
outro section vector:

```sql
SELECT s.track_id, s.label, s.start_s, s.start_beat,
       s.embedding <=> :a_outro_vec AS distance
FROM sections s
JOIN tracks t ON t.id = s.track_id
WHERE s.is_mixin = TRUE
  AND t.bpm BETWEEN :lo AND :hi
  AND t.camelot = ANY(:keys)
  AND s.track_id <> :a_track_id
ORDER BY distance
LIMIT :k;
```

---

## Access layer (`src/dj/vibe/store.py`)

| Function | Status | Purpose |
|---|---|---|
| `ensure_schema()` | ✅ | Create extension + table + indexes if absent (applies the additive taste columns too). Idempotent. |
| `upsert_track(features, tags, embedding, sections, source, is_favorite)` | ✅ | Insert/replace one track row **+ its sections** in one txn (taste columns untouched). |
| `nearest(embedding, k, exclude_path, filters)` | ✅ | Track-level cosine KNN (acoustic) + SQL filters. |
| `nearest_to_text(text, k, filters)` | ✅ | **Text→audio** track search via CLAP's text encoder. |
| `get_track(path)` | ✅ | One track: display fields + acoustic vector + current taste (tagging CLI). |
| `set_taste(path, note, vec, rating, role)` | ✅ | Write my manual taste (`taste_source='manual'`). |
| `set_propagated_taste(path, vec, rating, confidence)` | ✅ | Write a provisional vector **+ its propagation `confidence`**; never clobbers a manual label. |
| `tagged_corpus()` / `untagged()` | ✅ | Vectors in / out of the labeled set — the propagation inputs. |
| `favorite_taste_vectors()` | ✅ | Taste vectors of the manually-tagged tracks — the eval taste-match reference set. |
| `taste_eval_rows(paths)` | ✅ | Per-path `taste_vec` + source + `is_favorite` — the eval scorecard inputs. |
| `nearest_taste(taste_vec, k, filters)` | ✅ | Rank by the personal taste vector. |
| `ranked(q_acoustic, q_taste, weights, filters, k, pool)` | ✅ | **Blended** retrieve-then-rerank (α/β/γ) — the Selector's main entry. |
| `nearest_section(embedding, k, filters, mixin, mixout, exclude_path)` | ✅ | Section-level KNN (mix flags, key/BPM) — transition matching. |
| `get_sections(path)` | ✅ | Every section of a track, in order — the Selector + Mixer. |
| `get_cards(paths)` | ✅ | Batch display+mixing fields for a path set (avoids an N+1 in the Selector). |
| `count()` | ✅ | Row count — Curator progress. |

`filters` is a dict, e.g. `{"bpm": (122, 126), "camelot": {"8A", "9A"},
"is_favorite": True}`. Python lists/arrays adapt to Postgres arrays via psycopg2;
embeddings serialize to pgvector's `'[...]'` string. `_build_where` also supports
an `exclude_paths` key (`path <> ALL(...)`) — used to skip recently-played tracks
across sets.

---

## Applying the schema

```bash
psql "$DATABASE_URL" -f src/dj/vibe/schema.sql   # one-time / CI
# or: the Curator auto-applies on first run
uv run python -m dj.curator ~/Music/some-folder
```

### Enabling pgvector

The `vector` extension must exist before any table is created. `schema.sql` runs
`CREATE EXTENSION IF NOT EXISTS vector`, so applying the schema enables it — but
that only works if the connecting role is allowed to create extensions. On
Supabase you can instead enable it once via **Dashboard → Database → Extensions**
(search `vector`); otherwise enable it once as a superuser / database owner before
running the Curator with a lower-privilege role.

### Dimension invariants

`tracks.embedding` is `vector(512)` (`config.VIBE_DIM`, CLAP). `tracks.taste_vec`
is `vector(384)` (`config.TASTE_DIM`, the sentence-transformer). `sections.embedding`
is `vector(512)` (CLAP). Change any dimension → `ALTER TABLE` + index rebuild +
re-ingest of that vector.

---

## Open questions / decisions

- **`is_favorite`**: set when a path contains a `favorites/` segment, or for a
  whole folder via `--favorites <folder>`. Favorites are the first tagging queue.
- **Default α/β/γ**: starting point 0.5 / 0.4 / 0.1, shifting toward taste as
  labels accumulate (`docs/taste.md`). Tune on real data.
- **Section label vocabulary**: fixed set above; refine once we see how the
  detector labels real tracks (`ADR 0005`).
