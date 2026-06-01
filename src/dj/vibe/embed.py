"""TrackFeatures → a fixed-length, L2-normalized "vibe vector".

This is the v1 embedding: a hand-engineered feature vector (no ML model). It's
still a real vector you store in pgvector and search by cosine similarity — so
you learn the whole embeddings/vector-DB workflow. Phase 5 swaps this for CLAP
(learned audio embeddings) without changing store.py, just VIBE_DIM + this fn.

Layout (VIBE_DIM = 28):
    [0]      bpm (scaled)
    [1]      energy_mean (scaled)
    [2]      spectral_centroid (scaled)
    [3]      spectral_rolloff (scaled)
    [4]      zero_crossing_rate
    [5:18]   13 MFCCs (scaled)
    [18:26]  8-point energy curve (already 0..1)
    [26]     camelot number / 12
    [27]     camelot letter (A=0, B=1)
"""

from __future__ import annotations

import numpy as np

from dj.audio.analyze import TrackFeatures
from dj.audio.camelot import parse
from dj.config import VIBE_DIM


def embed(f: TrackFeatures) -> np.ndarray:
    """Return a (VIBE_DIM,) float32 L2-normalized vector for cosine search."""
    v = np.zeros(VIBE_DIM, dtype=np.float32)

    v[0] = _scale(f.bpm, 60, 200)                  # typical music BPM range
    v[1] = np.clip(f.energy_mean * 5.0, 0, 1)      # RMS is small; lift into 0..1
    v[2] = _scale(f.spectral_centroid, 0, 8000)
    v[3] = _scale(f.spectral_rolloff, 0, 11025)
    v[4] = np.clip(f.zero_crossing_rate, 0, 1)

    mfcc = (f.mfcc + [0.0] * 13)[:13]
    v[5:18] = np.tanh(np.array(mfcc, dtype=np.float32) / 50.0)  # squash to ~-1..1

    curve = (f.energy_curve + [0.0] * 8)[:8]
    v[18:26] = np.array(curve, dtype=np.float32)

    number, letter = parse(f.camelot)
    v[26] = number / 12.0
    v[27] = 1.0 if letter == "B" else 0.0

    norm = np.linalg.norm(v)
    return v / norm if norm > 1e-9 else v


def _scale(x: float, lo: float, hi: float) -> float:
    return float(np.clip((x - lo) / (hi - lo), 0.0, 1.0))
