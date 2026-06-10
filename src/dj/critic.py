"""The Critic: deterministic scoring of a transition and of a whole set.

No LLM, no audio (yet) — pure math over the structured columns the Curator
computed. This serves two roles:
  - **the Selector's verifier** in its generate→verify→revise loop (Phase 3): a
    proposed SetPlan is graded here, and failures feed the next revision, and
  - **the eval scorecard core** (Phase 5): BPM continuity, harmonic-compat %, and
    Energy-arc RMSE (in LUFS) are exactly these numbers.

Camelot compatibility is a *hard* constraint (a key clash is a train wreck);
BPM/energy jumps and arc fit are *soft* — penalized and surfaced, not forbidden,
so the Selector can trade a slightly rough transition for a much better track.
The audio-level transition smoothness (spectral discontinuity at the actual mix)
is a Phase 5 add-on once the Mixer renders; this scores the *plan*.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from dj.audio import camelot
from dj.plan import SetPlan, Slot


@dataclass(frozen=True)
class Thresholds:
    """Accept/reject bars for a set. Defaults are deliberately forgiving v1."""

    max_bpm_jump: float = 6.0            # BPM between adjacent slots
    max_lufs_jump: float = 5.0           # LUFS between adjacent slots
    min_harmonic_compat: float = 0.7     # fraction of transitions Camelot-OK
    max_arc_rmse_lufs: float = 4.0       # set energy curve vs the target arc
    max_artist_repeats: int = 0          # back-to-back same-artist transitions
    min_artist_gap: int = 3              # same artist must be ≥ this many slots apart
    max_key_run: int = 4                 # consecutive identical-Camelot slots before it's monotonous


@dataclass
class TransitionScore:
    bpm_jump: float
    lufs_jump: float
    harmonic_distance: int
    compatible: bool
    artist_clash: bool
    rough: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class SetReport:
    n: int
    avg_bpm_jump: float
    max_bpm_jump: float
    harmonic_compat_pct: float
    energy_arc_rmse: float
    artist_repeats: int
    rough_transitions: list[int]         # i → the slot[i] → slot[i+1] transition
    passed: bool
    notes: list[str] = field(default_factory=list)
    close_artist_pairs: list[tuple[int, int]] = field(default_factory=list)  # (i,j) same artist, too close
    longest_key_run: int = 0             # longest run of identical Camelot codes


def transition(a: Slot, b: Slot, thresholds: Thresholds | None = None) -> TransitionScore:
    """Grade the A→B transition. `rough` = a hard key clash or an out-of-bounds jump."""
    th = thresholds or Thresholds()
    bpm_jump = abs(a.bpm - b.bpm)
    lufs_jump = abs(a.lufs - b.lufs)
    compatible = _compat(a.camelot, b.camelot)
    dist = _camelot_distance(a.camelot, b.camelot)
    artist_clash = bool(a.artist) and a.artist == b.artist

    reasons: list[str] = []
    if not compatible:
        reasons.append(f"key clash {a.camelot}→{b.camelot}")
    if bpm_jump > th.max_bpm_jump:
        reasons.append(f"bpm jump {bpm_jump:.1f}")
    if lufs_jump > th.max_lufs_jump:
        reasons.append(f"energy jump {lufs_jump:.1f} LUFS")
    if artist_clash:
        reasons.append(f"same artist ({a.artist})")
    rough = bool(reasons)
    return TransitionScore(bpm_jump, lufs_jump, dist, compatible, artist_clash, rough, reasons)


def evaluate_set(plan: SetPlan, thresholds: Thresholds | None = None) -> SetReport:
    """Grade a whole ordered set against its arc + the smoothness thresholds."""
    th = thresholds or Thresholds()
    slots = plan.slots
    n = len(slots)
    if n == 0:
        return SetReport(0, 0, 0, 0, 0, 0, [], False, ["empty set"])

    transitions = [transition(a, b, th) for a, b in zip(slots, slots[1:])]
    bpm_jumps = [t.bpm_jump for t in transitions] or [0.0]
    compat = [t.compatible for t in transitions]
    rough = [i for i, t in enumerate(transitions) if t.rough]
    artist_repeats = sum(t.artist_clash for t in transitions)
    harmonic_pct = (sum(compat) / len(compat)) if compat else 1.0
    arc_rmse = energy_arc_rmse(plan)
    close_pairs = _close_artist_pairs(slots, th.min_artist_gap)
    key_run = _longest_key_run(slots)

    passed = (
        harmonic_pct >= th.min_harmonic_compat
        and max(bpm_jumps) <= th.max_bpm_jump
        and arc_rmse <= th.max_arc_rmse_lufs
        and artist_repeats <= th.max_artist_repeats
    )
    notes: list[str] = []
    if harmonic_pct < th.min_harmonic_compat:
        notes.append(f"harmonic compat {harmonic_pct:.0%} < {th.min_harmonic_compat:.0%}")
    if max(bpm_jumps) > th.max_bpm_jump:
        notes.append(f"max bpm jump {max(bpm_jumps):.1f} > {th.max_bpm_jump}")
    if arc_rmse > th.max_arc_rmse_lufs:
        notes.append(f"energy-arc RMSE {arc_rmse:.1f} LUFS > {th.max_arc_rmse_lufs}")
    if artist_repeats > th.max_artist_repeats:
        notes.append(f"{artist_repeats} back-to-back same-artist")
    # Soft signals (reported + fed to the revise loop, not hard pass/fail gates):
    for i, j in close_pairs:
        notes.append(f"{slots[i].artist} repeats too close (slots {i + 1}→{j + 1}, gap {j - i})")
    if key_run > th.max_key_run:
        notes.append(f"key sits on one Camelot code for {key_run} slots (monotonous)")

    return SetReport(
        n=n,
        avg_bpm_jump=float(np.mean(bpm_jumps)),
        max_bpm_jump=float(max(bpm_jumps)),
        harmonic_compat_pct=harmonic_pct,
        energy_arc_rmse=arc_rmse,
        artist_repeats=artist_repeats,
        rough_transitions=rough,
        passed=passed,
        notes=notes,
        close_artist_pairs=close_pairs,
        longest_key_run=key_run,
    )


def energy_arc_rmse(plan: SetPlan) -> float:
    """RMSE between the set's actual LUFS curve and the arc's target (ADR 0005)."""
    if not plan.slots:
        return 0.0
    errs = [s.lufs - plan.arc.target_at(s.position).lufs for s in plan.slots]
    return float(np.sqrt(np.mean(np.square(errs))))


def _close_artist_pairs(slots: list[Slot], min_gap: int) -> list[tuple[int, int]]:
    """Pairs of slots with the same artist closer than `min_gap` apart (gap > 1).

    Back-to-back (gap 1) is already counted as artist_repeats; this catches the
    'same artist with one track between them' case the adjacent check misses.
    """
    pairs: list[tuple[int, int]] = []
    for i, a in enumerate(slots):
        if not a.artist:
            continue
        for j in range(i + 2, min(i + min_gap, len(slots))):
            if slots[j].artist == a.artist:
                pairs.append((i, j))
    return pairs


def _longest_key_run(slots: list[Slot]) -> int:
    """Longest run of consecutive identical Camelot codes — a monotony signal."""
    best = run = 0
    prev = None
    for s in slots:
        run = run + 1 if s.camelot == prev else 1
        best = max(best, run)
        prev = s.camelot
    return best


def _compat(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True            # unknown key → don't fail the transition on it
    try:
        return camelot.compatible(a, b)
    except Exception:
        return True


def _camelot_distance(a: str | None, b: str | None) -> int:
    if not a or not b:
        return 0
    try:
        return camelot.distance(a, b)
    except Exception:
        return 0
