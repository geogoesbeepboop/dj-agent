"""Export a SetPlan as a printable set sheet (markdown) — the DJ's cheat card.

The rekordbox XML carries the cues into the player; this carries the *plan* into
the booth: track order with keys/BPM/cue times, and per-transition notes — how
many bars to blend, roughly when, and anything the Critic flagged ("key clash
here — cut, don't blend"). It's what a working DJ scribbles on paper or tapes to
the mixer, generated. Pure string building over the plan + the Mixer's
transition planning + the Critic's report — no DB, no audio.
"""

from __future__ import annotations

from pathlib import Path

from dj.critic import SetReport, evaluate_set, transition as score_transition
from dj.mixer import plan_transitions, set_duration_seconds
from dj.plan import SetPlan, Slot


def write_setsheet(
    plan: SetPlan,
    out_path: str,
    *,
    durations: dict[str, float] | None = None,
    report: SetReport | None = None,
) -> str:
    """Render the set sheet and write it to `out_path`. Returns the path."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(render_setsheet(plan, durations=durations, report=report),
                              encoding="utf-8")
    return out_path


def render_setsheet(
    plan: SetPlan,
    *,
    durations: dict[str, float] | None = None,
    report: SetReport | None = None,
) -> str:
    """The sheet as markdown (pure — unit-tested by string inspection)."""
    from dj import profiles

    arc = plan.arc
    th = profiles.get(plan.genre).thresholds()    # grade on the GENRE's bars
    report = report or evaluate_set(plan, th)
    transitions = plan_transitions(plan)
    est_min = set_duration_seconds(plan) / 60.0

    head = [f"# Set sheet — {arc.name}"]
    meta = [f"{len(plan.slots)} tracks", f"target {arc.minutes} min",
            f"estimated ~{est_min:.0f} min"]
    if plan.genre:
        meta.insert(0, f"genre: {plan.genre}")
    head.append(" · ".join(meta))
    head.append("arc: " + " → ".join(f"{p.bpm:.0f}bpm/{p.lufs:.0f}LUFS" for p in arc.points))
    head.append("")

    table = [
        "| # | Track | Key | BPM | In | Out | Play | Part |",
        "|---|-------|-----|-----|----|----|------|------|",
    ]
    for i, s in enumerate(plan.slots, start=1):
        cue_in = _mmss(s.cue_start_s) if s.cue_start_s is not None else "—"
        cue_out = _mmss(s.cue_end_s) if s.cue_end_s is not None else "—"
        play = _play_time(s, durations)
        part = _part_label(s)
        table.append(
            f"| {i} | {s.display} | {s.camelot} | {s.bpm:.0f} "
            f"| {cue_in} | {cue_out} | {play} | {part} |"
        )

    notes = ["", "## Transitions", ""]
    for t in transitions:
        a, b = plan.slots[t.from_idx], plan.slots[t.to_idx]
        line = (f"{t.from_idx + 1} → {t.to_idx + 1}: {_style(t.bars)} "
                f"(~{t.crossfade_s:.0f}s at {t.target_bpm:.0f} BPM)")
        ends = []
        if a.mixout_label:
            ends.append(f"out of the {a.mixout_label}")
        if b.mixin_label:
            ends.append(f"into the {b.mixin_label}")
        if ends:
            line += " — " + ", ".join(ends)
        warn = ", ".join(score_transition(a, b, th).reasons)
        if warn:
            line += f"  ⚠ {warn}"
        notes.append(line)

    tail = [""]
    if report.notes:
        tail += ["## Watch out", ""] + [f"- {n}" for n in report.notes] + [""]
    return "\n".join(head + table + notes + tail)


# --- pure helpers ------------------------------------------------------------


def _mmss(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 60}:{s % 60:02d}"


def _play_time(slot: Slot, durations: dict[str, float] | None) -> str:
    if slot.cue_start_s is not None and slot.cue_end_s is not None:
        return _mmss(slot.cue_end_s - slot.cue_start_s)
    if durations and slot.path in durations:
        return _mmss(durations[slot.path])
    return "—"


def _part_label(slot: Slot) -> str:
    """'intro→drop→outro' when the span is known, else the core label or whole."""
    parts = [p for p in (slot.mixin_label, slot.section_label, slot.mixout_label) if p]
    deduped: list[str] = []
    for p in parts:
        if not deduped or deduped[-1] != p:
            deduped.append(p)
    return "→".join(deduped) if deduped else "whole track"


def _style(bars: int) -> str:
    if bars >= 8:
        return f"{bars}-bar long blend"
    if bars >= 4:
        return f"{bars}-bar blend"
    if bars >= 2:
        return f"{bars}-bar short blend"
    return "quick cut (1 bar)"
