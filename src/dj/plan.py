"""Shared plan types: a Slot (a track/section placed in the set) and a SetPlan.

Neutral dataclasses with no behavior, so every layer can depend on them without
cycles: the Selector *produces* a SetPlan, the Critic *scores* it, the HITL gate
*renders* it, and the Mixer *renders it to audio*. The plan is the data the HITL
gate approves — the set-acceptance eval is literally a human reading one of these.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from dj.arc import Arc


@dataclass
class Slot:
    """One placed track (optionally just a section of it) at a set position."""

    position: float                      # 0..1 over the set
    path: str
    bpm: float
    camelot: str
    lufs: float
    title: str = ""
    artist: str = ""
    # Which part to actually play (ADR 0004). None → the whole track. The cue
    # span runs from a mix-IN section's start to a mix-OUT section's end; the
    # "core" section between them is the part the arc targeted (the energy the
    # Critic scores). core_start_s is only set when it differs from cue_start_s.
    section_idx: int | None = None
    section_label: str | None = None
    cue_start_s: float | None = None
    cue_end_s: float | None = None
    mixin_label: str | None = None       # label of the section the cue enters on
    mixout_label: str | None = None      # label of the section the cue exits on
    core_start_s: float | None = None    # where the core section hits (hot-cue 2)
    taste_score: float = 0.0             # blended retrieval score (for display/sorting)
    # First bar-"1" time (s) from segmentation (ADR 0011) — the rekordbox
    # beat-grid anchor. None → the export omits TEMPO and rekordbox analyzes.
    first_downbeat_s: float | None = None

    @property
    def display(self) -> str:
        who = f"{self.title or self.path}" + (f" — {self.artist}" if self.artist else "")
        part = f" [{self.section_label}]" if self.section_label else ""
        return who + part

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Slot":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class SetPlan:
    """An ordered set + the arc it was built to follow — the HITL-approved artifact."""

    arc: Arc
    slots: list[Slot]
    # Which genre profile (dj/profiles.py) shaped this plan — the Mixer reads it
    # back at render time for crossfade/stretch policy. None → the open default.
    genre: str | None = None

    @property
    def paths(self) -> list[str]:
        return [s.path for s in self.slots]

    def __len__(self) -> int:
        return len(self.slots)

    def to_dict(self) -> dict:
        """Plain-data form — the whole plan round-trips through JSON (persist.py)."""
        return {
            "arc": self.arc.to_dict(),
            "genre": self.genre,
            "slots": [s.to_dict() for s in self.slots],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SetPlan":
        return cls(arc=Arc.from_dict(d["arc"]), slots=[Slot.from_dict(s) for s in d.get("slots", [])],
                   genre=d.get("genre"))
