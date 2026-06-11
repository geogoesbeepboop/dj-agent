"""Arc artifact: interpolation, shapes, brief heuristics (pure; no LLM)."""

from dj.arc import Arc, ArcPoint, shape_from_brief


def _arc():
    return Arc(name="t", minutes=60, points=[
        ArcPoint(0.0, 120, -18), ArcPoint(0.5, 124, -12), ArcPoint(1.0, 122, -16),
    ])


def test_target_at_endpoints_and_midpoint():
    a = _arc()
    assert a.target_at(0.0).bpm == 120 and a.target_at(0.0).lufs == -18
    assert a.target_at(1.0).bpm == 122
    mid = a.target_at(0.25)              # halfway into the first segment
    assert mid.bpm == 122 and mid.lufs == -15


def test_target_at_clamps_out_of_range():
    a = _arc()
    assert a.target_at(-1.0).bpm == 120
    assert a.target_at(2.0).bpm == 122


def test_from_shape_build_rises_monotonically():
    a = Arc.from_shape("b", shape="build", bpm=(118, 126), lufs=(-18, -8), n_points=5)
    lufs = [p.lufs for p in a.points]
    assert lufs == sorted(lufs)          # build → non-decreasing energy
    assert a.points[0].lufs == -18 and a.points[-1].lufs == -8


def test_from_shape_peak_tops_out_before_end():
    a = Arc.from_shape("p", shape="peak", lufs=(-18, -8), n_points=11)
    energies = [p.lufs for p in a.points]
    assert max(energies) > energies[-1]  # eases down after the peak


def test_bpm_band_pads_the_range():
    a = Arc.from_shape("b", shape="build", bpm=(120, 128))
    lo, hi = a.bpm_band(slack=4)
    assert lo <= 116 and hi >= 132


def test_shape_from_brief_keywords():
    assert shape_from_brief("2-hr sunset rooftop, slow build") == "build"
    assert shape_from_brief("peak-time main room banger") == "peak"
    assert shape_from_brief("dinner lounge background") == "flat"
    assert shape_from_brief("closing wind-down comedown") == "down"
