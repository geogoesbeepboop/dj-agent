"""HITL plan rendering (pure formatter; the set-acceptance artifact)."""

from dj.agents import hitl
from dj.arc import Arc
from dj.plan import SetPlan, Slot


def test_render_plan_lists_tracks_and_verdict():
    arc = Arc.from_shape("flat", shape="flat", bpm=(124, 124), lufs=(-12, -12))
    slots = [
        Slot(0.0, "a.mp3", 124, "8A", -12, title="Opener", artist="A",
             section_label="intro", cue_start_s=0, cue_end_s=30),
        Slot(1.0, "b.mp3", 124, "9A", -12, title="Closer", artist="B"),
    ]
    out = hitl.render_plan(SetPlan(arc, slots))
    assert "Opener" in out and "Closer" in out
    assert "[intro]" in out                 # section the Mixer will use
    assert "PASS" in out                    # clean two-track set passes


def test_render_plan_explains_rough_transition_reason():
    arc = Arc.from_shape("flat", shape="flat", bpm=(124, 124), lufs=(-12, -12))
    # compatible keys but a big energy cliff → rough for an *energy* reason
    slots = [
        Slot(0.0, "a", 124, "8A", -6, title="loud", artist="A"),
        Slot(1.0, "b", 124, "9A", -18, title="quiet", artist="B"),
    ]
    out = hitl.render_plan(SetPlan(arc, slots))
    assert "energy jump" in out             # the real reason, not the (fine) key
