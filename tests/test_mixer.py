"""Mixer: pure transition planning + the equal-power crossfade (no audio deps)."""

import numpy as np

from dj.arc import Arc
from dj.mixer import (
    _crossfade_join,
    _normalize,
    crossfade_bars,
    crossfade_seconds,
    plan_transitions,
    section_bars,
    set_duration_seconds,
    stretch_ratio,
)
from dj.plan import SetPlan, Slot


def test_section_bars_at_124():
    # 124 BPM, 4/4 → ~1.935 s/bar; ~15.5 s ≈ 8 bars.
    assert section_bars(15.48, 124) == 8
    assert section_bars(None, 124) is None
    assert section_bars(10, 0) is None


def test_crossfade_bars_is_half_section_capped():
    assert crossfade_bars(31.0, 124) == 8        # 16-bar section → 8, hits the cap
    assert crossfade_bars(8.0, 124) == 2         # ~4-bar section → 2 bars
    assert crossfade_bars(None, 124) == 4        # unknown → a safe default


def test_crossfade_seconds_matches_tempo():
    assert crossfade_seconds(120, 8) == 16.0     # 8 bars × 4 beats × 0.5 s
    assert crossfade_seconds(0, 8) == 0.0


def test_stretch_ratio():
    assert stretch_ratio(120, 124) == 124 / 120  # speed up to beatmatch
    assert stretch_ratio(0, 124) == 1.0


def test_plan_transitions_uses_arc_tempo():
    arc = Arc.from_shape("flat", shape="flat", bpm=(124, 124))
    slots = [
        Slot(0.0, "a", 120, "8A", -12, cue_start_s=0, cue_end_s=15.48),
        Slot(1.0, "b", 128, "9A", -12, cue_start_s=0, cue_end_s=15.48),
    ]
    trans = plan_transitions(SetPlan(arc, slots))
    assert len(trans) == 1
    assert trans[0].target_bpm == 124            # beatmatch B to the arc target
    assert trans[0].crossfade_s > 0


def test_crossfade_join_is_equal_power_and_continuous():
    sr = 100
    a = np.ones(300, dtype=np.float32)
    b = np.ones(300, dtype=np.float32)
    out = _crossfade_join(a, b, sr, cross_s=1.0)   # 100-sample overlap
    assert len(out) == 500                          # 300 + 300 − 100 overlap
    assert float(np.max(np.abs(out))) <= 1.5        # equal-power, no big spike


def test_crossfade_join_zero_overlap_concatenates():
    out = _crossfade_join(np.ones(10, np.float32), np.ones(10, np.float32), 100, 0.0)
    assert len(out) == 20


def test_normalize_is_robust_to_a_single_transient():
    mix = np.full(10000, 0.5, np.float32)
    mix[0] = 5.0                                   # one giant transient sample
    out = _normalize(mix)
    assert out.max() <= 1.0                         # output never clips
    # the steady body is normalized UP toward the ceiling, not crushed to ~0.1
    assert float(np.median(np.abs(out))) > 0.8


def test_set_duration_seconds_subtracts_the_overlap():
    arc = Arc.from_shape("flat", shape="flat", bpm=(120, 120))
    slots = [
        Slot(0.0, "a", 120, "8A", -12, cue_start_s=0, cue_end_s=120),
        Slot(1.0, "b", 120, "9A", -12, cue_start_s=0, cue_end_s=120),
    ]
    dur = set_duration_seconds(SetPlan(arc, slots))
    assert 200 < dur < 240                          # 240 s of audio minus the crossfade
