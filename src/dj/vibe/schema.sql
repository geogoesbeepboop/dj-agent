-- Vibe vector DB schema (Supabase / Postgres + pgvector).
-- Run once: psql "$DATABASE_URL" -f src/dj/vibe/schema.sql
-- (store.ensure_schema() also applies this idempotently.)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tracks (
    id            BIGSERIAL PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,
    duration_s    REAL NOT NULL,
    bpm           REAL NOT NULL,
    camelot       TEXT NOT NULL,
    energy_mean   REAL NOT NULL,
    is_favorite   BOOLEAN NOT NULL DEFAULT FALSE,
    embedding     vector(28) NOT NULL,          -- must match config.VIBE_DIM
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Approximate nearest-neighbor index for fast cosine "vibe" search.
CREATE INDEX IF NOT EXISTS tracks_embedding_idx
    ON tracks USING hnsw (embedding vector_cosine_ops);
