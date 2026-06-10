"""Selector: greedy ordering, LLM-selection parsing, section choice, the loop.

All pure or fake-injected — no DB, no real model, no audio."""

from dj.agents import selector
from dj.agents.tools import TrackCard
from dj.arc import Arc
from dj.critic import evaluate_set
from dj.plan import SetPlan


def _card(path, bpm, camelot, lufs, score=0.5, artist="x", title=None):
    return TrackCard(path=path, title=title or path, artist=artist, bpm=bpm,
                     camelot=camelot, lufs=lufs, score=score)


def test_greedy_select_is_harmonic_when_possible():
    arc = Arc.from_shape("flat", shape="flat", bpm=(124, 124), lufs=(-12, -12))
    cards = [
        _card("a", 124, "8A", -12, artist="a"),
        _card("b", 124, "9A", -12, artist="b"),
        _card("c", 124, "10A", -12, artist="c"),
        _card("d", 124, "11A", -12, artist="d"),
        _card("decoy", 124, "3B", -12, score=0.4, artist="z"),  # incompatible
    ]
    slots = selector.greedy_select(cards, arc, n=4)
    assert len(slots) == 4
    assert len({s.path for s in slots}) == 4                    # all distinct
    report = evaluate_set(SetPlan(arc, slots))
    assert report.harmonic_compat_pct == 1.0                    # avoided the decoy


def test_greedy_positions_span_zero_to_one():
    arc = Arc.from_shape("build", shape="build")
    cards = [_card(str(i), 120 + i, "8A", -12) for i in range(6)]
    slots = selector.greedy_select(cards, arc, n=4)
    assert slots[0].position == 0.0 and slots[-1].position == 1.0


def test_parse_selection_plain_array():
    cards = [_card(str(i), 124, "8A", -12) for i in range(5)]
    arc = Arc.from_shape("flat", shape="flat")
    slots = selector.parse_selection("[3, 1, 4]", cards, arc)
    assert [s.path for s in slots] == ["2", "0", "3"]           # 1-based → 0-based


def test_parse_selection_tolerates_objects_dupes_and_oob():
    cards = [_card(str(i), 124, "8A", -12) for i in range(3)]
    arc = Arc.from_shape("flat", shape="flat")
    slots = selector.parse_selection('here you go: [{"n":1}, 1, 99, 2]', cards, arc)
    assert [s.path for s in slots] == ["0", "1"]                # dupe + OOB dropped


def test_parse_selection_garbage_returns_empty():
    cards = [_card("0", 124, "8A", -12)]
    arc = Arc.from_shape("flat", shape="flat")
    assert selector.parse_selection("no json here", cards, arc) == []


def test_pick_section_closest_energy():
    class S:
        def __init__(self, idx, lufs):
            self.idx, self.energy_lufs, self.label = idx, lufs, "x"
            self.start_s, self.end_s = 0.0, 1.0
            self.is_mixin = self.is_mixout = False

    sections = [S(0, -20), S(1, -8), S(2, -14)]
    assert selector.pick_section(sections, target_lufs=-9).idx == 1
    assert selector.pick_section([], -9) is None


class _FakeTools:
    """Stand-in for dj.agents.tools: serves a fixed pool, no sections."""

    def __init__(self, cards):
        self.cards = cards

    def query_vibe_db(self, prompt, filters=None, k=60, weights=None):
        return self.cards

    def get_sections(self, path):
        return []


def test_select_loop_returns_passing_plan_from_model():
    arc = Arc.from_shape("flat", shape="flat", bpm=(124, 124), lufs=(-12, -12))
    cards = [
        _card("a", 124, "8A", -12, artist="a"),
        _card("b", 124, "9A", -12, artist="b"),
        _card("c", 124, "10A", -12, artist="c"),
    ]
    model = lambda messages: "[1, 2, 3]"        # noqa: E731 — pick all three in order
    plan = selector.select("vibe", arc, model=model, n=3, tools=_FakeTools(cards))
    assert [s.path for s in plan.slots] == ["a", "b", "c"]
    assert evaluate_set(plan).passed


def test_tracks_for_minutes_scales_and_clamps():
    assert selector.tracks_for_minutes(90) == 26     # round(90 / 3.5)
    assert selector.tracks_for_minutes(10) == 4      # clamped to the low floor
    assert selector.tracks_for_minutes(1000) == 40   # clamped to the high cap


def test_select_falls_back_to_greedy_without_model():
    arc = Arc.from_shape("flat", shape="flat", bpm=(124, 124), lufs=(-12, -12))
    cards = [_card("a", 124, "8A", -12), _card("b", 124, "9A", -12)]
    plan = selector.select("vibe", arc, model=None, n=2, tools=_FakeTools(cards))
    assert len(plan.slots) == 2


class _Sec:
    """A minimal section stand-in for _with_sections / pick_section."""

    def __init__(self, idx, label, lufs, start_s=0.0, end_s=30.0):
        self.idx, self.label, self.energy_lufs = idx, label, lufs
        self.start_s, self.end_s = start_s, end_s


class _SectionTools(_FakeTools):
    """Serves a fixed pool AND a fixed section list for every track."""

    def __init__(self, cards, sections):
        super().__init__(cards)
        self.sections = sections

    def get_sections(self, path):
        return self.sections


def test_section_assignment_rewrites_slot_lufs_to_the_played_part():
    # Track LUFS is -20 (quiet), but the chosen peak slot should grab the louder
    # drop section and the slot's energy must become the section's, so the Critic
    # scores the arc against what actually plays (ADR 0004/0005).
    arc = Arc.from_shape("flat", shape="flat", bpm=(124, 124), lufs=(-8, -8))
    cards = [_card("a", 124, "8A", -20, artist="a")]
    sections = [_Sec(0, "intro", -20.0), _Sec(1, "drop", -8.0)]
    plan = selector.select("peak", arc, model=None, n=1, tools=_SectionTools(cards, sections))
    assert plan.slots[0].section_label == "drop"
    assert plan.slots[0].lufs == -8.0                       # rewritten from -20
    assert evaluate_set(plan).energy_arc_rmse == 0.0        # now on-target
