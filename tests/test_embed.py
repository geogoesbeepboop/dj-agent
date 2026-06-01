import numpy as np

from dj.audio.analyze import TrackFeatures
from dj.config import VIBE_DIM
from dj.vibe.embed import embed


def _fake_features(**over) -> TrackFeatures:
    base = dict(
        path="x.mp3",
        duration_s=180.0,
        bpm=124.0,
        pitch_class=0,
        mode="major",
        camelot="8B",
        energy_mean=0.1,
        energy_curve=[0.1, 0.3, 0.5, 0.7, 0.8, 0.6, 0.4, 0.2],
        spectral_centroid=2500.0,
        spectral_rolloff=6000.0,
        zero_crossing_rate=0.05,
        mfcc=[float(i) for i in range(13)],
    )
    base.update(over)
    return TrackFeatures(**base)


def test_embed_shape_and_norm():
    v = embed(_fake_features())
    assert v.shape == (VIBE_DIM,)
    assert v.dtype == np.float32
    assert abs(np.linalg.norm(v) - 1.0) < 1e-5   # L2-normalized for cosine


def test_embed_is_deterministic():
    a = embed(_fake_features())
    b = embed(_fake_features())
    assert np.allclose(a, b)


def test_embed_differs_with_vibe():
    slow_low = embed(_fake_features(bpm=80, energy_curve=[0.1] * 8))
    fast_high = embed(_fake_features(bpm=170, energy_curve=[0.9] * 8))
    assert not np.allclose(slow_low, fast_high)
