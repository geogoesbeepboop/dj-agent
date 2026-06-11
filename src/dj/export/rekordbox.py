"""Export a SetPlan as rekordbox.xml — play the set MANUALLY (ADR 0010).

The mixer (`mixer.render_set`) is automatic mode: the machine plays the
transitions. This is manual mode: rekordbox imports the collection plus a
playlist in set order, with BPM, key, and the planned MIX IN / MIX OUT cue
points on every track — the human controls the transitions, the machine has
already done the ordering/key/energy work. The cue points are the Selector's
section bounds (ADR 0004), so "mix out of the outro" is a marker on the deck,
not a note on paper.

Pure file building (ElementTree + string formatting): no DB, no audio, no
network — fully unit-testable by parsing the output back. `write_m3u8` is the
lowest-common-denominator fallback for players that don't read rekordbox XML.
"""

from __future__ import annotations

import os
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from dj.audio import camelot
from dj.plan import SetPlan, Slot


def write_rekordbox_xml(
    plan: SetPlan,
    out_path: str,
    *,
    durations: dict[str, float] | None = None,
    playlist_name: str | None = None,
) -> str:
    """Write the plan as a rekordbox-importable XML library + playlist.

    One COLLECTION entry per unique track path (a track may fill several
    slots; the first slot wins for cue data), then a playlist NODE listing
    every slot in set order. `durations` (path → seconds) feeds TotalTime,
    which rekordbox uses to place cue markers — pass real file durations
    when you have them, or the markers land approximately.
    """
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name="rekordbox", Version="6.0.0", Company="AlphaTheta")

    # Unique tracks in first-appearance order; TrackID is a stable 1..N.
    first_slot: dict[str, Slot] = {}
    for slot in plan.slots:
        first_slot.setdefault(slot.path, slot)
    track_ids = {path: str(i) for i, path in enumerate(first_slot, start=1)}

    collection = ET.SubElement(root, "COLLECTION", Entries=str(len(first_slot)))
    for path, slot in first_slot.items():
        track = ET.SubElement(
            collection,
            "TRACK",
            TrackID=track_ids[path],
            Name=slot.title or Path(path).stem,
            Artist=slot.artist,
            Location=_location(path),
            AverageBpm=f"{slot.bpm:.2f}",
            Tonality=_tonality(slot.camelot),
            TotalTime=_total_time(slot, durations),
        )
        # Deliberately NO <TEMPO> element: rekordbox trusts an imported grid, and
        # we don't know each track's first-downbeat offset — a grid anchored at
        # 0.000 would be confidently wrong on every track. Omitting it makes
        # rekordbox analyze the grid itself; AverageBpm and the second-based
        # cue marks below carry regardless (backlog E2: export real anchors).
        for name, start_s, num in _cue_marks(slot):
            # Each cue twice: a hot cue (Num 0/1) to jump from, and a memory
            # cue (Num -1) so it survives on players with hot cues disabled.
            for n in (num, "-1"):
                ET.SubElement(track, "POSITION_MARK", Name=name, Type="0",
                              Start=f"{start_s:.3f}", Num=n)

    playlists = ET.SubElement(root, "PLAYLISTS")
    folder = ET.SubElement(playlists, "NODE", Type="0", Name="ROOT", Count="1")
    node = ET.SubElement(
        folder,
        "NODE",
        Type="1",
        Name=playlist_name or f"dj-agent — {plan.arc.name}",
        KeyType="0",
        Entries=str(len(plan.slots)),
    )
    for slot in plan.slots:           # set order, repeats allowed
        ET.SubElement(node, "TRACK", Key=track_ids[slot.path])

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_path, xml_declaration=True, encoding="UTF-8")
    return out_path


def write_m3u8(
    plan: SetPlan,
    out_path: str,
    *,
    durations: dict[str, float] | None = None,
) -> str:
    """Write the set order as a plain m3u8 playlist (no cues — just the order)."""
    lines = ["#EXTM3U"]
    for slot in plan.slots:
        name = slot.title or Path(slot.path).stem
        label = f"{slot.artist} - {name}" if slot.artist else name
        lines.append(f"#EXTINF:{_extinf_seconds(slot, durations)},{label}")
        lines.append(os.path.abspath(slot.path))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


# --- pure helpers ------------------------------------------------------------


def _location(path: str) -> str:
    """Rekordbox's quirky file URI form: file://localhost + percent-encoded path."""
    return "file://localhost" + urllib.parse.quote(os.path.abspath(path), safe="/")


def _tonality(code: str) -> str:
    """Camelot → key name ('8A' → 'Am'); a malformed code passes through raw."""
    try:
        return camelot.key_name(code)
    except (ValueError, IndexError):
        return code


def _total_time(slot: Slot, durations: dict[str, float] | None) -> str:
    """Whole-seconds track length: real duration > cue end > 0 (unknown).

    TotalTime matters — rekordbox scales cue-marker positions against it, so a
    real file duration places the markers exactly."""
    if durations and slot.path in durations:
        return str(int(round(durations[slot.path])))
    if slot.cue_end_s is not None:
        return str(int(round(slot.cue_end_s)))
    return "0"


def _cue_marks(slot: Slot) -> list[tuple[str, float, str]]:
    """(name, start_s, hot-cue Num) for the slot's planned mix points, if any."""
    label = slot.section_label or "cue"
    marks: list[tuple[str, float, str]] = []
    if slot.cue_start_s is not None:
        marks.append((f"MIX IN — {label}", slot.cue_start_s, "0"))
    if slot.cue_end_s is not None:
        marks.append((f"MIX OUT — {label}", slot.cue_end_s, "1"))
    return marks


def _extinf_seconds(slot: Slot, durations: dict[str, float] | None) -> int:
    if durations and slot.path in durations:
        return int(round(durations[slot.path]))
    if slot.cue_end_s is not None:
        return int(round(slot.cue_end_s))
    return -1
