"""The Selector: order tracks (and the *parts* of them) to follow the arc.

This is the agentic core of Phase 3 — a **generate → verify → revise** loop:

  1. retrieve a blended candidate pool (acoustic + taste) inside the arc's BPM band,
  2. the LLM proposes an ordered set from the pool,
  3. the deterministic **Critic** verifies it (Camelot, BPM jumps, energy-arc fit),
  4. on failure, the Critic's notes feed the next revision — up to a budget,
  5. then the chosen tracks get their *sections* assigned (ADR 0004): the part of
     each track whose energy best fits that moment in the arc.

The whole thing degrades gracefully: with **no model** it runs a deterministic
greedy selector (the eval baseline + the offline fallback), so a usable set comes
out even without an API key. The greedy selector and the parsing/section logic are
pure and unit-tested; the LLM is an injectable seam (ADR 0006).
"""

from __future__ import annotations

import json
import re
from typing import Callable

from dj.agents import tools as tools_default
from dj.agents.tools import TrackCard
from dj.arc import Arc
from dj.critic import SetReport, Thresholds, evaluate_set
from dj.plan import SetPlan, Slot

Model = Callable[[list[dict]], str]

# Greedy cost weights: how much a BPM/LUFS miss vs. a taste win matters, and the
# (large) penalties that keep the chain harmonically compatible + artist-spaced.
_W_BPM, _W_LUFS, _W_TASTE = 1.0, 1.5, 8.0
_INCOMPAT_PENALTY, _ARTIST_PENALTY = 25.0, 15.0
# Within the compatible set, gently prefer the smoother key move (identical/
# relative over a fifth) using camelot.distance — a tiebreak, not a gate.
_W_HARMONIC = 1.0
# Space the same artist across a window (decaying penalty), and nudge off a key
# the set has sat on for a while — both soft, both surfaced by the Critic.
_ARTIST_GAP = 3
_W_KEY_MONOTONY = 2.0

# A mixed track is on air ~3.5 min before the next blends in — turns a set length
# in minutes into a sensible track count when --tracks isn't given (#26). Genre
# profiles override the average (a hip-hop set burns tracks faster than techno).
AVG_SLOT_MINUTES = 3.5


def tracks_for_minutes(
    minutes: int, *, lo: int = 4, hi: int = 40, avg_slot_minutes: float = AVG_SLOT_MINUTES
) -> int:
    """How many tracks ≈ fill `minutes` of continuous mix (clamped to a sane range)."""
    return int(min(hi, max(lo, round(minutes / avg_slot_minutes))))

_SYSTEM = """You are a DJ selector. From the NUMBERED candidate pool, choose an
ordered set that follows the target ENERGY ARC. Output ONLY a JSON array of the
chosen candidate numbers, in play order, e.g. [4, 1, 9, 2].

Optimize for: tracks whose BPM and LUFS track the arc at each position; smooth
Camelot transitions (adjacent tracks in compatible keys); no back-to-back same
artist; lean on higher-taste-score tracks. Pick exactly the requested count."""


def select(
    prompt: str,
    arc: Arc,
    *,
    model: Model | None = None,
    n: int = 12,
    pool: int = 60,
    weights=None,
    thresholds: Thresholds | None = None,
    max_revisions: int = 3,
    exclude_paths: set[str] | None = None,
    profile=None,                       # dj.profiles.GenreProfile | None
    tools=tools_default,
) -> SetPlan:
    """Build a SetPlan for `prompt` following `arc`. Deterministic without a model.

    `profile` (when given) supplies the genre's Critic thresholds and the play-span
    airtime bounds, and stamps the plan so the Mixer renders genre-appropriately."""
    th = thresholds or (profile.thresholds() if profile is not None else Thresholds())
    genre = profile.name if profile is not None else None
    filters: dict = {"bpm": arc.bpm_band()}
    if exclude_paths:
        filters["exclude_paths"] = list(exclude_paths)   # skip recently-played (#24)
    cards = tools.query_vibe_db(prompt, filters=filters, k=pool, weights=weights)
    if not cards:
        return SetPlan(arc=arc, slots=[], genre=genre)
    n = min(n, len(cards))

    if model is None:
        slots = greedy_select(cards, arc, n)
        return _with_sections(SetPlan(arc=arc, slots=slots, genre=genre), tools, profile)

    # generate → verify → revise
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _format_request(cards, arc, n, profile=profile)},
    ]
    best: tuple[SetReport, SetPlan] | None = None
    for _ in range(max_revisions):
        try:
            text = model(messages)
        except Exception:
            break
        slots = parse_selection(text, cards, arc)
        if not slots:
            break
        plan = SetPlan(arc=arc, slots=slots, genre=genre)
        report = evaluate_set(plan, th)
        if best is None or _better(report, best[0]):
            best = (report, plan)
        if report.passed:
            return _with_sections(plan, tools, profile)
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": _critique(report, slots)})

    if best is not None:
        return _with_sections(best[1], tools, profile)
    return _with_sections(
        SetPlan(arc=arc, slots=greedy_select(cards, arc, n), genre=genre), tools, profile
    )


# --- deterministic greedy selection (pure; the baseline + fallback) ---------


def greedy_select(cards: list[TrackCard], arc: Arc, n: int) -> list[Slot]:
    """Order cards to the arc by greedy nearest-fit, harmonic + artist aware.

    At each set position it picks the unused card with the lowest cost: distance
    from the target BPM/LUFS, minus a taste bonus, plus big penalties for a key
    clash or a repeated artist vs. the previous pick. Deterministic and pure."""
    remaining = list(cards)
    slots: list[Slot] = []
    for i in range(n):
        position = i / (n - 1) if n > 1 else 0.0
        target = arc.target_at(position)
        best = min(remaining, key=lambda c: _greedy_cost(c, target, slots))
        remaining.remove(best)
        slots.append(best.to_slot(position))
    return slots


def _greedy_cost(card: TrackCard, target, placed: list[Slot]) -> float:
    cost = (
        _W_BPM * abs(card.bpm - target.bpm)
        + _W_LUFS * abs(card.lufs - target.lufs)
        - _W_TASTE * card.score
    )
    if placed:
        cost += _camelot_penalty(placed[-1].camelot, card.camelot)
    # Windowed artist spacing: full penalty back-to-back, decaying within the window.
    if card.artist:
        for back, s in enumerate(reversed(placed[-_ARTIST_GAP:]), start=1):
            if s.artist == card.artist:
                cost += _ARTIST_PENALTY / back
    # Discourage extending a run of the identical key (harmonic monotony).
    run = 0
    for s in reversed(placed):
        if s.camelot != card.camelot:
            break
        run += 1
    if run >= 2:
        cost += _W_KEY_MONOTONY * (run - 1)
    return cost


def _camelot_penalty(a: str, b: str) -> float:
    from dj.audio import camelot

    try:
        if camelot.compatible(a, b):
            return _W_HARMONIC * camelot.distance(a, b)   # smoother key = cheaper
        return _INCOMPAT_PENALTY
    except Exception:
        return 0.0


# --- LLM selection parsing + section assignment (pure where possible) -------


def parse_selection(text: str, cards: list[TrackCard], arc: Arc) -> list[Slot]:
    """Parse a model's `[2, 0, 5, ...]` choice into ordered Slots (1-based in the
    prompt; tolerant of objects like {"n": 2} and out-of-range/duplicate indices)."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    picks: list[int] = []
    for item in raw:
        idx = item if isinstance(item, int) else (
            item.get("n") or item.get("index") or item.get("number")
            if isinstance(item, dict) else None
        )
        if not isinstance(idx, int):
            continue
        zero = idx - 1                       # prompt numbers candidates from 1
        if 0 <= zero < len(cards) and zero not in picks:
            picks.append(zero)
    if not picks:
        return []
    m = len(picks)
    return [
        cards[zero].to_slot(i / (m - 1) if m > 1 else 0.0)
        for i, zero in enumerate(picks)
    ]


def pick_section(sections, target_lufs: float):
    """Choose the section whose energy best fits the arc target at this moment.

    Realizes ADR 0004's "use the right *part*": the section whose LUFS is closest
    to the target, so a peak slot grabs the drop and a warm-up slot grabs the
    intro/break. Returns None if no section has a known energy."""
    usable = [s for s in sections if getattr(s, "energy_lufs", None) is not None]
    if not usable:
        return None
    return min(usable, key=lambda s: abs(s.energy_lufs - target_lufs))


def plan_play_span(
    sections,
    target_lufs: float,
    *,
    min_play_s: float = 120.0,
    max_play_s: float = 330.0,
):
    """Pick (entry, core, exit) sections — the playable span of one slot.

    A single section is rarely a slot's whole airtime: a DJ mixes IN at a clean
    entry point, rides through the part the moment calls for (the *core* — the
    energy fit the arc targeted), and mixes OUT at a clean exit. So: choose the
    core by energy fit, then the entry/exit pair — entry a mix-in section at or
    before the core, exit a mix-out section at or after it — whose total span
    best fills the genre's airtime window (longest within [min, max]; least-bad
    overflow otherwise). Falls back to the core itself when nothing is flagged.
    Pure; returns None when no section has a known energy."""
    core = pick_section(sections, target_lufs)
    if core is None:
        return None
    ordered = sorted(sections, key=lambda s: s.idx)
    entries = [s for s in ordered if s.idx <= core.idx and getattr(s, "is_mixin", False)]
    exits = [s for s in ordered if s.idx >= core.idx and getattr(s, "is_mixout", False)]
    if not entries:
        entries = [core]
    if not exits:
        exits = [core]

    def cost(entry, exit) -> tuple[int, float]:
        span = exit.end_s - entry.start_s
        if span <= 0:
            return (2, 0.0)
        if span < min_play_s:
            return (1, min_play_s - span)
        if span > max_play_s:
            return (1, span - max_play_s)
        return (0, max_play_s - span)        # in-window: prefer the longer span
    entry, exit = min(
        ((e, x) for e in entries for x in exits), key=lambda pair: cost(*pair)
    )
    return entry, core, exit


def _with_sections(plan: SetPlan, tools, profile=None) -> SetPlan:
    """Assign each slot the play span of its track that best fits the arc here."""
    min_play = profile.min_play_s if profile is not None else 120.0
    max_play = profile.max_play_s if profile is not None else 330.0
    for slot in plan.slots:
        try:
            sections = tools.get_sections(slot.path)
        except Exception:
            continue
        if not sections:
            continue
        target = plan.arc.target_at(slot.position)
        span = plan_play_span(sections, target.lufs,
                              min_play_s=min_play, max_play_s=max_play)
        if span is None:
            continue
        entry, core, exit = span
        slot.section_idx = core.idx
        slot.section_label = core.label
        slot.cue_start_s = entry.start_s
        slot.cue_end_s = exit.end_s
        slot.mixin_label = entry.label
        slot.mixout_label = exit.label
        # Hot-cue the core's hit point when the span starts earlier than it.
        slot.core_start_s = core.start_s if core.start_s > entry.start_s else None
        # The set plays this *part*, not the whole track — so the energy the
        # Critic scores against the arc (and the HITL gate shows) must be the
        # core section's LUFS, not the track average (ADR 0004/0005). Without
        # this the whole point of sections never reaches the arc-fit metric.
        if core.energy_lufs is not None:
            slot.lufs = core.energy_lufs
    return plan


# --- prompt + critique formatting -------------------------------------------


def _format_request(cards: list[TrackCard], arc: Arc, n: int, profile=None) -> str:
    lines = [f"Target arc '{arc.name}' ({arc.minutes} min), pick {n} tracks in order.",
             "Arc targets (position → BPM / LUFS):"]
    if profile is not None:
        keys = ("harmonic keys matter here" if profile.min_harmonic_compat >= 0.7
                else "key clashes are tolerable if the vibe fits")
        lines.insert(1, f"Genre: {profile.name} — keep adjacent BPM jumps "
                        f"≤ {profile.max_bpm_jump:.0f}; {keys}.")
    for i in range(min(n, 6)):
        p = i / (max(n, 2) - 1)
        t = arc.target_at(p)
        lines.append(f"  {p:.2f} → {t.bpm:.0f} BPM / {t.lufs:.0f} LUFS")
    lines.append("\nCandidate pool:")
    for i, c in enumerate(cards, 1):
        lines.append(
            f"  {i}. {c.title or c.path} — {c.artist or '?'} | "
            f"{c.bpm:.0f} BPM | {c.camelot} | {c.lufs:.0f} LUFS | taste {c.score:.2f}"
        )
    return "\n".join(lines)


def _critique(report: SetReport, slots: list[Slot]) -> str:
    parts = ["That set didn't pass. Problems:"]
    parts += [f"  - {note}" for note in report.notes]
    for i in report.rough_transitions:
        a, b = slots[i], slots[i + 1]
        parts.append(f"  - rough transition: {a.display} → {b.display} ({a.camelot}→{b.camelot})")
    parts.append("Revise the order (and swaps) to fix these. Output the JSON array only.")
    return "\n".join(parts)


def _better(a: SetReport, b: SetReport) -> bool:
    """Is report a strictly better than b? (passed wins; else lower arc RMSE.)"""
    if a.passed != b.passed:
        return a.passed
    return (a.energy_arc_rmse, a.max_bpm_jump) < (b.energy_arc_rmse, b.max_bpm_jump)
