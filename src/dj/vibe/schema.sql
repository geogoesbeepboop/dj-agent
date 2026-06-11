-- Vibe vector DB schema v2 (Supabase / Postgres + pgvector).
-- Run once: psql "$DATABASE_URL" -f src/dj/vibe/schema.sql
-- (store.ensure_schema() also applies this idempotently on first ingest.)
--
-- Two tables (docs/database.md): `tracks` is one row per file (global features +
-- the acoustic vector + the personal taste columns, ADR 0003); `sections` is many
-- rows per track (the functional parts, each with its own vibe vector + energy,
-- ADR 0004) — this is what lets a set use *part* of a track and mix on musical
-- boundaries. Energy is cross-track LUFS, BPM is downbeat-derived (ADR 0005).

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per audio file.
CREATE TABLE IF NOT EXISTS tracks (
    id            BIGSERIAL PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,
    source        TEXT NOT NULL DEFAULT 'local',

    -- structured mixing constraints (the Selector filters on these)
    duration_s    REAL NOT NULL,
    bpm           REAL NOT NULL,                   -- downbeat-derived (ADR 0005)
    camelot       TEXT NOT NULL,
    loudness_lufs REAL NOT NULL,                   -- integrated LUFS, cross-track comparable
    energy_curve  REAL[] NOT NULL,                 -- 8-pt normalized arc (display only)
    first_downbeat_s REAL,                         -- first bar-"1" time (s) → rekordbox grid anchor (ADR 0011)

    -- metadata (cheap semantic bridge + display)
    title         TEXT,
    artist        TEXT,
    genre         TEXT,
    isrc          TEXT,                            -- recording id (ADR 0008): gold key for dedup/matching
    tags          TEXT[],                          -- normalized descriptor keywords
    is_favorite   BOOLEAN NOT NULL DEFAULT FALSE,

    -- GENERAL vibe: CLAP acoustic vector (shares space with text → text→audio)
    embedding     vector(512) NOT NULL,            -- must match config.VIBE_DIM

    -- ME: personal taste (ADR 0003). Nullable until tagged/propagated.
    taste_note    TEXT,
    taste_vec     vector(384),                     -- must match config.TASTE_DIM
    taste_source  TEXT,                            -- 'manual' | 'propagated' | NULL
    taste_confidence REAL,                          -- propagation confidence 0..1 (propagated rows)
    rating        SMALLINT,                        -- 1..5 | NULL
    role          TEXT,                            -- 'warmup'|'peak'|'closer'|... | NULL

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Parked taste reviews captured before I own the file (ADR 0008). A review left
-- while listening (Spotify now-playing or in chat) lands here keyed by track
-- identity; the Curator drains it into tracks.taste_* on the matching ingest, so
-- the note is "already ready" with no extra input. Unmatched rows are a want-list.
CREATE TABLE IF NOT EXISTS pending_taste (
    id           BIGSERIAL PRIMARY KEY,
    isrc         TEXT,                              -- gold key: Spotify external_ids.isrc / file TSRC
    spotify_id   TEXT,
    artist       TEXT NOT NULL,
    title        TEXT NOT NULL,
    album        TEXT,
    duration_s   REAL,                              -- from Spotify at capture; ± tolerance at match
    note         TEXT NOT NULL,                     -- the taste signal; embedded at apply-time
    rating       SMALLINT,                          -- 1..5 | NULL  (same vocab as dj.taste.tag)
    role         TEXT,                              -- warmup|build|peak|closer|tool|wildcard | NULL
    source       TEXT,                              -- 'spotify_now' | 'chat' | 'manual'
    status       TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'applied'
    matched_path TEXT,                              -- the track path it landed on, once applied
    captured_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Many rows per track: functional sections (ADR 0004).
CREATE TABLE IF NOT EXISTS sections (
    id          BIGSERIAL PRIMARY KEY,
    track_id    BIGINT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    idx         SMALLINT NOT NULL,                 -- order within the track (0-based)
    label       TEXT NOT NULL,                     -- intro|verse|build|chorus|drop|break|bridge|outro
    start_s     REAL NOT NULL,
    end_s       REAL NOT NULL,
    start_beat  INT,                               -- downbeat index → phrase-aligned cueing
    bars        INT,
    energy_lufs REAL,                              -- section short-term LUFS (comparable)
    camelot     TEXT,                              -- local key if it differs from the track
    is_mixin    BOOLEAN NOT NULL DEFAULT FALSE,    -- clean entry point
    is_mixout   BOOLEAN NOT NULL DEFAULT FALSE,    -- clean exit point
    loopable    BOOLEAN NOT NULL DEFAULT FALSE,
    embedding   vector(512) NOT NULL,              -- CLAP vibe of THIS section
    UNIQUE (track_id, idx)
);

-- Additive self-migration: bring a v1 table (energy_mean/energy_curve, no taste,
-- no LUFS) up to v2 without dropping the row data (there's no production data, so
-- the old energy_mean column is simply dropped — energy_curve is kept for display).
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS loudness_lufs REAL;
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS taste_note   TEXT;
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS taste_vec    vector(384);
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS taste_source TEXT;
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS taste_confidence REAL;
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS rating       SMALLINT;
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS role         TEXT;
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS isrc         TEXT;
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS first_downbeat_s REAL;
ALTER TABLE tracks DROP COLUMN IF EXISTS energy_mean;

-- Approximate nearest-neighbor indexes for fast cosine "vibe" search.
CREATE INDEX IF NOT EXISTS tracks_embedding_idx
    ON tracks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS tracks_taste_idx
    ON tracks USING hnsw (taste_vec vector_cosine_ops);
CREATE INDEX IF NOT EXISTS sections_embedding_idx
    ON sections USING hnsw (embedding vector_cosine_ops);

-- B-tree indexes for the hard mixing-constraint filters.
CREATE INDEX IF NOT EXISTS tracks_bpm_idx     ON tracks (bpm);
CREATE INDEX IF NOT EXISTS tracks_camelot_idx ON tracks (camelot);
CREATE INDEX IF NOT EXISTS tracks_isrc_idx    ON tracks (isrc);
CREATE INDEX IF NOT EXISTS sections_track_idx ON sections (track_id);
CREATE INDEX IF NOT EXISTS sections_label_idx ON sections (label);

-- Pending-review lookups: the Curator drains 'pending' rows at ingest (ADR 0008).
CREATE INDEX IF NOT EXISTS pending_taste_status_idx ON pending_taste (status);
CREATE INDEX IF NOT EXISTS pending_taste_isrc_idx   ON pending_taste (isrc);
