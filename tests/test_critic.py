"""Critic: transition + whole-set scoring (pure; no LLM, no audio)."""

from dj.arc import Arc
from dj.critic import Thresholds, evaluate_set, energy_arc_rmse, transition
from dj.plan import SetPlan, Slot


def _slot(pos, bpm, camelot, lufs, artist="x", path=None):
    return Slot(position=pos, path=path or f"{camelot}-{bpm}", bpm=bpm,
                camelot=camelot, lufs=lufs, artist=artist)


def test_transition_compatible_is_smooth():
    t = transition(_slot(0, 124, "8A", -12), _slot(1, 125, "9A", -12, artist="y"))
    assert t.compatible and not t.rough


def test_transition_key_clash_is_rough():
    t = transition(_slot(0, 124, "8A", -12), _slot(1, 124, "3B", -12, artist="y"))
    assert not t.compatible and t.rough
    assert any("key clash" in r for r in t.reasons)


def test_transition_flags_big_jumps_and_artist_repeat():
    t = transition(_slot(0, 124, "8A", -12, artist="a"),
                   _slot(1, 140, "8A", -4, artist="a"))
    assert t.rough
    assert any("bpm jump" in r for r in t.reasons)
    assert any("energy jump" in r for r in t.reasons)
    assert t.artist_clash


def test_evaluate_set_passes_a_clean_set():
    arc = Arc.from_shape("flat", shape="flat", bpm=(124, 124), lufs=(-12, -12))
    slots = [
        _slot(0.0, 124, "8A", -12, artist="a"),
        _slot(0.5, 124, "9A", -12, artist="b"),
        _slot(1.0, 124, "10A", -12, artist="c"),
    ]
    report = evaluate_set(SetPlan(arc, slots))
    assert report.passed
    assert report.harmonic_compat_pct == 1.0
    assert report.rough_transitions == []


def test_evaluate_set_fails_on_key_clashes():
    arc = Arc.from_shape("flat", shape="flat", bpm=(124, 124), lufs=(-12, -12))
    slots = [_slot(0.0, 124, "8A", -12, "a"),
             _slot(0.5, 124, "3B", -12, "b"),
             _slot(1.0, 124, "7A", -12, "c")]
    report = evaluate_set(SetPlan(arc, slots), Thresholds())
    assert not report.passed
    assert report.harmonic_compat_pct < 0.7


def test_evaluate_set_flags_close_artist_and_key_monotony():
    arc = Arc.from_shape("flat", shape="flat", bpm=(124, 124), lufs=(-12, -12))
    slots = [                                            # same artist at 0 and 2 (gap 2)
        _slot(0.0, 124, "8A", -12, artist="dup"),
        _slot(0.25, 124, "8A", -12, artist="x"),
        _slot(0.5, 124, "8A", -12, artist="dup"),
        _slot(0.75, 124, "8A", -12, artist="y"),
        _slot(1.0, 124, "8A", -12, artist="z"),          # 5 identical keys in a row
    ]
    r = evaluate_set(SetPlan(arc, slots))
    assert (0, 2) in r.close_artist_pairs
    assert r.longest_key_run == 5
    assert any("too close" in n for n in r.notes)
    assert any("monotonous" in n for n in r.notes)
    assert r.passed                                      # soft signals don't fail the set


def test_energy_arc_rmse_zero_when_on_target():
    arc = Arc.from_shape("flat", shape="flat", lufs=(-12, -12))
    slots = [_slot(p, 124, "8A", -12) for p in (0.0, 0.5, 1.0)]
    assert energy_arc_rmse(SetPlan(arc, slots)) == 0.0
