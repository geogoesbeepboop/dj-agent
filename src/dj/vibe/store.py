"""The vibe vector store: upsert tracks and run cosine-similarity "vibe search".

pgvector via psycopg2 (both lazily imported). Everything guards on
settings.db_enabled, so the module imports and unit-tests run with no DB.

Two kinds of search, both backed by pgvector's `<=>` cosine-distance operator:
  - nearest(vector)      — "tracks like this seed track" (audio→audio)
  - nearest_to_text(str) — "tracks that feel like this phrase" (text→audio),
                           the day-one payoff of CLAP's shared text+audio space.
Both accept structured `filters` (bpm range, Camelot set, favorites) applied as
SQL WHERE clauses — hard mixing constraints layered on top of the soft vibe rank.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from dj.audio.analyze import TrackFeatures
from dj.config import settings
from dj.metadata import TrackTags

_SCHEMA_FILE = Path(__file__).with_name("schema.sql")


@dataclass
class Neighbor:
    path: str
    bpm: float
    camelot: str
    title: str
    artist: str
    genre: str
    distance: float  # cosine distance (0 = identical vibe)


def _connect():
    if not settings.db_enabled:
        raise RuntimeError("DATABASE_URL not set — DB ops are disabled.")
    import psycopg2  # lazy

    return psycopg2.connect(settings.database_url)


def ensure_schema() -> None:
    """Create the extension, table, and indexes if absent."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(_SCHEMA_FILE.read_text())
        conn.commit()


def upsert_track(
    features: TrackFeatures,
    tags: TrackTags,
    embedding: np.ndarray,
    source: str = "local",
    is_favorite: bool = False,
) -> None:
    """Insert/replace one analyzed track + its CLAP vibe vector + metadata."""
    vec = _to_pgvector(embedding)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tracks
                (path, source, duration_s, bpm, camelot, energy_mean, energy_curve,
                 title, artist, genre, tags, is_favorite, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (path) DO UPDATE SET
                source       = EXCLUDED.source,
                duration_s   = EXCLUDED.duration_s,
                bpm          = EXCLUDED.bpm,
                camelot      = EXCLUDED.camelot,
                energy_mean  = EXCLUDED.energy_mean,
                energy_curve = EXCLUDED.energy_curve,
                title        = EXCLUDED.title,
                artist       = EXCLUDED.artist,
                genre        = EXCLUDED.genre,
                tags         = EXCLUDED.tags,
                is_favorite  = EXCLUDED.is_favorite,
                embedding    = EXCLUDED.embedding
            """,
            (
                features.path,
                source,
                features.duration_s,
                features.bpm,
                features.camelot,
                features.energy_mean,
                features.energy_curve,          # psycopg2 adapts list → REAL[]
                tags.title or None,
                tags.artist or None,
                tags.genre or None,
                tags.tags or None,              # list → TEXT[]
                is_favorite,
                vec,
            ),
        )
        conn.commit()


def nearest(
    embedding: np.ndarray,
    k: int = 10,
    exclude_path: str | None = None,
    filters: dict[str, Any] | None = None,
) -> list[Neighbor]:
    """Return the k most vibe-similar tracks by cosine distance, after filters."""
    vec = _to_pgvector(embedding)
    where, where_args = _build_where(exclude_path, filters)
    args = [vec, *where_args, k]
    sql = f"""
        SELECT path, bpm, camelot, title, artist, genre,
               embedding <=> %s AS distance
        FROM tracks
        {where}
        ORDER BY distance
        LIMIT %s
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return [
            Neighbor(
                path=r[0], bpm=r[1], camelot=r[2], title=r[3] or "",
                artist=r[4] or "", genre=r[5] or "", distance=float(r[6]),
            )
            for r in cur.fetchall()
        ]


def nearest_to_text(
    text: str, k: int = 10, filters: dict[str, Any] | None = None
) -> list[Neighbor]:
    """Vibe search from a text prompt ("dreamy nocturnal") via CLAP's text encoder."""
    from dj.vibe import clap  # lazy: only this path needs torch/transformers

    return nearest(clap.embed_text(text), k=k, filters=filters)


def count() -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM tracks")
        return int(cur.fetchone()[0])


def _build_where(
    exclude_path: str | None, filters: dict[str, Any] | None
) -> tuple[str, list[Any]]:
    """Assemble a parameterized WHERE clause from optional filters.

    filters: {"bpm": (lo, hi), "camelot": {"8A", "9A"}, "is_favorite": True}
    """
    clauses: list[str] = []
    args: list[Any] = []
    if exclude_path:
        clauses.append("path <> %s")
        args.append(exclude_path)
    if filters:
        if "bpm" in filters:
            lo, hi = filters["bpm"]
            clauses.append("bpm BETWEEN %s AND %s")
            args.extend([lo, hi])
        if "camelot" in filters:
            clauses.append("camelot = ANY(%s)")
            args.append(list(filters["camelot"]))
        if filters.get("is_favorite"):
            clauses.append("is_favorite = TRUE")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, args


def _to_pgvector(embedding: np.ndarray) -> str:
    """pgvector accepts a string like '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in np.asarray(embedding, dtype=float)) + "]"
