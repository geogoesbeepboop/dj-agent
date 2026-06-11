"""Blended ranking: combine the general (acoustic) and personal (taste) signals.

Pure math over already-fetched similarities — no DB, no model — so it's fully
unit-testable with synthetic numbers. `store.ranked()` does the retrieval and
hands per-candidate similarities here to produce the final order.

    score = α·acoustic_sim + β·(confidence · taste_sim) + γ·(rating / 5)

Cosine sims are in [-1, 1]; rating is 1..5 or None. A candidate with no taste
vector yet (taste_sim/taste_source None) contributes 0 to the taste term, so
ranking falls back to acoustic — the right cold-start behavior before I've
tagged much. A *propagated* taste vector counts for less than one I wrote myself.
"""

from __future__ import annotations

from dataclasses import dataclass

from dj.config import TASTE_WEIGHTS

# A guess from my neighbors is a weaker signal than a label I actually wrote. The
# 0.5 is only the *default* for a propagated row; a row that carries its own
# measured propagation confidence uses that instead (see score()).
_CONFIDENCE = {"manual": 1.0, "propagated": 0.5}


@dataclass(frozen=True)
class BlendWeights:
    acoustic: float = TASTE_WEIGHTS[0]
    taste: float = TASTE_WEIGHTS[1]
    rating: float = TASTE_WEIGHTS[2]


@dataclass
class Candidate:
    path: str
    acoustic_sim: float                 # cosine similarity to the acoustic query
    taste_sim: float | None = None      # cosine similarity to the taste query
    rating: int | None = None           # 1..5
    taste_source: str | None = None     # 'manual' | 'propagated' | None
    taste_confidence: float | None = None  # measured propagation confidence (0..1)


def score(c: Candidate, w: BlendWeights | None = None) -> float:
    """Blended score for one candidate (higher = better fit)."""
    w = w or BlendWeights()
    confidence = _CONFIDENCE.get(c.taste_source, 0.0)
    if c.taste_source == "propagated" and c.taste_confidence is not None:
        confidence = c.taste_confidence       # how sure propagation was, per track
    taste_term = w.taste * confidence * (c.taste_sim or 0.0)
    rating_term = w.rating * ((c.rating or 0) / 5.0)
    return w.acoustic * c.acoustic_sim + taste_term + rating_term


def rank(candidates: list[Candidate], w: BlendWeights | None = None) -> list[Candidate]:
    """Candidates ordered best-first by blended score (stable on ties)."""
    w = w or BlendWeights()
    return sorted(candidates, key=lambda c: score(c, w), reverse=True)
