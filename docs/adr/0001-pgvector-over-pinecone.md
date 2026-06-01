# ADR 0001 — pgvector over a dedicated vector DB

**Status:** Accepted  
**Date:** 2026-06-01

## Context

The vibe vector store needs approximate nearest-neighbor search over ~10k
track embeddings (28-d for v1, 512-d for Phase 5 CLAP). Dedicated vector DBs
(Pinecone, Weaviate, Qdrant) are purpose-built for this. We also use Supabase
for general storage (the tracks table).

## Decision

Use **pgvector** (Postgres extension in Supabase) rather than adding a
dedicated vector DB.

## Rationale

- **Zero new infra.** We already have Supabase. A second service (Pinecone
  free tier, self-hosted Qdrant, etc.) adds auth surface, another connection
  to manage, and another thing to break.
- **Scale fits.** A 10k-track library at 512-d CLAP vectors is ~20 MB of
  vector data. HNSW in pgvector handles sub-millisecond ANN at this scale.
  The "pgvector doesn't scale" argument applies at 100M+ vectors.
- **Transactional consistency.** Track metadata and embedding live in the same
  row, same transaction. No sync lag between a metadata DB and a separate
  vector store.
- **Phase 5 migration is localized.** Switching from 28-d to 512-d only
  touches `schema.sql` (one ALTER TABLE) and `config.VIBE_DIM`. The rest of
  the stack is unaffected.

## Trade-offs accepted

- pgvector's HNSW is slightly less recall-accurate than Pinecone at extreme
  scale. Irrelevant at library scale.
- No out-of-the-box hybrid search (vector + metadata filter in one query).
  We layer the Camelot/BPM filter as a WHERE clause — adds a millisecond,
  acceptable.
- If we ever need multi-modal search across millions of users' libraries,
  we'd revisit. That's not this product.
