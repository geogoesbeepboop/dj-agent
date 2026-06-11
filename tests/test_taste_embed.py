"""Taste-note embedding (slow: downloads the sentence-transformer, runs inference).

Run explicitly with:  uv run pytest -m slow
"""

import numpy as np
import pytest

from dj.config import TASTE_DIM


@pytest.mark.slow
def test_embed_note_shape_and_norm():
    from dj.taste import embed

    v = embed.embed_note("dreamy nocturnal sunset opener")
    assert v.shape == (TASTE_DIM,)
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-3


@pytest.mark.slow
def test_similar_notes_are_closer_than_dissimilar():
    from dj.taste import embed

    a = embed.embed_note("euphoric peak-time banger, hands in the air")
    b = embed.embed_note("high-energy peak track, the crowd goes wild")
    c = embed.embed_note("slow mellow ambient intro, barely-there beat")
    assert float(a @ b) > float(a @ c)
