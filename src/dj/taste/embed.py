"""Embed my free-text taste notes into the (TASTE_DIM,) taste space.

A small local sentence-transformer — a *different* space from CLAP (it captures
my words, not the audio). Imported lazily so the package and the fast test suite
load without the dependency or the model download (mirrors vibe/clap.py).

Vectors are L2-normalized (`normalize_embeddings=True`) so cosine distance in
pgvector behaves the same way it does for the CLAP vectors.
"""

from __future__ import annotations

import numpy as np

from dj.config import TASTE_DIM, TASTE_MODEL

_model = None


def _load():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(TASTE_MODEL)
    return _model


def embed_note(text: str) -> np.ndarray:
    """Encode one note into a (TASTE_DIM,) float32 L2-normalized taste vector."""
    return embed_notes([text])[0]


def embed_notes(texts: list[str]) -> np.ndarray:
    """Encode a batch of notes into an (N, TASTE_DIM) float32 array."""
    model = _load()
    vecs = model.encode(list(texts), normalize_embeddings=True, convert_to_numpy=True)
    vecs = np.asarray(vecs, dtype=np.float32)
    if vecs.ndim != 2 or vecs.shape[1] != TASTE_DIM:
        raise ValueError(
            f"taste model returned shape {vecs.shape}, expected (*, {TASTE_DIM})"
        )
    return vecs
