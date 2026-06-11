"""The Architect: a vibe brief → a target energy/BPM arc (the set's shape).

A small, high-value LLM step (ADR 0006): translate "2-hr sunset rooftop, deep →
melodic, slow build" into a handful of arc control points (position, BPM, LUFS).
The arc is an explicit artifact (`dj/arc.py`) the deterministic Selector and
Critic consume — the LLM never touches audio or ranks tracks, it just shapes the
journey.

The model is an injectable callable (`messages -> str`) so this is unit-testable
with a fake model and runs offline: when no model is given, or the model's output
won't parse, it falls back to a deterministic shape derived from brief keywords.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from dj.arc import DEFAULT_BPM, DEFAULT_LUFS, Arc, ArcPoint, shape_from_brief

Model = Callable[[list[dict]], str]

_SYSTEM = """You are a DJ set architect. Given a vibe brief, output the target
ENERGY ARC of the set as JSON — nothing else. Energy is in LUFS (−24 ≈ very quiet
warm-up, −6 ≈ peak-time loud). BPM is the tempo at that point in the set.

Return exactly:
{"name": "<short label>", "points": [{"position": 0.0, "bpm": 120, "lufs": -18}, ...]}

Rules: 4–7 points; position runs 0.0 (first track) → 1.0 (last); BPM and LUFS
should rise/fall to match the brief's journey (a "slow build" rises gradually; a
"peak-time" set tops out ~70% through; a "wind-down" descends at the end)."""


def plan_arc(
    brief: str,
    minutes: int = 90,
    *,
    model: Model | None = None,
    n_points: int = 5,
    profile=None,                       # dj.profiles.GenreProfile | None
) -> Arc:
    """Plan the set's arc from a free-text brief. Never raises — falls back to a
    deterministic shape if there's no model or the model's JSON won't parse.

    A genre profile (when given) seeds the BPM/LUFS ranges and the default shape
    — both for the deterministic fallback and as context the LLM sees."""
    if model is not None:
        try:
            user = f"Brief: {brief}\nLength: {minutes} min."
            if profile is not None:
                lo, hi = profile.bpm_range
                user += f"\nGenre: {profile.name} (typical {lo:.0f}–{hi:.0f} BPM)."
            text = model([
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ])
            arc = _parse_arc(text, minutes)
            if arc is not None and len(arc.points) >= 2:
                return arc
        except Exception:
            pass  # any LLM/parse failure → deterministic fallback below
    return _deterministic_arc(brief, minutes, n_points, profile)


def default_model(tier: str = "hard", temperature: float = 0.3) -> Model:
    """A live model callable backed by dj.llm (Anthropic, tier-routed)."""

    def _call(messages: list[dict]) -> str:
        from dj.llm import complete  # lazy: tests/offline runs never import it

        return complete(messages, tier=tier, temperature=temperature, max_tokens=1024)

    return _call


def _deterministic_arc(brief: str, minutes: int, n_points: int, profile=None) -> Arc:
    default_shape = profile.default_shape if profile is not None else "build"
    shape = shape_from_brief(brief, default=default_shape)
    bpm = profile.bpm_range if profile is not None else _bpm_range(brief)
    lufs = profile.lufs_range if profile is not None else _lufs_range(brief)
    return Arc.from_shape(
        name=_name_from_brief(brief), minutes=minutes, shape=shape,
        bpm=bpm, lufs=lufs, n_points=n_points,
    )


def _parse_arc(text: str, minutes: int) -> Arc | None:
    """Extract the JSON arc object from a model response (tolerant of prose)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    raw = data.get("points") or []
    points: list[ArcPoint] = []
    for p in raw:
        try:
            pos = min(max(float(p["position"]), 0.0), 1.0)   # positions live in 0..1
            points.append(ArcPoint(position=pos, bpm=float(p["bpm"]), lufs=float(p["lufs"])))
        except (KeyError, TypeError, ValueError):
            continue
    if len(points) < 2:
        return None
    points.sort(key=lambda p: p.position)
    if points[-1].position <= points[0].position:
        return None    # degenerate (all positions equal) → fall back to a real shape
    return Arc(name=str(data.get("name") or "set"), minutes=minutes, points=points)


def _bpm_range(brief: str) -> tuple[float, float]:
    b = brief.lower()
    if any(w in b for w in ("techno", "driving", "hard", "warehouse")):
        return (124.0, 134.0)
    if any(w in b for w in ("dnb", "drum and bass", "jungle")):
        return (168.0, 176.0)
    if any(w in b for w in ("downtempo", "ambient", "chill", "lounge", "sunset")):
        return (100.0, 116.0)
    if any(w in b for w in ("house", "deep", "melodic")):
        return (118.0, 126.0)
    return DEFAULT_BPM


def _lufs_range(brief: str) -> tuple[float, float]:
    b = brief.lower()
    if any(w in b for w in ("dinner", "lounge", "background", "ambient", "chill")):
        return (-22.0, -14.0)
    if any(w in b for w in ("peak", "banger", "main room", "festival")):
        return (-16.0, -6.0)
    return DEFAULT_LUFS


def _name_from_brief(brief: str) -> str:
    words = re.findall(r"[a-zA-Z]+", brief)[:4]
    return " ".join(words).title() or "set"
