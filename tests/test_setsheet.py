"""Set sheet export: the printable cue card is built purely from the plan."""

from dj.arc import Arc
from dj.export.setsheet import render_setsheet, write_setsheet
from dj.plan import SetPlan, Slot


def _plan() -> SetPlan:
    arc = Arc.from_shape("block party", shape="flat", bpm=(95, 95), lufs=(-10, -10))
    slots = [
        Slot(0.0, "/m/a.mp3", 92, "8A", -10, title="Opener", artist="MC A",
             section_idx=1, section_label="chorus", cue_start_s=0.0, cue_end_s=120.0,
             mixin_label="intro", mixout_label="outro", core_start_s=30.0),
        Slot(1.0, "/m/b.mp3", 96, "3B", -10, title="Closer", artist="MC B",
             cue_start_s=15.0, cue_end_s=140.0),
    ]
    return SetPlan(arc, slots, genre="hiphop")


def test_setsheet_lists_tracks_with_cue_times_and_parts():
    sheet = render_setsheet(_plan())
    assert "# Set sheet — block party" in sheet
    assert "genre: hiphop" in sheet
    assert "| 1 | Opener — MC A [chorus] | 8A | 92 | 0:00 | 2:00 | 2:00 |" in sheet
    assert "intro→chorus→outro" in sheet          # the span, not just one section


def test_setsheet_transition_notes_carry_style_and_warnings():
    sheet = render_setsheet(_plan())
    assert "1 → 2:" in sheet
    # hiphop caps blends at 2 bars → a cut/short-blend, never a long blend
    assert "long blend" not in sheet
    # 8A → 3B is a key clash; the sheet must warn the human at the decks
    assert "⚠" in sheet and "key clash" in sheet


def test_write_setsheet_writes_the_file(tmp_path):
    out = write_setsheet(_plan(), str(tmp_path / "set.setsheet.md"))
    assert (tmp_path / "set.setsheet.md").read_text().startswith("# Set sheet")
    assert out.endswith("set.setsheet.md")
