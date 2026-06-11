"""Spread sparse taste labels across the library + surface what to label next.

I tag ~150 favorites; CLAP's acoustic neighborhood carries those labels to the
rest (ADR 0003). The math here is pure numpy over already-fetched vectors (no
DB), so it's unit-testable; `propagate_library()` / `labeling_queue()` are the
thin DB-driven orchestrators that pull vectors via `store` and call it.

All vectors are assumed L2-normalized (clap.py and taste/embed.py both
normalize), so a dot product *is* cosine similarity.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dj.config import VIBE_DIM


@dataclass
class Propagated:
    taste_vec: np.ndarray   # similarity-weighted average of neighbor taste vectors
    rating: float           # similarity-weighted average neighbor rating
    confidence: float       # weighted mean neighbor similarity (0..1)


def _topk_sims(
    target: np.ndarray, neighbors: np.ndarray, k: int
) -> tuple[np.ndarray, np.ndarray]:
    """(indices of the k most similar neighbors, all cosine sims) — descending."""
    sims = neighbors @ target
    return np.argsort(-sims)[:k], sims


def provisional_taste(
    target: np.ndarray,
    neighbor_acoustic: np.ndarray,   # (n, VIBE_DIM)
    neighbor_taste: np.ndarray,      # (n, TASTE_DIM)
    neighbor_ratings: np.ndarray,    # (n,)
    k: int = 8,
) -> Propagated | None:
    """A provisional taste vector for an untagged track from its tagged neighbors.

    Returns None when no neighbor is positively similar (nothing to learn from).
    """
    if len(neighbor_acoustic) == 0:
        return None
    idx, sims = _topk_sims(target, neighbor_acoustic, k)
    w = np.clip(sims[idx], 0.0, None)
    if w.sum() < 1e-9:
        return None
    w = w / w.sum()
    vec = (neighbor_taste[idx] * w[:, None]).sum(axis=0)
    norm = np.linalg.norm(vec)
    if norm > 1e-9:
        vec = vec / norm
    rating = float((neighbor_ratings[idx] * w).sum())
    confidence = float((sims[idx] * w).sum())
    return Propagated(vec.astype(np.float32), rating, confidence)


def uncertainty(
    target: np.ndarray,
    tagged_acoustic: np.ndarray,     # (n, VIBE_DIM)
    tagged_ratings: np.ndarray,      # (n,)
    k: int = 8,
) -> float:
    """How much labeling this track would help (0..1, higher = label it sooner).

    High when the track has few/distant tagged neighbors (we can't propagate
    confidently) or when those neighbors *disagree* on rating (taste is unsettled
    in this region of the library).
    """
    if len(tagged_acoustic) == 0:
        return 1.0
    idx, sims = _topk_sims(target, tagged_acoustic, k)
    closeness = float(np.clip(sims[idx].mean(), 0.0, 1.0))
    disagreement = float(np.std(tagged_ratings[idx]) / 4.0) if len(idx) > 1 else 0.0
    return float(np.clip((1.0 - closeness) + disagreement, 0.0, 1.0))


# --- DB-driven orchestration (skipped when DATABASE_URL is empty) -----------


def propagate_library(k: int = 8) -> int:
    """Give every untagged track a provisional taste vector. Returns count updated."""
    from dj.vibe import store

    corpus = store.tagged_corpus()
    if not corpus:
        return 0
    acoustic = np.stack([c.acoustic for c in corpus])
    taste = np.stack([c.taste for c in corpus])
    ratings = np.array([c.rating for c in corpus], dtype=np.float32)
    done = 0
    for path, vec in store.untagged():
        p = provisional_taste(vec, acoustic, taste, ratings, k=k)
        if p is not None:
            store.set_propagated_taste(path, p.taste_vec, round(p.rating) or None, confidence=p.confidence)
            done += 1
    return done


def labeling_queue(n: int = 20, k: int = 8) -> list[str]:
    """The next tracks worth labeling: highest acoustic-uncertainty untagged tracks."""
    from dj.vibe import store

    corpus = store.tagged_corpus()
    acoustic = (
        np.stack([c.acoustic for c in corpus])
        if corpus
        else np.zeros((0, VIBE_DIM), dtype=np.float32)
    )
    ratings = np.array([c.rating for c in corpus], dtype=np.float32)
    scored = [(uncertainty(vec, acoustic, ratings, k=k), path) for path, vec in store.untagged()]
    scored.sort(reverse=True)
    return [path for _, path in scored[:n]]
