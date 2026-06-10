"""`--explain`: narrate a planned set in plain language (deterministic).

A pure formatter over the SetPlan + Critic SetReport — "opens at 118 BPM / −18
LUFS, lifts 8A→9A into the peak, rides only the drop of <track> at the apex, winds
down to −16 to close". No model needed, so `--explain` works offline; an LLM can
polish the prose later (a `model` seam like the Architect's), but the facts always
come from the plan, never invented. This is the Phase 5 `--explain` deliverable.
"""

from __future__ import annotations

from dj.audio import camelot
from dj.critic import SetReport, evaluate_set
from dj.plan import SetPlan


def narrate(plan: SetPlan, report: SetReport | None = None) -> str:
    """A few sentences explaining the arc, the key moves, and the section picks."""
    if not plan.slots:
        return "Empty set — nothing to narrate."
    report = report or evaluate_set(plan)
    slots = plan.slots
    arc = plan.arc
    n = len(slots)
    peak_i = max(range(n), key=lambda i: slots[i].lufs)
    out = [f'"{arc.name}" — {arc.minutes} min, {n} tracks.']

    out.append(
        f"Opens at {slots[0].bpm:.0f} BPM / {slots[0].lufs:.0f} LUFS, "
        f"peaks at {slots[peak_i].bpm:.0f} BPM / {slots[peak_i].lufs:.0f} LUFS "
        f"(track {peak_i + 1}), closes at {slots[-1].bpm:.0f} BPM / {slots[-1].lufs:.0f} LUFS."
    )

    # Harmonic narrative — compatibility, key variety, energy-boost lifts.
    distinct = len({s.camelot for s in slots})
    boosts = [
        (i, slots[i].camelot, slots[i + 1].camelot)
        for i in range(n - 1)
        if camelot.energy_boost(slots[i].camelot, slots[i + 1].camelot)
    ]
    harm = (f"Harmonic flow: {report.harmonic_compat_pct:.0%} compatible across "
            f"{max(0, n - 1)} transitions, {distinct} keys.")
    if boosts:
        i, a, b = boosts[0]
        harm += f" Energy-boost lift {a}→{b} at track {i + 1}."
    if report.longest_key_run > 1:
        harm += f" Longest single-key run: {report.longest_key_run}."
    out.append(harm)

    # Section choices worth calling out (ADR 0004 — the set plays *parts*).
    notable: list[str] = []
    for i, s in enumerate(slots):
        if not s.section_label:
            continue
        if i == peak_i and s.section_label in ("drop", "chorus"):
            notable.append(f"rides the {s.section_label} of {s.display} at the apex")
        elif i == 0:
            notable.append(f"opens on the {s.section_label} of {s.display}")
        elif i == n - 1:
            notable.append(f"exits on the {s.section_label} of {s.display}")
    if notable:
        out.append("Section use: " + "; ".join(notable[:3]) + ".")

    if report.rough_transitions:
        rough = ", ".join(f"{i + 1}→{i + 2}" for i in report.rough_transitions)
        out.append(f"Watch the transitions at {rough}.")
    if report.notes:
        out.append("Notes: " + "; ".join(report.notes) + ".")
    out.append(
        f"Verdict: {'clean' if report.passed else 'needs work'} — "
        f"max BPM jump {report.max_bpm_jump:.1f}, energy-arc RMSE {report.energy_arc_rmse:.1f} LUFS."
    )
    return "\n".join(out)
