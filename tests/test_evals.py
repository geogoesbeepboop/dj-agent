"""Eval scorecard: the pure metric core (taste-match, discovery, assembly).

No DB — synthetic vectors + a hand-built SetReport, like test_critic/test_score."""

import numpy as np

from dj.arc import Arc
from dj.critic import evaluate_set
from dj.evals.runner import (
    Scorecard,
    discovery_ratio,
    from_report,
    render,
    taste_match,
)
from dj.plan import SetPlan, Slot


def _unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


def test_taste_match_high_when_set_sits_on_favorites():
    favs = [_unit([1, 0, 0]), _unit([0.9, 0.1, 0])]
    on = [_unit([1, 0, 0]), _unit([0.95, 0.05, 0])]
    assert taste_match(on, favs) > 0.95


def test_taste_match_low_when_orthogonal():
    favs = [_unit([1, 0, 0])]
    off = [_unit([0, 1, 0]), _unit([0, 0, 1])]
    assert abs(taste_match(off, favs)) < 1e-6


def test_taste_match_empty_sides_are_zero():
    assert taste_match([], [_unit([1, 0, 0])]) == 0.0
    assert taste_match([_unit([1, 0, 0])], []) == 0.0


def test_discovery_ratio_counts_unknown_slots():
    # 2 of 4 already known (favorite/manual) → 50% discovery.
    assert discovery_ratio([True, False, True, False]) == 0.5
    assert discovery_ratio([]) == 0.0
    assert discovery_ratio([False, False]) == 1.0


def test_from_report_carries_critic_metrics_and_personal_signal():
    arc = Arc.from_shape("flat", shape="flat", bpm=(124, 124), lufs=(-12, -12))
    slots = [
        Slot(0.0, "a", 124, "8A", -12, artist="a"),
        Slot(1.0, "b", 124, "9A", -12, artist="b"),
    ]
    report = evaluate_set(SetPlan(arc, slots))
    card = from_report(report, taste_match=0.42, discovery_ratio=0.5, n_with_taste=2)
    assert isinstance(card, Scorecard)
    assert card.passed and card.harmonic_compat_pct == 1.0
    assert card.taste_match == 0.42 and card.discovery_ratio == 0.5
    assert "taste-match" in render(card)
