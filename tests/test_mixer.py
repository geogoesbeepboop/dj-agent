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


# --- phrase quantization + genre physics --------------------------------------


def test_quantize_phrase_bars_snaps_to_powers_of_two():
    from dj.mixer import quantize_phrase_bars

    assert quantize_phrase_bars(1) == 1
    assert quantize_phrase_bars(3) == 2
    assert quantize_phrase_bars(7) == 4      # a 7-bar blend would end mid-phrase
    assert quantize_phrase_bars(8) == 8
    assert quantize_phrase_bars(12) == 8
    assert quantize_phrase_bars(33) == 32
    assert quantize_phrase_bars(0) == 1


def test_crossfade_bars_is_phrase_quantized():
    # ~12-bar section at 124 → half is 6 → quantized DOWN to a 4-bar phrase.
    twelve_bars_s = 12 * 4 * 60.0 / 124
    assert crossfade_bars(twelve_bars_s, 124) == 4


def test_clamp_target_bpm_caps_the_stretch():
    from dj.mixer import clamp_target_bpm

    assert clamp_target_bpm(124, 120, 0.05) == 124          # within ±5%
    assert clamp_target_bpm(130, 120, 0.05) == 126          # capped high
    assert clamp_target_bpm(110, 120, 0.05) == 114          # capped low
    assert clamp_target_bpm(0, 120, 0.05) == 120            # no target → native
    assert clamp_target_bpm(124, 0, 0.05) == 124            # no source → target


def test_plan_transitions_respects_the_genre_profile():
    # A hip-hop plan: blends cap at 2 bars and a rap vocal is never bent >3%.
    arc = Arc.from_shape("flat", shape="flat", bpm=(95, 95))
    slots = [
        Slot(0.0, "a", 92, "8A", -10, cue_start_s=0, cue_end_s=120),
        Slot(1.0, "b", 100, "9A", -10, cue_start_s=0, cue_end_s=120),
    ]
    trans = plan_transitions(SetPlan(arc, slots, genre="hiphop"))
    assert trans[0].bars <= 2                               # cut, not a long blend
    assert trans[0].target_bpm == 97.0                      # 95 clamped to 100·0.97
    # The SAME slots as a house plan: longer blend, the arc tempo is reachable.
    house = plan_transitions(SetPlan(arc, slots, genre="house"))
    assert house[0].bars > trans[0].bars
    assert house[0].target_bpm == 95.0
