"""The --explain narrator: pure facts over a SetPlan (no model, no DB)."""

from dj.agents.explain import narrate
from dj.arc import Arc
from dj.plan import SetPlan, Slot


def test_narrate_covers_journey_keys_and_sections():
    arc = Arc.from_shape("set", shape="build", bpm=(120, 126), lufs=(-18, -8))
    slots = [
        Slot(0.0, "a", 120, "8A", -17, title="Opener", artist="x", section_label="intro"),
        Slot(0.5, "b", 123, "9A", -12, title="Mid", artist="y"),
        Slot(1.0, "c", 126, "8A", -8, title="Peak", artist="z", section_label="drop"),
    ]
    text = narrate(SetPlan(arc, slots))
    assert "3 tracks" in text
    assert "Opens at 120 BPM" in text
    assert "opens on the intro of Opener" in text     # section choice surfaced
    assert "rides the drop of Peak" in text           # peak section surfaced
    assert "Verdict" in text


def test_narrate_handles_empty_plan():
    assert "Empty set" in narrate(SetPlan(Arc.from_shape("x"), []))
