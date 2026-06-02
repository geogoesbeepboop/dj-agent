-- Vibe vector DB schema (Supabase / Postgres + pgvector).
-- Run once: psql "$DATABASE_URL" -f src/dj/vibe/schema.sql
-- (store.ensure_schema() also applies this idempotently.)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tracks (
    id            BIGSERIAL PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,
    source        TEXT NOT NULL DEFAULT 'local',

    -- structured mixing constraints (the Selector filters on these)
    duration_s    REAL NOT NULL,
    bpm           REAL NOT NULL,
    camelot       TEXT NOT NULL,
    energy_mean   REAL NOT NULL,
    energy_curve  REAL[] NOT NULL,                -- 8-point normalized energy arc

    -- metadata (cheap semantic bridge + display)
    title         TEXT,
    artist        TEXT,
    genre         TEXT,
    tags          TEXT[],                         -- normalized descriptor keywords
    is_favorite   BOOLEAN NOT NULL DEFAULT FALSE,

    -- semantic vibe vector (CLAP; shares space with text — enables text→audio)
    embedding     vector(512) NOT NULL,           -- must match config.VIBE_DIM
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Approximate nearest-neighbor index for fast cosine "vibe" search.
CREATE INDEX IF NOT EXISTS tracks_embedding_idx
    ON tracks USING hnsw (embedding vector_cosine_ops);

-- B-tree indexes for the hard mixing-constraint filters.
CREATE INDEX IF NOT EXISTS tracks_bpm_idx ON tracks (bpm);
CREATE INDEX IF NOT EXISTS tracks_camelot_idx ON tracks (camelot);
