"""CLAP encoder tests — slow; skipped unless torch + transformers are present.

First run downloads ~1.5 GB of model weights, so this is NOT part of the fast
unit path. Run explicitly with: uv run pytest tests/test_clap.py
"""

import numpy as np
import pytest

pytest.importorskip("torch")
pytest.importorskip("transformers")

from dj.config import VIBE_DIM  # noqa: E402
from dj.vibe import clap  # noqa: E402


def _is_unit_norm(v: np.ndarray) -> bool:
    return abs(float(np.linalg.norm(v)) - 1.0) < 1e-4


@pytest.mark.slow
def test_embed_text_shape_and_norm():
    v = clap.embed_text("dreamy nocturnal deep house")
    assert v.shape == (VIBE_DIM,)
    assert v.dtype == np.float32
    assert _is_unit_norm(v)


@pytest.mark.slow
def test_text_similarity_is_semantic():
    # Closely related prompts should sit nearer than unrelated ones.
    a = clap.embed_text("aggressive hard techno")
    b = clap.embed_text("driving warehouse techno")
    c = clap.embed_text("soft acoustic ballad")
    assert float(a @ b) > float(a @ c)


def test_window_caps_long_signal():
    sr = clap.CLAP_SAMPLE_RATE
    long_signal = np.zeros(sr * 600, dtype=np.float32)  # 10 min
    windows = clap._window(long_signal, sr)
    assert len(windows) <= clap._MAX_WINDOWS
    assert all(len(w) == clap._WINDOW_SECONDS * sr for w in windows)
