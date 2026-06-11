"""The eval scorecard (Phase 5): put set quality in numbers — and in CI.

DJ quality has semi-objective oracles (BUILD_PLAN), so we score every generated
set the way the migration agent scored its diffs. This module is the **scorecard
core** + an A/B runner; like the rest of the codebase it splits a PURE layer
(metrics over already-fetched numbers/vectors, unit-tested with synthetic data)
from a thin DB-orchestrated layer.

The deterministic *transition* metrics — BPM continuity, harmonic-compat %,
energy-arc RMSE (LUFS), artist spacing — come straight from the Critic's
`SetReport`; the Critic was built to be reused here (docs/set-generation.md). This
module adds the two *personal* metrics the Critic can't see:

  - **taste-match** — mean cosine of the set's taste vectors to the centroid of my
    hand-labeled favorites. "Does this set sit where the music I love sits?"
  - **discovery ratio** — fraction of the set I had *not* already labeled or
    favorited. A good set is mine *and* shows me something new.

`compare_to_acoustic_baseline()` is the Phase 3 verify goal as a one-liner: build
the set with the full blend vs. a CLAP-only (taste-off) blend and show the blended
set wins on taste-match. The deterministic greedy selector makes that A/B run with
no API key.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dj.critic import SetReport, Thresholds, evaluate_set
from dj.plan import SetPlan


@dataclass
class Scorecard:
    """Every metric for one set — the deterministic gates + the personal signal."""

    n: int
    avg_bpm_jump: float
    max_bpm_jump: float
    harmonic_compat_pct: float
    energy_arc_rmse: float
    artist_repeats: int
    taste_match: float          # mean cosine of set taste-vecs to my favorites (−1..1)
    discovery_ratio: float      # fraction of the set I had NOT already labeled/favorited
    n_with_taste: int           # slots that had a taste vector (taste_match's support)
    passed: bool                # the Critic's hard/soft gates


# --- pure metric core (unit-tested with synthetic vectors) ------------------


def _centroid(vecs: list[np.ndarray] | np.ndarray) -> np.ndarray | None:
    """L2-normalized mean of a set of vectors (the favorites' center of mass)."""
    arr = np.asarray(vecs, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] == 0:
        return None
    c = arr.mean(axis=0)
    norm = np.linalg.norm(c)
    return c / norm if norm > 1e-9 else None


def taste_match(set_vecs: list[np.ndarray], favorite_vecs: list[np.ndarray]) -> float:
    """Mean cosine of the set's taste vectors to my favorites' centroid.

    Both sides are L2-normalized (taste/embed.py), so a dot product is cosine.
    0.0 when either side is empty — the cold-start before I've tagged anything.
    """
    fav = _centroid(favorite_vecs)
    if fav is None or len(set_vecs) == 0:
        return 0.0
    sv = np.asarray(set_vecs, dtype=np.float64)
    return float(np.mean(sv @ fav))


def discovery_ratio(known_flags: list[bool]) -> float:
    """Fraction of slots I had NOT already labeled/favorited (`known=False`)."""
    if not known_flags:
        return 0.0
    return float(np.mean([0.0 if k else 1.0 for k in known_flags]))


def from_report(
    report: SetReport, *, taste_match: float, discovery_ratio: float, n_with_taste: int
) -> Scorecard:
    """Assemble a Scorecard from a Critic SetReport + the personal metrics."""
    return Scorecard(
        n=report.n,
        avg_bpm_jump=report.avg_bpm_jump,
        max_bpm_jump=report.max_bpm_jump,
        harmonic_compat_pct=report.harmonic_compat_pct,
        energy_arc_rmse=report.energy_arc_rmse,
        artist_repeats=report.artist_repeats,
        taste_match=taste_match,
        discovery_ratio=discovery_ratio,
        n_with_taste=n_with_taste,
        passed=report.passed,
    )


def render(card: Scorecard) -> str:
    """One-block human (and log) view of a scorecard."""
    return (
        f"n={card.n}  {'✓ PASS' if card.passed else '✗ needs work'}\n"
        f"  harmonic-compat   {card.harmonic_compat_pct:.0%}\n"
        f"  BPM jump          avg {card.avg_bpm_jump:.1f} / max {card.max_bpm_jump:.1f}\n"
        f"  energy-arc RMSE   {card.energy_arc_rmse:.1f} LUFS\n"
        f"  artist repeats    {card.artist_repeats}\n"
        f"  taste-match       {card.taste_match:+.3f}  (over {card.n_with_taste}/{card.n} tracks)\n"
        f"  discovery ratio   {card.discovery_ratio:.0%}"
    )


# --- DB-orchestrated scoring (skipped when DATABASE_URL is empty) -----------


def evaluate_plan(plan: SetPlan, thresholds: Thresholds | None = None) -> Scorecard:
    """Full scorecard for a plan: Critic metrics + taste-match + discovery (needs DB)."""
    from dj.vibe import store

    report = evaluate_set(plan, thresholds)
    info = store.taste_eval_rows(plan.paths)
    favorites = store.favorite_taste_vectors()

    set_vecs = [
        info[p]["taste_vec"]
        for p in plan.paths
        if info.get(p, {}).get("taste_vec") is not None
    ]
    known = [
        bool(info.get(p, {}).get("is_favorite") or info.get(p, {}).get("taste_source") == "manual")
        for p in plan.paths
    ]
    return from_report(
        report,
        taste_match=taste_match(set_vecs, favorites),
        discovery_ratio=discovery_ratio(known),
        n_with_taste=len(set_vecs),
    )


def compare_to_acoustic_baseline(
    brief: str,
    *,
    minutes: int = 90,
    tracks: int = 12,
    model=None,
) -> tuple[Scorecard, Scorecard]:
    """A/B the blended Selector against a CLAP-only (taste-off) baseline.

    The Phase 3 verify goal: the blended set should beat the acoustic-only set on
    taste-match. Runs deterministically (greedy selector) with `model=None`, so it
    needs only an ingested+tagged library — no API key. Returns (blended, baseline).
    """
    from dj.agents import architect, selector
    from dj.taste.score import BlendWeights

    arc = architect.plan_arc(brief, minutes=minutes, model=model)
    blended = selector.select(brief, arc, model=model, n=tracks)
    acoustic_only = selector.select(
        brief, arc, model=model, n=tracks,
        weights=BlendWeights(acoustic=1.0, taste=0.0, rating=0.0),
    )
    return evaluate_plan(blended), evaluate_plan(acoustic_only)


def main(argv: list[str] | None = None) -> int:
    import sys

    from dj.config import settings

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print('usage: python -m dj.evals.runner "<vibe brief>" [--tracks N] [--minutes N]')
        return 1
    if not settings.db_enabled:
        print("DATABASE_URL not set — eval needs the ingested vibe DB.")
        return 1

    brief = argv[0]
    tracks = int(_opt(argv[1:], "--tracks") or 12)
    minutes = int(_opt(argv[1:], "--minutes") or 90)

    print(f"[eval] A/B for {brief!r}: blended vs CLAP-only baseline (greedy, offline)")
    blended, baseline = compare_to_acoustic_baseline(brief, minutes=minutes, tracks=tracks)
    print("\n── blended (acoustic + taste) ──")
    print(render(blended))
    print("\n── baseline (CLAP-only) ──")
    print(render(baseline))
    delta = blended.taste_match - baseline.taste_match
    print(f"\ntaste-match delta: {delta:+.3f} "
          f"({'blended wins' if delta > 0 else 'baseline ties/wins — tune α/β'})")
    return 0


def _opt(args: list[str], flag: str) -> str | None:
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


if __name__ == "__main__":
    raise SystemExit(main())
