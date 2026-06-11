"""Rekordbox/m3u8 export: pure XML/text building, parsed back (no DB/audio/network)."""

import urllib.parse
import xml.etree.ElementTree as ET

import pytest

from dj.arc import Arc
from dj.export import write_m3u8, write_rekordbox_xml
from dj.plan import SetPlan, Slot


def _slot(path: str, position: float = 0.0, bpm: float = 124.0,
          camelot: str = "8A", **kw) -> Slot:
    return Slot(position, path, bpm, camelot, -12.0, **kw)


def _plan(slots: list[Slot], name: str = "sunset") -> SetPlan:
    return SetPlan(Arc.from_shape(name, shape="flat", bpm=(124, 124)), slots)


def _write(tmp_path, plan: SetPlan, **kw) -> ET.Element:
    out = write_rekordbox_xml(plan, str(tmp_path / "rekordbox.xml"), **kw)
    return ET.parse(out).getroot()


def test_collection_dedupes_and_playlist_keeps_set_order(tmp_path):
    # 4 slots over 3 unique tracks: /a opens AND closes the set.
    plan = _plan([
        _slot("/music/a.mp3", 0.0),
        _slot("/music/b.mp3", 0.33),
        _slot("/music/c.mp3", 0.66),
        _slot("/music/a.mp3", 1.0),
    ])
    root = _write(tmp_path, plan)

    collection = root.find("COLLECTION")
    tracks = collection.findall("TRACK")
    assert collection.get("Entries") == "3" and len(tracks) == 3
    # TrackID is a stable 1..N in first-appearance order.
    assert [(t.get("TrackID"), t.get("Name")) for t in tracks] == [
        ("1", "a"), ("2", "b"), ("3", "c")]

    node = root.find("PLAYLISTS/NODE[@Name='ROOT']/NODE")
    assert node.get("Entries") == "4"
    assert [t.get("Key") for t in node.findall("TRACK")] == ["1", "2", "3", "1"]


def test_location_quoting_roundtrips_awkward_paths(tmp_path):
    path = "/music/Mix Tapes/#1 — Côte d'Azur.mp3"   # spaces, #, unicode
    root = _write(tmp_path, _plan([_slot(path)]))
    location = root.find("COLLECTION/TRACK").get("Location")
    assert location.startswith("file://localhost/")
    assert "#" not in location and " " not in location
    assert urllib.parse.unquote(location.removeprefix("file://localhost")) == path


@pytest.mark.parametrize("camelot, tonality", [
    ("8A", "Am"),       # minor side
    ("8B", "C"),        # major side
    ("13Z", "13Z"),     # malformed → raw passthrough, never a crash
])
def test_tonality_maps_camelot_to_key_name(tmp_path, camelot, tonality):
    root = _write(tmp_path, _plan([_slot("/music/a.mp3", camelot=camelot)]))
    assert root.find("COLLECTION/TRACK").get("Tonality") == tonality


def test_cued_slot_gets_hot_and_memory_marks_and_uncued_gets_none(tmp_path):
    plan = _plan([
        _slot("/music/a.mp3", 0.0, section_label="drop", cue_start_s=10.0, cue_end_s=40.5),
        _slot("/music/b.mp3", 1.0),                     # no cues → no marks
    ])
    root = _write(tmp_path, plan)
    a, b = root.findall("COLLECTION/TRACK")

    marks = [(m.get("Name"), m.get("Start"), m.get("Num")) for m in a.findall("POSITION_MARK")]
    assert marks == [                                   # each cue = hot + memory
        ("MIX IN — drop", "10.000", "0"),
        ("MIX IN — drop", "10.000", "-1"),
        ("MIX OUT — drop", "40.500", "1"),
        ("MIX OUT — drop", "40.500", "-1"),
    ]
    assert all(m.get("Type") == "0" for m in a.findall("POSITION_MARK"))
    assert b.findall("POSITION_MARK") == []


def test_totaltime_prefers_durations_over_cue_end(tmp_path):
    path = "/music/a.mp3"
    slots = [_slot(path, cue_end_s=40.4)]
    with_durations = _write(tmp_path, _plan(slots), durations={path: 215.6})
    assert with_durations.find("COLLECTION/TRACK").get("TotalTime") == "216"

    cue_only = _write(tmp_path, _plan(slots))
    assert cue_only.find("COLLECTION/TRACK").get("TotalTime") == "40"

    unknown = _write(tmp_path, _plan([_slot(path)]))
    assert unknown.find("COLLECTION/TRACK").get("TotalTime") == "0"


def test_track_attributes_and_no_tempo_without_anchor(tmp_path):
    root = _write(tmp_path, _plan([_slot("/music/a.mp3", bpm=123.456, title="Anthem",
                                         artist="Ms. X")]))
    track = root.find("COLLECTION/TRACK")
    assert track.get("Name") == "Anthem" and track.get("Artist") == "Ms. X"
    assert track.get("AverageBpm") == "123.46"
    # No anchor known → no TEMPO on purpose: a grid anchored at a guess would be
    # confidently wrong, and rekordbox trusts an imported grid — let it analyze.
    assert track.find("TEMPO") is None
    product = root.find("PRODUCT")
    assert product.get("Name") == "rekordbox" and product.get("Company") == "AlphaTheta"


def test_known_first_downbeat_exports_a_real_grid_anchor(tmp_path):
    # ADR 0011 / backlog E2: with a stored first downbeat the imported grid is
    # OURS — TEMPO Inizio at the detected bar-1, not rekordbox's re-analysis.
    plan = _plan([
        _slot("/music/a.mp3", 0.0, bpm=124.0, first_downbeat_s=0.482),
        _slot("/music/b.mp3", 1.0, bpm=0.0, first_downbeat_s=5.0),  # bpm 0 → no grid
    ])
    root = _write(tmp_path, plan)
    a, b = root.findall("COLLECTION/TRACK")
    tempo = a.find("TEMPO")
    assert tempo is not None
    assert tempo.get("Inizio") == "0.482"
    assert tempo.get("Bpm") == "124.00"
    assert tempo.get("Metro") == "4/4" and tempo.get("Battito") == "1"
    assert b.find("TEMPO") is None


def test_playlist_node_name_defaults_to_arc_name(tmp_path):
    plan = _plan([_slot("/music/a.mp3")], name="warehouse warmup")
    default = _write(tmp_path, plan)
    assert default.find("PLAYLISTS/NODE/NODE").get("Name") == "dj-agent — warehouse warmup"

    named = _write(tmp_path, plan, playlist_name="Friday B2B")
    assert named.find("PLAYLISTS/NODE/NODE").get("Name") == "Friday B2B"


def test_m3u8_lists_slots_with_extinf_and_fallback(tmp_path):
    plan = _plan([
        _slot("/music/a.mp3", 0.0, title="Anthem", artist="Ms. X"),
        _slot("/music/b side.mp3", 1.0),                # no duration, no cue → -1
    ])
    out = write_m3u8(plan, str(tmp_path / "set.m3u8"), durations={"/music/a.mp3": 215.6})
    lines = (tmp_path / "set.m3u8").read_text(encoding="utf-8").splitlines()
    assert out == str(tmp_path / "set.m3u8")
    assert lines == [
        "#EXTM3U",
        "#EXTINF:216,Ms. X - Anthem",
        "/music/a.mp3",
        "#EXTINF:-1,b side",                            # stem fallback, no artist dash
        "/music/b side.mp3",
    ]


def test_cue_marks_name_entry_exit_and_core_sections(tmp_path):
    slot = _slot("/music/a.mp3", section_label="drop", cue_start_s=10.0,
                 cue_end_s=250.0, mixin_label="intro", mixout_label="outro",
                 core_start_s=120.0)
    root = _write(tmp_path, _plan([slot]))
    marks = root.findall("COLLECTION/TRACK/POSITION_MARK")
    hot = {m.get("Name"): (m.get("Start"), m.get("Num")) for m in marks if m.get("Num") != "-1"}
    assert hot["MIX IN — intro"] == ("10.000", "0")
    assert hot["MIX OUT — outro"] == ("250.000", "1")
    assert hot["DROP"] == ("120.000", "2")                  # jump to the money moment
    # every hot cue is doubled as a memory cue (Num -1)
    assert sum(1 for m in marks if m.get("Num") == "-1") == 3


def test_core_cue_omitted_when_span_starts_on_it(tmp_path):
    slot = _slot("/music/a.mp3", section_label="drop", cue_start_s=10.0,
                 cue_end_s=250.0, core_start_s=10.0)        # core IS the entry
    root = _write(tmp_path, _plan([slot]))
    names = [m.get("Name") for m in root.findall("COLLECTION/TRACK/POSITION_MARK")]
    assert "DROP" not in names
