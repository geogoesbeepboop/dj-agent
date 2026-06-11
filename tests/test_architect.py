"""Architect: brief → arc, deterministic fallback + LLM-JSON parsing (no API)."""

from dj.agents import architect


def test_deterministic_arc_when_no_model():
    arc = architect.plan_arc("2-hr sunset rooftop, slow build", minutes=120)
    assert arc.minutes == 120
    assert len(arc.points) >= 2
    lufs = [p.lufs for p in arc.points]
    assert lufs == sorted(lufs)                 # "build" → rising energy


def test_bpm_range_follows_genre_keywords():
    techno = architect.plan_arc("driving warehouse techno")
    chill = architect.plan_arc("downtempo sunset lounge")
    assert max(p.bpm for p in techno.points) > max(p.bpm for p in chill.points)


def test_model_json_is_parsed():
    def fake_model(messages):
        return ('Sure! {"name": "dusk", "points": ['
                '{"position": 0.0, "bpm": 118, "lufs": -20},'
                '{"position": 1.0, "bpm": 126, "lufs": -8}]}')

    arc = architect.plan_arc("anything", minutes=60, model=fake_model)
    assert arc.name == "dusk"
    assert arc.points[0].bpm == 118 and arc.points[-1].lufs == -8


def test_bad_model_output_falls_back():
    arc = architect.plan_arc("slow build", model=lambda m: "no json at all")
    assert len(arc.points) >= 2                  # deterministic fallback kicked in
