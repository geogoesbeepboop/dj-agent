"""Parked taste reviews — capture how I feel about a track before I own the file.

The taste loop's input (`dj.taste.tag`) needs the track to already be in the DB,
which needs the raw audio file. But the strongest taste signal happens *while
listening* — often on Spotify, with no file in hand. This module decouples
**capture** from **ingest**: a review left now is parked in `pending_taste` keyed
by track identity, and the Curator drains it into `tracks.taste_*` on the matching
ingest — so the note is "already ready" with no extra input (ADR 0008).

Matching is **confident-only** (the Curator auto-applies only sure matches):

  1. ISRC equality                                  → confident  (the gold key)
  2. normalized (artist, title) + duration within ±  → confident
  3. several name hits, or a duration mismatch       → ambiguous  (park; resolve by hand)
  4. nothing                                         → no match

The normalization + `find_match` core is pure (no DB), so the matching policy is
unit-testable with synthetic rows. The DB orchestration (`add`, `list_pending`,
`match`, `apply_review`, ...) is lazily psycopg2-backed and guarded on
`settings.db_enabled`, exactly like `vibe/store.py`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from dj.config import settings

DURATION_TOL_S = 5.0  # encode/master differences; wider gaps read as a different version


@dataclass
class PendingReview:
    """One parked review — identity to match on + the taste payload to apply."""

    artist: str
    title: str
    note: str
    id: int | None = None
    isrc: str | None = None
    spotify_id: str | None = None
    album: str | None = None
    duration_s: float | None = None
    rating: int | None = None
    role: str | None = None
    source: str | None = None
    status: str = "pending"
    matched_path: str | None = None
    captured_at: Any = None


@dataclass
class Candidate:
    """The identity of a track being ingested — what we match parked reviews against."""

    artist: str
    title: str
    isrc: str | None = None
    duration_s: float | None = None


@dataclass
class MatchResult:
    review: PendingReview
    confident: bool   # True → Curator may auto-apply; False → park for a manual resolve
    reason: str       # 'isrc' | 'name+duration' | 'duration-mismatch' | 'multiple-name'


# --- pure normalization + matching (no DB) ----------------------------------

_FEAT_RE = re.compile(r"\s*[\(\[]?\s*(?:feat\.?|ft\.?|featuring)\b.*$", re.IGNORECASE)
_PAREN_RE = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|;|/| x | vs\.? | with )\s*", re.IGNORECASE)
_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


def _basic(s: str) -> str:
    """Lowercase + strip diacritics (NFKD, drop combining marks)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def normalize_title(title: str) -> str:
    """Canonical title for matching: drop version qualifiers + punctuation.

    "Strobe (Radio Edit)" / "Strobe - 2011 Remaster" / "Strobe feat. X" all
    collapse to "strobe". Aggressive on purpose — Spotify and DJ files disagree on
    qualifiers constantly, and ISRC + duration + uniqueness guard the rare clash.
    """
    s = _basic(title).replace("&", " and ")
    s = _PAREN_RE.sub(" ", s)          # "(original mix)" / "[remix]"
    s = _FEAT_RE.sub(" ", s)           # trailing "feat. X"
    s = re.split(r"\s-\s", s)[0]       # " - radio edit" / " - 2011 remaster"
    return " ".join(_NONALNUM_RE.sub(" ", s).split())


def normalize_artist(artist: str) -> str:
    """Canonical primary artist: drop featured/collab credits + punctuation.

    Keeps single acts joined by '&'/'and' intact ("Above & Beyond" →
    "above and beyond") while cutting credit lists ("Calvin Harris, Dua Lipa" →
    "calvin harris"). '&'-joined collabs can still miss; ISRC covers those.
    """
    s = _basic(artist).replace("&", " and ")
    s = _FEAT_RE.sub(" ", s)
    s = _ARTIST_SPLIT_RE.split(s)[0]   # primary artist only
    return " ".join(_NONALNUM_RE.sub(" ", s).split())


def match_key(artist: str, title: str) -> tuple[str, str]:
    return normalize_artist(artist), normalize_title(title)


def existing_match(
    isrc: str | None, artist: str, title: str, rows: list[PendingReview]
) -> PendingReview | None:
    """The already-parked review for this identity, if any (ISRC, else name) — pure.

    Used to DEDUPE on capture: a second review of the same track updates that row
    rather than adding a duplicate that would later read as an ambiguous match.
    """
    key = match_key(artist, title)
    for r in rows:
        if isrc and _isrc_eq(r.isrc, isrc):
            return r
    for r in rows:
        if match_key(r.artist, r.title) == key:
            return r
    return None


def _isrc_eq(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a.strip().upper() == b.strip().upper()


def _dur_match(a: float | None, b: float | None, tol: float) -> bool:
    """True unless both durations are known and differ by more than tol."""
    if a is None or b is None:
        return True
    return abs(a - b) <= tol


def _latest(rows: list[PendingReview]) -> PendingReview:
    """Most recently captured row (by id; falls back to last)."""
    return max(rows, key=lambda r: (r.id or 0))


def find_match(
    candidate: Candidate,
    rows: list[PendingReview],
    *,
    duration_tol_s: float = DURATION_TOL_S,
) -> MatchResult | None:
    """Best parked review for an ingesting track, with a confidence verdict.

    Pure: pass the candidate's identity and the list of pending rows. ISRC wins
    outright; otherwise a unique name hit (with compatible duration) is confident
    and anything else is ambiguous (parked for a manual resolve).
    """
    if candidate.isrc:
        isrc_hits = [r for r in rows if _isrc_eq(r.isrc, candidate.isrc)]
        if isrc_hits:
            return MatchResult(_latest(isrc_hits), confident=True, reason="isrc")

    key = match_key(candidate.artist, candidate.title)
    name_hits = [r for r in rows if match_key(r.artist, r.title) == key]
    if not name_hits:
        return None

    dur_ok = [r for r in name_hits if _dur_match(r.duration_s, candidate.duration_s, duration_tol_s)]
    if len(dur_ok) == 1:
        return MatchResult(dur_ok[0], confident=True, reason="name+duration")
    if not dur_ok:  # single/multiple name hits, all with a duration that disagrees
        return MatchResult(_latest(name_hits), confident=False, reason="duration-mismatch")
    return MatchResult(_latest(name_hits), confident=False, reason="multiple-name")


# --- DB orchestration (skipped when DATABASE_URL is empty) ------------------


def _connect():
    if not settings.db_enabled:
        raise RuntimeError("DATABASE_URL not set — DB ops are disabled.")
    import psycopg2  # lazy

    return psycopg2.connect(settings.database_url)


def add(
    artist: str,
    title: str,
    note: str,
    *,
    isrc: str | None = None,
    spotify_id: str | None = None,
    album: str | None = None,
    duration_s: float | None = None,
    rating: int | None = None,
    role: str | None = None,
    source: str = "chat",
) -> int:
    """Park a review for a track I don't own yet. Returns the row id.

    Idempotent on identity: a second review of the same track (by ISRC or
    normalized name) updates the existing parked row — latest note wins — instead
    of stacking duplicates that would later read as an ambiguous match (#6).
    """
    dup = existing_match(isrc, artist, title, list_pending())
    with _connect() as conn, conn.cursor() as cur:
        if dup is not None and dup.id is not None:
            cur.execute(
                """UPDATE pending_taste SET
                       note = %s,
                       rating = COALESCE(%s, rating),
                       role = COALESCE(%s, role),
                       isrc = COALESCE(%s, isrc),
                       spotify_id = COALESCE(%s, spotify_id),
                       album = COALESCE(%s, album),
                       duration_s = COALESCE(%s, duration_s),
                       source = %s
                   WHERE id = %s""",
                (note, rating, role, isrc, spotify_id, album, duration_s, source, dup.id),
            )
            conn.commit()
            return dup.id
        cur.execute(
            """INSERT INTO pending_taste
                   (isrc, spotify_id, artist, title, album, duration_s,
                    note, rating, role, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (isrc, spotify_id, artist, title, album, duration_s,
             note, rating, role, source),
        )
        new_id = int(cur.fetchone()[0])
        conn.commit()
    return new_id


_SELECT = """SELECT id, isrc, spotify_id, artist, title, album, duration_s,
                    note, rating, role, source, status, matched_path, captured_at
             FROM pending_taste"""


def _row(r) -> PendingReview:
    return PendingReview(
        id=r[0], isrc=r[1], spotify_id=r[2], artist=r[3], title=r[4], album=r[5],
        duration_s=(None if r[6] is None else float(r[6])), note=r[7], rating=r[8],
        role=r[9], source=r[10], status=r[11], matched_path=r[12], captured_at=r[13],
    )


def list_pending() -> list[PendingReview]:
    """Every still-parked review (newest first) — the inbox / want-list."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"{_SELECT} WHERE status = 'pending' ORDER BY captured_at DESC")
        return [_row(r) for r in cur.fetchall()]


def get(review_id: int) -> PendingReview | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"{_SELECT} WHERE id = %s", (review_id,))
        r = cur.fetchone()
    return _row(r) if r else None


def match(
    isrc: str | None,
    artist: str,
    title: str,
    duration_s: float | None,
) -> MatchResult | None:
    """Find the parked review (if any) for a track the Curator is ingesting."""
    rows = list_pending()
    if not rows:
        return None
    return find_match(Candidate(artist=artist, title=title, isrc=isrc, duration_s=duration_s), rows)


def mark_applied(review_id: int, path: str) -> None:
    """Consume a parked review — it won't re-fire on the next ingest."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE pending_taste SET status = 'applied', matched_path = %s WHERE id = %s",
            (path, review_id),
        )
        conn.commit()


def apply_review(review: PendingReview, path: str) -> None:
    """Embed a parked review's note and write it onto a track as a manual label.

    The single apply path shared by the Curator (auto, after a confident match)
    and the CLI's `--apply` (a deliberate manual resolve). It reuses
    `store.set_taste`, so a parked review lands identically to one typed into
    `dj.taste.tag` — `taste_source='manual'` — then is marked applied.
    """
    from dj.taste import embed
    from dj.vibe import store

    store.set_taste(path, review.note, embed.embed_note(review.note),
                    rating=review.rating, role=review.role)
    if review.id is not None:
        mark_applied(review.id, path)


def apply_manual(review_id: int, path: str) -> bool:
    """Bind a parked review to an already-ingested track by hand (resolve ambiguity)."""
    review = get(review_id)
    if review is None:
        return False
    apply_review(review, path)
    return True
