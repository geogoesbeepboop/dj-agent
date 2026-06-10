"""The energy/BPM arc — the set's shape over time, as an explicit data artifact.

This is the contract between the Architect (which *produces* an arc from a vibe
brief) and the Selector + Critic (which *order tracks to it* and *score against
it*). Keeping the arc as plain data — a few control points the rest of the system
interpolates — means the LLM only has to emit a handful of numbers, and every
downstream consumer is pure, deterministic, and unit-testable (ADR 0006).

Energy is in **LUFS** (cross-track comparable, ADR 0005), so "warm-up at −18,
peak at −9" is a real, schedulable target the Energy-arc-RMSE eval measures.
"""

from __future__ import annotations

from dataclasses import dataclass

# Sensible defaults for a club-ish set; the Architect overrides per brief.
DEFAULT_BPM = (118.0, 126.0)
DEFAULT_LUFS = (-18.0, -8.0)
SHAPES = ("flat", "build", "peak", "wave", "down")


@dataclass(frozen=True)
class ArcPoint:
    position: float   # 0..1 over the set (0 = first track, 1 = last)
    bpm: float
    lufs: float


@dataclass
class Arc:
    """A target energy/tempo curve as control points the system interpolates."""

    name: str
    minutes: int
    points: list[ArcPoint]

    def target_at(self, position: float) -> ArcPoint:
        """Linearly interpolate the target BPM + LUFS at any set position 0..1."""
        pts = self.points
        if not pts:
            return ArcPoint(position, DEFAULT_BPM[0], DEFAULT_LUFS[0])
        position = min(max(position, 0.0), 1.0)
        if position <= pts[0].position:
            return ArcPoint(position, pts[0].bpm, pts[0].lufs)
        if position >= pts[-1].position:
            return ArcPoint(position, pts[-1].bpm, pts[-1].lufs)
        for lo, hi in zip(pts, pts[1:]):
            if lo.position <= position <= hi.position:
                span = hi.position - lo.position
                t = 0.0 if span < 1e-9 else (position - lo.position) / span
                return ArcPoint(position, _lerp(lo.bpm, hi.bpm, t), _lerp(lo.lufs, hi.lufs, t))
        return ArcPoint(position, pts[-1].bpm, pts[-1].lufs)

    def bpm_band(self, slack: float = 4.0) -> tuple[float, float]:
        """The [min, max] BPM the whole arc spans, padded — the retrieval filter."""
        bpms = [p.bpm for p in self.points] or list(DEFAULT_BPM)
        return (min(bpms) - slack, max(bpms) + slack)

    def to_dict(self) -> dict:
        """Plain-data form for persisting / sharing a plan (no behavior)."""
        return {
            "name": self.name,
            "minutes": self.minutes,
            "points": [{"position": p.position, "bpm": p.bpm, "lufs": p.lufs} for p in self.points],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Arc":
        return cls(
            name=d.get("name", "set"),
            minutes=int(d.get("minutes", 90)),
            points=[ArcPoint(float(p["position"]), float(p["bpm"]), float(p["lufs"]))
                    for p in d.get("points", [])],
        )

    @classmethod
    def from_shape(
        cls,
        name: str,
        minutes: int = 90,
        shape: str = "build",
        bpm: tuple[float, float] = DEFAULT_BPM,
        lufs: tuple[float, float] = DEFAULT_LUFS,
        n_points: int = 5,
    ) -> "Arc":
        """Build a smooth arc of `n_points` from a named shape and value ranges.

        The deterministic fallback the Architect uses when there's no LLM (or its
        output won't parse), and the baseline the eval A/B compares against.
        """
        curve = _shape_curve(shape, n_points)
        points = [
            ArcPoint(
                position=i / (n_points - 1) if n_points > 1 else 0.0,
                bpm=_lerp(bpm[0], bpm[1], c),
                lufs=_lerp(lufs[0], lufs[1], c),
            )
            for i, c in enumerate(curve)
        ]
        return cls(name=name, minutes=minutes, points=points)


def shape_from_brief(brief: str) -> str:
    """Pick an arc shape from keywords in a free-text vibe brief (heuristic)."""
    b = brief.lower()
    if any(w in b for w in ("wave", "ebb", "peaks and valley", "up and down")):
        return "wave"
    if any(w in b for w in ("wind down", "wind-down", "cool down", "descend", "closing", "comedown")):
        return "down"
    if any(w in b for w in ("peak time", "peak-time", "banger", "high energy", "main room")):
        return "peak"
    if any(w in b for w in ("steady", "flat", "background", "dinner", "lounge")):
        return "flat"
    return "build"  # the default: a slow rise, the most common request


def _shape_curve(shape: str, n: int) -> list[float]:
    """A list of n values in 0..1 describing intensity over the set."""
    if n <= 1:
        return [0.5]
    xs = [i / (n - 1) for i in range(n)]
    if shape == "flat":
        return [0.5 for _ in xs]
    if shape == "build":
        return [x for x in xs]                                    # linear rise
    if shape == "down":
        return [1.0 - x for x in xs]                              # linear fall
    if shape == "peak":
        # rise to a peak ~70% through, then ease back down.
        peak = 0.7
        return [x / peak if x <= peak else 1.0 - 0.5 * (x - peak) / (1 - peak) for x in xs]
    if shape == "wave":
        import math

        return [0.5 - 0.5 * math.cos(2 * math.pi * x) for x in xs]  # one up-down cycle
    return [x for x in xs]


def _lerp(a: float, b: float, t: float) -> float:
    return float(a + (b - a) * t)
