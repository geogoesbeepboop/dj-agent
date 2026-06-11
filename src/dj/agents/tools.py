"""The agent toolbelt — retrieval, features, harmonic checks, transition scoring.

These are plain Python functions over the deterministic layers (`store`,
`camelot`, `critic`). The Selector's revise loop calls them directly today; the
same functions are what a Claude Agent SDK in-process MCP server would expose as
tools (`query_vibe_db`, `get_sections`, `check_harmonic_compat`,
`score_transition`) when we move the loop into the SDK. Keeping them as a thin,
importable seam means the planning logic is testable without an agent runtime
(ADR 0006).

Everything that touches the DB or a model is lazy + guarded, so importing this
module is free and the fast tests never spin up Postgres or CLAP.
"""

from __future__ import annotations

from dataclasses import dataclass

from dj.audio import camelot
from dj.plan import Slot


@dataclass
class TrackCard:
    """A retrieved candidate: enough to rank, order to an arc, and display."""

    path: str
    title: str
    artist: str
    bpm: float
    camelot: str
    lufs: float
    score: float                 # blended acoustic+taste+rating retrieval score
    rating: int | None = None
    taste_source: str | None = None

    def to_slot(self, position: float) -> Slot:
        return Slot(
            position=position, path=self.path, bpm=self.bpm, camelot=self.camelot,
            lufs=self.lufs, title=self.title, artist=self.artist, taste_score=self.score,
        )


def query_vibe_db(
    prompt: str | None = None,
    *,
    filters: dict | None = None,
    k: int = 60,
    weights=None,
    acoustic_vec=None,
    taste_vec=None,
) -> list[TrackCard]:
    """Blended candidate retrieval for a vibe brief (the Selector's pool).

    The same `prompt` drives BOTH query vectors: CLAP's text encoder for the
    acoustic side ("what it should sound like") and the taste sentence-model for
    the personal side ("notes like this" → tracks I'd describe this way). Pass
    `acoustic_vec`/`taste_vec` directly to skip the model calls (tests, replays).
    """
    from dj.taste.score import BlendWeights
    from dj.vibe import store

    if acoustic_vec is None or taste_vec is None:
        if prompt is None:
            raise ValueError("query_vibe_db needs a prompt or explicit query vectors")
        if acoustic_vec is None:
            from dj.vibe import clap

            acoustic_vec = clap.embed_text(prompt)
        if taste_vec is None:
            from dj.taste import embed

            taste_vec = embed.embed_note(prompt)

    w = weights or BlendWeights()
    candidates = store.ranked(acoustic_vec, taste_vec, weights=w, filters=filters, k=k)
    cards = store.get_cards([c.path for c in candidates])
    out: list[TrackCard] = []
    for c in candidates:
        f = cards.get(c.path)
        if f is None:
            continue
        out.append(TrackCard(
            path=c.path, title=f["title"], artist=f["artist"], bpm=f["bpm"],
            camelot=f["camelot"], lufs=f["lufs"], score=_blended_score(c, w),
            rating=f["rating"], taste_source=f["taste_source"],
        ))
    return out


def get_sections(path: str):
    """Every section of a track (the Selector's partial-track / cue choices)."""
    from dj.vibe import store

    return store.get_sections(path)


def check_harmonic_compat(a_camelot: str, b_camelot: str) -> dict:
    """Hard harmonic-mixing check + a soft distance (the Selector's key filter)."""
    return {
        "compatible": camelot.compatible(a_camelot, b_camelot),
        "distance": camelot.distance(a_camelot, b_camelot),
    }


def score_transition(a: Slot, b: Slot, thresholds=None):
    """Deterministic A→B transition score (delegates to the Critic)."""
    from dj.critic import transition

    return transition(a, b, thresholds)


def _blended_score(candidate, weights=None) -> float:
    """The blended score for one ranked candidate (re-uses taste/score policy).

    Uses the *same* weights the pool was ranked with, so a custom blend (e.g. a
    discovery-leaning query) is reflected in the score the greedy selector and the
    HITL gate read — not silently reset to the defaults."""
    from dj.taste.score import BlendWeights, score

    return score(candidate, weights or BlendWeights())


# --- taste capture (ADR 0008): park a review before I own the file -----------


def save_review(
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
    """Park a taste review for a track I don't own yet — the agent's capture sink.

    Both capture flows land here: "review what's playing" (the agent reads the
    Spotify MCP `get_currently_playing` for artist/title/isrc/duration, then calls
    this with source='spotify_now') and conversational chat (the agent parses my
    sentence, source='chat' — still works if the Spotify MCP ever changes). The
    Curator drains it into the track's taste on the matching ingest. Returns the
    new pending row id. See `dj/taste/pending.py`.
    """
    from dj.taste import pending

    return pending.add(
        artist, title, note, isrc=isrc, spotify_id=spotify_id, album=album,
        duration_s=duration_s, rating=rating, role=role, source=source,
    )


def list_pending_reviews():
    """Still-parked reviews — the inbox, and a want-list of music to go acquire."""
    from dj.taste import pending

    return pending.list_pending()
