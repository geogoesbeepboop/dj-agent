"""The human-in-the-loop gate: present the set plan, approve before rendering.

The one high-value HITL checkpoint (docs/architecture.md): the Selector proposes
the plan as *data* — ordered tracks, the *sections* it'll use, cue points, the
target-vs-actual energy arc, and any rough transitions — and I approve or nudge
before the Mixer spends compute on audio. Reading one of these and saying "yes"
*is* the set-acceptance eval metric.

`render_plan` is a pure formatter (unit-tested); `confirm` is the only part that
does I/O and is gated by `HITL_LEVEL` (`full` pauses, `none` auto-approves for
eval runs).
"""

from __future__ import annotations

from dj.config import settings
from dj.critic import SetReport, evaluate_set, transition
from dj.plan import SetPlan


def render_plan(plan: SetPlan, report: SetReport | None = None) -> str:
    """A human-readable view of the set: tracklist + sections + arc fit + flags."""
    from dj.mixer import set_duration_seconds

    report = report or evaluate_set(plan)
    rough = set(report.rough_transitions)
    arc = plan.arc
    est_min = set_duration_seconds(plan) / 60.0
    lines = [
        f"━━ SET PLAN: {arc.name} ({arc.minutes} min, {len(plan)} tracks) ━━",
        f"arc shape: {' → '.join(f'{p.bpm:.0f}bpm/{p.lufs:.0f}LUFS' for p in arc.points)}",
        f"estimated length: ~{est_min:.0f} min (target {arc.minutes})",
        "",
    ]
    for i, s in enumerate(plan.slots):
        t = arc.target_at(s.position)
        cue = ""
        if s.cue_start_s is not None:
            cue = f"  cue {s.cue_start_s:.0f}–{s.cue_end_s:.0f}s"
        lines.append(
            f"{i + 1:2}. {s.display}\n"
            f"     {s.bpm:.0f} BPM · {s.camelot} · {s.lufs:.0f} LUFS "
            f"(target {t.bpm:.0f}/{t.lufs:.0f}) · taste {s.taste_score:.2f}{cue}"
        )
        if i in rough:
            nxt = plan.slots[i + 1]
            why = ", ".join(transition(s, nxt).reasons) or "rough"
            lines.append(f"     ⚠ into {nxt.display}: {why}")
    lines += [
        "",
        f"verdict: {'✓ PASS' if report.passed else '✗ needs work'}  "
        f"| harmonic {report.harmonic_compat_pct:.0%} "
        f"| max BPM jump {report.max_bpm_jump:.1f} "
        f"| energy-arc RMSE {report.energy_arc_rmse:.1f} LUFS",
    ]
    if report.notes:
        lines.append("notes: " + "; ".join(report.notes))
    return "\n".join(lines)


def confirm(plan: SetPlan, report: SetReport | None = None, *, level: str | None = None) -> bool:
    """Show the plan and gate on approval. `HITL_LEVEL=none` auto-approves."""
    level = level or settings.hitl_level
    report = report or evaluate_set(plan)
    print(render_plan(plan, report))
    if level == "none":
        print("\n[HITL_LEVEL=none] auto-approved.")
        return True
    try:
        ans = input("\nApprove this set for rendering? [y/N] ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes")
