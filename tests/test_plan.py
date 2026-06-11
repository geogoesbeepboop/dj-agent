"""SetPlan / Slot / Arc serialization — the plan round-trips through JSON (persist)."""

import json

from dj.arc import Arc
from dj.plan import SetPlan, Slot


def test_setplan_roundtrips_through_json():
    arc = Arc.from_shape("sunset", minutes=120, shape="build", bpm=(118, 124), lufs=(-18, -9))
    slots = [
        Slot(0.0, "/a.mp3", 120, "8A", -16, title="A", artist="x",
             section_idx=1, section_label="drop", cue_start_s=10.0, cue_end_s=40.0,
             taste_score=0.7),
        Slot(1.0, "/b.mp3", 124, "9A", -10, title="B", artist="y"),
    ]
    plan = SetPlan(arc, slots)
    revived = SetPlan.from_dict(json.loads(json.dumps(plan.to_dict())))

    assert revived.paths == plan.paths
    assert revived.arc.minutes == 120
    assert [p.bpm for p in revived.arc.points] == [p.bpm for p in arc.points]
    s0 = revived.slots[0]
    assert s0.section_label == "drop" and s0.cue_start_s == 10.0 and s0.taste_score == 0.7


def test_span_fields_and_genre_roundtrip():
    arc = Arc.from_shape("set", shape="flat")
    slot = Slot(0.0, "/a.mp3", 120, "8A", -16, mixin_label="intro",
                mixout_label="outro", core_start_s=42.0)
    plan = SetPlan(arc, [slot], genre="latin")
    revived = SetPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert revived.genre == "latin"
    s = revived.slots[0]
    assert (s.mixin_label, s.mixout_label, s.core_start_s) == ("intro", "outro", 42.0)


def test_old_persisted_plans_still_load():
    # Plans logged before genre/span fields existed must keep loading (persist.py).
    d = {"arc": {"name": "old", "minutes": 60, "points": []},
         "slots": [{"position": 0.0, "path": "/a.mp3", "bpm": 120.0,
                    "camelot": "8A", "lufs": -12.0}]}
    plan = SetPlan.from_dict(d)
    assert plan.genre is None and plan.slots[0].mixin_label is None
