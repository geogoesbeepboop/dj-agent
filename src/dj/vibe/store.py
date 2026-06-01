"""The vibe vector store: upsert tracks and run cosine-similarity "vibe search".

pgvector via psycopg2 (both lazily imported). Everything guards on
settings.db_enabled, so the module imports and unit-tests run with no DB.

The one interesting query is `nearest()`: pgvector's `<=>` operator is cosine
distance, so `ORDER BY embedding <=> query LIMIT k` returns the k most
vibe-similar tracks. That single line is the "gold mine" the agent queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from dj.audio.analyze import TrackFeatures
from dj.config import settings

_SCHEMA_FILE = Path(__file__).with_name("schema.sql")


@dataclass
class Neighbor:
    path: str
    bpm: float
    camelot: str
    distance: float  # cosine distance (0 = identical vibe)


def _connect():
    if not settings.db_enabled:
        raise RuntimeError("DATABASE_URL not set — DB ops are disabled.")
    import psycopg2  # lazy

    return psycopg2.connect(settings.database_url)


def ensure_schema() -> None:
    """Create the extension, table, and index if absent."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(_SCHEMA_FILE.read_text())
        conn.commit()


def upsert_track(features: TrackFeatures, embedding: np.ndarray, is_favorite: bool = False) -> None:
    """Insert/replace one analyzed track + its vibe vector."""
    vec = _to_pgvector(embedding)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tracks (path, duration_s, bpm, camelot, energy_mean, is_favorite, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (path) DO UPDATE SET
                duration_s = EXCLUDED.duration_s,
                bpm        = EXCLUDED.bpm,
                camelot    = EXCLUDED.camelot,
                energy_mean= EXCLUDED.energy_mean,
                embedding  = EXCLUDED.embedding
            """,
            (
                features.path,
                features.duration_s,
                features.bpm,
                features.camelot,
                features.energy_mean,
                is_favorite,
                vec,
            ),
        )
        conn.commit()


def nearest(embedding: np.ndarray, k: int = 10, exclude_path: str | None = None) -> list[Neighbor]:
    """Return the k most vibe-similar tracks by cosine distance."""
    vec = _to_pgvector(embedding)
    if exclude_path:
        where, args = "WHERE path <> %s", (vec, exclude_path, k)
    else:
        where, args = "", (vec, k)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT path, bpm, camelot, embedding <=> %s AS distance
            FROM tracks
            {where}
            ORDER BY distance
            LIMIT %s
            """,
            args,
        )
        return [Neighbor(path=p, bpm=b, camelot=c, distance=float(d)) for p, b, c, d in cur.fetchall()]


def count() -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM tracks")
        return int(cur.fetchone()[0])


def _to_pgvector(embedding: np.ndarray) -> str:
    """pgvector accepts a string like '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in np.asarray(embedding, dtype=float)) + "]"
