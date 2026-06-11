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


@dataclass
class SectionInput:
    """One section to persist alongside its track (the Curator assembles these)."""

    idx: int
    label: str
    start_s: float
    end_s: float
    embedding: np.ndarray            # CLAP vibe of this part
    start_beat: int | None = None
    bars: int | None = None
    energy_lufs: float | None = None
    camelot: str | None = None
    is_mixin: bool = False
    is_mixout: bool = False
    loopable: bool = False


@dataclass
class SectionNeighbor:
    """A section returned by transition search — enough to cue and mix on it."""

    path: str
    track_id: int
    idx: int
    label: str
    start_s: float
    end_s: float
    start_beat: int | None
    bpm: float
    camelot: str
    energy_lufs: float | None
    distance: float


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
    sections: list[SectionInput] | None = None,
    source: str = "local",
    is_favorite: bool = False,
) -> None:
    """Insert/replace one analyzed track + its CLAP vector + metadata + sections.

    The track row and its sections are written in one transaction: the track is
    upserted ON CONFLICT (path), then its old sections are deleted and the new
    set inserted. Re-ingest is therefore idempotent — the same file always lands
    as one track row and a fresh, ordered set of section rows (ADR 0004).
    """
    vec = _to_pgvector(embedding)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tracks
                (path, source, duration_s, bpm, camelot, loudness_lufs, energy_curve,
                 title, artist, genre, isrc, tags, is_favorite, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (path) DO UPDATE SET
                source        = EXCLUDED.source,
                duration_s    = EXCLUDED.duration_s,
                bpm           = EXCLUDED.bpm,
                camelot       = EXCLUDED.camelot,
                loudness_lufs = EXCLUDED.loudness_lufs,
                energy_curve  = EXCLUDED.energy_curve,
                title         = EXCLUDED.title,
                artist        = EXCLUDED.artist,
                genre         = EXCLUDED.genre,
                -- ISRC is the recording's immutable id: keep a known one if a later
                -- re-ingest (e.g. a local file with no tag) comes in without it.
                isrc          = COALESCE(EXCLUDED.isrc, tracks.isrc),
                tags          = EXCLUDED.tags,
                -- Favorite is a taste signal (ADR 0003): make it sticky so a plain
                -- re-ingest (without --favorites) can't silently un-favorite a track.
                is_favorite   = tracks.is_favorite OR EXCLUDED.is_favorite,
                embedding     = EXCLUDED.embedding
            RETURNING id
            """,
            (
                features.path,
                source,
                features.duration_s,
                features.bpm,
                features.camelot,
                features.loudness_lufs,
                features.energy_curve,          # psycopg2 adapts list → REAL[]
                tags.title or None,
                tags.artist or None,
                tags.genre or None,
                tags.isrc or None,
                tags.tags or None,              # list → TEXT[]
                is_favorite,
                vec,
            ),
        )
        track_id = cur.fetchone()[0]
        cur.execute("DELETE FROM sections WHERE track_id = %s", (track_id,))
        for s in sections or []:
            cur.execute(
                """
                INSERT INTO sections
                    (track_id, idx, label, start_s, end_s, start_beat, bars,
                     energy_lufs, camelot, is_mixin, is_mixout, loopable, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    track_id, s.idx, s.label, s.start_s, s.end_s, s.start_beat,
                    s.bars, s.energy_lufs, s.camelot, s.is_mixin, s.is_mixout,
                    s.loopable, _to_pgvector(s.embedding),
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


def nearest_section(
    embedding: np.ndarray,
    k: int = 10,
    filters: dict[str, Any] | None = None,
    mixin: bool | None = None,
    mixout: bool | None = None,
    exclude_path: str | None = None,
) -> list[SectionNeighbor]:
    """Section-level vibe search — "what part mixes cleanly out of A's outro?".

    Ranks sections by cosine proximity to a query section vector, behind the hard
    mixing filters (BPM band, Camelot set on the parent track) plus section mix
    flags. `mixin=True` restricts to clean entry points — the Mixer's other half
    of a transition (ADR 0004; the query in docs/database.md).
    """
    vec = _to_pgvector(embedding)
    clauses: list[str] = []
    args: list[Any] = [vec]
    if filters and "bpm" in filters:
        lo, hi = filters["bpm"]
        clauses.append("t.bpm BETWEEN %s AND %s")
        args.extend([lo, hi])
    if filters and "camelot" in filters:
        clauses.append("t.camelot = ANY(%s)")
        args.append(list(filters["camelot"]))
    if mixin is not None:
        clauses.append("s.is_mixin = %s")
        args.append(mixin)
    if mixout is not None:
        clauses.append("s.is_mixout = %s")
        args.append(mixout)
    if exclude_path:
        clauses.append("t.path <> %s")
        args.append(exclude_path)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    args.append(k)
    # arg order matches the placeholders left-to-right: SELECT distance vec,
    # then the WHERE-clause args, then LIMIT k.
    sql = f"""
        SELECT t.path, s.track_id, s.idx, s.label, s.start_s, s.end_s,
               s.start_beat, t.bpm, t.camelot, s.energy_lufs,
               s.embedding <=> %s AS distance
        FROM sections s
        JOIN tracks t ON t.id = s.track_id
        {where}
        ORDER BY distance
        LIMIT %s
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return [
            SectionNeighbor(
                path=r[0], track_id=r[1], idx=r[2], label=r[3], start_s=float(r[4]),
                end_s=float(r[5]), start_beat=r[6], bpm=float(r[7]), camelot=r[8],
                energy_lufs=(None if r[9] is None else float(r[9])), distance=float(r[10]),
            )
            for r in cur.fetchall()
        ]


def get_cards(paths: list[str]) -> dict[str, dict[str, Any]]:
    """Batch-fetch the display + mixing fields for a set of paths (one query).

    The Selector ranks tracks with `ranked()` (which returns paths + scores) then
    needs each track's BPM/Camelot/LUFS to order them to the arc — this avoids an
    N+1 of `get_track` (which would also drag back the 512-d embedding).
    """
    if not paths:
        return {}
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT path, title, artist, bpm, camelot, loudness_lufs, rating, taste_source
               FROM tracks WHERE path = ANY(%s)""",
            (list(paths),),
        )
        rows = cur.fetchall()
    return {
        r[0]: {
            "title": r[1] or "", "artist": r[2] or "", "bpm": float(r[3]),
            "camelot": r[4], "lufs": float(r[5]), "rating": r[6], "taste_source": r[7],
        }
        for r in rows
    }


def track_durations(paths: list[str]) -> dict[str, float]:
    """Batch-fetch duration_s for a set of paths — the Rekordbox export's TotalTime."""
    if not paths:
        return {}
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT path, duration_s FROM tracks WHERE path = ANY(%s)", (list(paths),)
        )
        return {r[0]: float(r[1]) for r in cur.fetchall()}


def find_track_by_meta(artist: str, title: str, isrc: str | None = None) -> str | None:
    """Path of the library track for a judged item — ISRC first, then artist+title.

    The bulk-judging CLI uses this to tag a track I already own directly instead
    of parking a pending review. ISRC is the recording's global id (ADR 0008), so
    when the judged item carries one (Spotify always does) it wins even if the
    titles differ ('Obsesión' vs 'Obsesion (feat. …)', remasters, casing). Only if
    there's no ISRC, or it matches nothing, do we fall back to a unique
    case-insensitive artist+title. Ambiguity returns None — parking is the safe
    fallback, never a guess."""
    with _connect() as conn, conn.cursor() as cur:
        if isrc and isrc.strip():
            cur.execute(
                "SELECT path FROM tracks WHERE isrc = %s ORDER BY id LIMIT 1",
                (isrc.strip(),),
            )
            row = cur.fetchone()
            if row:
                return row[0]
        if not (artist.strip() and title.strip()):
            return None
        cur.execute(
            """SELECT path FROM tracks
               WHERE lower(artist) = lower(%s) AND lower(title) = lower(%s)""",
            (artist.strip(), title.strip()),
        )
        rows = cur.fetchall()
    return rows[0][0] if len(rows) == 1 else None


def favorite_taste_vectors() -> list[np.ndarray]:
    """Taste vectors of my hand-labeled tracks — the eval taste-match reference set.

    "The ones I love" (BUILD_PLAN's taste-match metric) = manually tagged tracks;
    propagated guesses are excluded so the reference stays my real signal.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT taste_vec FROM tracks WHERE taste_source = 'manual' AND taste_vec IS NOT NULL"
        )
        return [_parse_vec(r[0]) for r in cur.fetchall()]


def taste_eval_rows(paths: list[str]) -> dict[str, dict[str, Any]]:
    """Per-path taste vector + source + favorite flag — the eval scorecard inputs."""
    if not paths:
        return {}
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT path, taste_vec, taste_source, is_favorite FROM tracks WHERE path = ANY(%s)",
            (list(paths),),
        )
        rows = cur.fetchall()
    return {
        r[0]: {
            "taste_vec": (None if r[1] is None else _parse_vec(r[1])),
            "taste_source": r[2],
            "is_favorite": bool(r[3]),
        }
        for r in rows
    }


def get_sections(path: str) -> list[SectionNeighbor]:
    """Every section of one track, in order — for the Selector and the Mixer."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT t.path, s.track_id, s.idx, s.label, s.start_s, s.end_s,
                      s.start_beat, t.bpm, t.camelot, s.energy_lufs
               FROM sections s JOIN tracks t ON t.id = s.track_id
               WHERE t.path = %s ORDER BY s.idx""",
            (path,),
        )
        return [
            SectionNeighbor(
                path=r[0], track_id=r[1], idx=r[2], label=r[3], start_s=float(r[4]),
                end_s=float(r[5]), start_beat=r[6], bpm=float(r[7]), camelot=r[8],
                energy_lufs=(None if r[9] is None else float(r[9])), distance=0.0,
            )
            for r in cur.fetchall()
        ]


# --- taste layer (ADR 0003) -------------------------------------------------


@dataclass
class Track:
    """A track row with what the tagging CLI needs: display + acoustic vec + taste."""

    path: str
    title: str
    artist: str
    genre: str
    bpm: float
    camelot: str
    embedding: np.ndarray            # CLAP acoustic vector
    taste_note: str | None
    rating: int | None
    role: str | None
    taste_source: str | None


@dataclass
class TasteRow:
    """A manually-tagged track's vectors + rating — the input to propagation."""

    path: str
    acoustic: np.ndarray
    taste: np.ndarray
    rating: float


def get_track(path: str) -> Track | None:
    """Fetch one track (display fields + acoustic embedding + current taste)."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT path, title, artist, genre, bpm, camelot, embedding,
                      taste_note, rating, role, taste_source
               FROM tracks WHERE path = %s""",
            (path,),
        )
        r = cur.fetchone()
    if r is None:
        return None
    return Track(
        path=r[0], title=r[1] or "", artist=r[2] or "", genre=r[3] or "",
        bpm=r[4], camelot=r[5], embedding=_parse_vec(r[6]),
        taste_note=r[7], rating=r[8], role=r[9], taste_source=r[10],
    )


def set_taste(
    path: str,
    note: str,
    taste_vec: np.ndarray,
    rating: int | None = None,
    role: str | None = None,
) -> None:
    """Write my manual taste signal for one track (the tagging CLI's sink)."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE tracks
                  SET taste_note = %s, taste_vec = %s, rating = %s,
                      role = %s, taste_source = 'manual'
                WHERE path = %s""",
            (note, _to_pgvector(taste_vec), rating, role, path),
        )
        conn.commit()


def set_propagated_taste(
    path: str, taste_vec: np.ndarray, rating: int | None, confidence: float | None = None
) -> None:
    """Write a provisional (propagated) taste vector — never clobbers a manual one."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE tracks
                  SET taste_vec = %s, rating = %s, taste_confidence = %s,
                      taste_source = 'propagated'
                WHERE path = %s AND COALESCE(taste_source, '') <> 'manual'""",
            (_to_pgvector(taste_vec), rating, confidence, path),
        )
        conn.commit()


def tagged_corpus() -> list[TasteRow]:
    """Every manually-tagged track's (acoustic, taste, rating) — for propagation."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT path, embedding, taste_vec, rating FROM tracks
               WHERE taste_source = 'manual' AND taste_vec IS NOT NULL"""
        )
        rows = cur.fetchall()
    return [
        TasteRow(path=r[0], acoustic=_parse_vec(r[1]), taste=_parse_vec(r[2]),
                 rating=float(r[3] or 0))
        for r in rows
    ]


def untagged() -> list[tuple[str, np.ndarray]]:
    """(path, acoustic vector) for every track without a manual taste label."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT path, embedding FROM tracks
               WHERE COALESCE(taste_source, '') <> 'manual'"""
        )
        rows = cur.fetchall()
    return [(r[0], _parse_vec(r[1])) for r in rows]


def nearest_taste(
    taste_vec: np.ndarray, k: int = 10, filters: dict[str, Any] | None = None
) -> list[Neighbor]:
    """Rank tracks by my taste vector (only those that have one)."""
    vec = _to_pgvector(taste_vec)
    where, where_args = _build_where(None, filters)
    where = f"{where} AND taste_vec IS NOT NULL" if where else "WHERE taste_vec IS NOT NULL"
    sql = f"""
        SELECT path, bpm, camelot, title, artist, genre,
               taste_vec <=> %s AS distance
        FROM tracks
        {where}
        ORDER BY distance
        LIMIT %s
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, [vec, *where_args, k])
        return [
            Neighbor(path=r[0], bpm=r[1], camelot=r[2], title=r[3] or "",
                     artist=r[4] or "", genre=r[5] or "", distance=float(r[6]))
            for r in cur.fetchall()
        ]


def ranked(
    acoustic_vec: np.ndarray,
    taste_vec: np.ndarray,
    weights=None,
    filters: dict[str, Any] | None = None,
    k: int = 10,
    pool: int = 200,
):
    """Blended retrieval: hard-filter + acoustic candidate pool, rerank by taste.

    Returns score.Candidate objects ordered best-first. pgvector computes both
    cosine sims; the blend policy (weights, confidence, cold-start) lives in
    taste/score.py so it's unit-testable without a DB.
    """
    from dj.taste.score import BlendWeights, Candidate, rank

    av, tv = _to_pgvector(acoustic_vec), _to_pgvector(taste_vec)
    where, where_args = _build_where(None, filters)
    sql = f"""
        SELECT path, rating, taste_source, taste_confidence,
               1 - (embedding <=> %s) AS acoustic_sim,
               CASE WHEN taste_vec IS NULL THEN NULL
                    ELSE 1 - (taste_vec <=> %s) END AS taste_sim
        FROM tracks
        {where}
        ORDER BY embedding <=> %s
        LIMIT %s
    """
    args = [av, tv, *where_args, av, pool]
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    candidates = [
        Candidate(
            path=r[0], rating=r[1], taste_source=r[2],
            taste_confidence=(None if r[3] is None else float(r[3])),
            acoustic_sim=float(r[4]),
            taste_sim=(None if r[5] is None else float(r[5])),
        )
        for r in rows
    ]
    return rank(candidates, weights or BlendWeights())[:k]


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
        if filters.get("exclude_paths"):
            clauses.append("path <> ALL(%s)")          # drop recently-played tracks (#24)
            args.append(list(filters["exclude_paths"]))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, args


def _to_pgvector(embedding: np.ndarray) -> str:
    """pgvector accepts a string like '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in np.asarray(embedding, dtype=float)) + "]"


def _parse_vec(raw: Any) -> np.ndarray:
    """pgvector comes back as a '[a,b,...]' string (no adapter) → float32 ndarray."""
    if isinstance(raw, (list, tuple, np.ndarray)):
        return np.asarray(raw, dtype=np.float32)
    body = raw.strip().lstrip("[").rstrip("]")
    return np.array([float(x) for x in body.split(",") if x], dtype=np.float32)
