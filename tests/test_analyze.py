"""Analysis test on a synthetic click track — no audio files needed.

Skipped automatically if librosa isn't installed, so the fast unit tests
(camelot, metadata) still run in a minimal environment.
"""

import numpy as np
import pytest

librosa = pytest.importorskip("librosa")

from dj.audio.analyze import _features_from_signal  # noqa: E402
from dj.audio.camelot import parse  # noqa: E402


def _click_track(bpm=120, sr=22050, seconds=8) -> np.ndarray:
    """Impulses at a fixed BPM → a signal with a real, detectable tempo."""
    y = np.zeros(int(sr * seconds), dtype=np.float32)
    step = int(sr * 60 / bpm)
    y[::step] = 1.0
    return y


def test_features_from_signal_basic():
    y = _click_track(bpm=120)
    f = _features_from_signal(y, sr=22050)

    assert f.duration_s == pytest.approx(8.0, abs=0.1)
    assert f.bpm > 0
    # camelot code is well-formed
    n, letter = parse(f.camelot)
    assert 1 <= n <= 12 and letter in ("A", "B")
    # energy curve is the right length and within 0..1
    assert len(f.energy_curve) == 8
    assert all(0.0 <= e <= 1.0 for e in f.energy_curve)
    # pitch_class / mode are populated
    assert 0 <= f.pitch_class <= 11
    assert f.mode in ("major", "minor")


def test_bpm_in_reasonable_range():
    f = _features_from_signal(_click_track(bpm=120), sr=22050)
    # beat trackers often report half/double tempo; accept the common multiples.
    assert any(abs(f.bpm - t) < 8 for t in (60, 120, 240))
