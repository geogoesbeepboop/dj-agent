"""`python -m dj.agents.generate` — the Phase 3 entry point: brief → approved set.

Ties the agent stack together: Architect (brief → arc) → Selector (generate →
verify → revise against the Critic) → HITL gate (approve the plan). An approved
set ships in **two playable forms** (ADR 0010):
  - **manual mode** (always): a `rekordbox.xml` + `.m3u8` next to the renders —
    import into rekordbox and play it yourself; the order, keys, BPM, and the
    planned MIX IN/OUT cue points are already on every track.
  - **automatic mode** (`--render`): the Phase 4 Mixer renders one continuous
    beatmatched file — press play.

    uv run python -m dj.agents.generate "2-hr sunset rooftop, deep → melodic, slow build"
    uv run python -m dj.agents.generate "peak-time techno" --minutes 60 --render --explain

A genre profile (dj/profiles.py) is detected from the brief — or forced with
`--genre` — and shapes everything: arc BPM/LUFS ranges, Critic thresholds, play
spans, crossfade phrase caps, and tempo-stretch limits. Track count is derived
from `--minutes` (genre-typical airtime per track) unless `--tracks` is given.
By default it uses a live model (dj.llm, Anthropic) for the Architect + Selector; pass
`--offline` to run the deterministic arc + greedy selector with no API calls.
`--explain` narrates the set; every generation is logged for the set-acceptance
eval and recently-played tracks are skipped unless `--allow-repeats`.
`--rekordbox <path>` overrides where the XML lands.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dj.agents import architect, hitl, selector
from dj.config import settings


def generate(
    brief: str,
    *,
    minutes: int = 90,
    tracks: int | None = None,
    offline: bool = False,
    render: bool = False,
    explain: bool = False,
    allow_repeats: bool = False,
    rekordbox_path: str | None = None,
    genre: str | None = None,
) -> int:
    from dj import persist, profiles
    from dj.critic import evaluate_set

    if not settings.db_enabled:
        print("DATABASE_URL not set — ingest a library first (python -m dj.curator <folder>).")
        return 1

    profile = profiles.get(genre) if genre else profiles.detect(brief)
    print(f"[profile] genre: {profile.name} "
          f"({profile.bpm_range[0]:.0f}–{profile.bpm_range[1]:.0f} BPM, "
          f"blends ≤{profile.max_xfade_bars} bars, stretch ±{profile.max_stretch:.0%})")

    model = None if offline else architect.default_model()
    n = tracks if tracks is not None else selector.tracks_for_minutes(
        minutes, avg_slot_minutes=profile.avg_slot_minutes)
    print(f"[architect] planning arc for: {brief!r}")
    arc = architect.plan_arc(brief, minutes=minutes, model=model, profile=profile)
    print(f"[architect] arc '{arc.name}': "
          + " → ".join(f"{p.bpm:.0f}bpm/{p.lufs:.0f}LUFS" for p in arc.points))

    exclude = None if allow_repeats else persist.recent_paths()
    extra = f" (excluding {len(exclude)} recently-played)" if exclude else ""
    print(f"[selector] choosing ~{n} tracks for ~{minutes} min…{extra}")
    plan = selector.select(brief, arc, model=model, n=n, exclude_paths=exclude,
                           profile=profile)
    if not plan.slots:
        print("[selector] no candidates matched — is the library ingested for this BPM band?")
        return 1

    if explain:
        from dj.agents.explain import narrate

        print("\n" + narrate(plan) + "\n")

    report = evaluate_set(plan, profile.thresholds())   # graded on the GENRE's bars
    approved = hitl.confirm(plan, report)
    hist = persist.save_plan(plan, brief=brief, approved=approved)  # set-acceptance record
    print(f"[persist] logged this set → {hist}")
    if not approved:
        print("Not approved — stopping before export/render.")
        return 0

    xml, m3u, sheet = _export_set(plan, rekordbox_path, report)
    print(f"[export] manual mode → {xml}")
    print("         import in rekordbox: Preferences → Advanced → Database → rekordbox xml,")
    print("         then drag the playlist in — cues, BPM, and key are already set.")
    print(f"[export] plain playlist → {m3u}")
    print(f"[export] set sheet (print me) → {sheet}")

    if render:
        from dj.mixer import render_set

        out = render_set(plan)
        print(f"[mixer] automatic mode rendered → {out}")
    else:
        print("\n(approved — re-run with --render for the press-play beatmatched mix.)")
    return 0


def _export_set(plan, rekordbox_path: str | None, report=None) -> tuple[str, str, str]:
    """Write the manual-mode artifacts (rekordbox.xml + m3u8 + set sheet)."""
    from dj.export import write_m3u8, write_rekordbox_xml, write_setsheet
    from dj.vibe import store

    safe = "".join(c if c.isalnum() else "-" for c in plan.arc.name).strip("-") or "set"
    base = Path(settings.output_dir) / safe
    durations = store.track_durations(plan.paths)
    xml = write_rekordbox_xml(plan, rekordbox_path or f"{base}.rekordbox.xml", durations=durations)
    m3u = write_m3u8(plan, f"{base}.m3u8", durations=durations)
    sheet = write_setsheet(plan, f"{base}.setsheet.md", durations=durations, report=report)
    return xml, m3u, sheet


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print('usage: python -m dj.agents.generate "<vibe brief>" '
              "[--minutes N] [--tracks N] [--genre house|techno|…] [--offline] "
              "[--render] [--explain] [--allow-repeats] [--rekordbox <out.xml>]")
        return 1

    brief = argv[0]
    rest = argv[1:]
    minutes = int(_opt_value(rest, "--minutes") or 90)
    tracks_opt = _opt_value(rest, "--tracks")
    tracks = int(tracks_opt) if tracks_opt else None     # None → derive from minutes
    return generate(
        brief, minutes=minutes, tracks=tracks,
        offline="--offline" in rest, render="--render" in rest,
        explain="--explain" in rest, allow_repeats="--allow-repeats" in rest,
        rekordbox_path=_opt_value(rest, "--rekordbox"),
        genre=_opt_value(rest, "--genre"),
    )


def _opt_value(args: list[str], flag: str) -> str | None:
    """Return the value following `flag` in args, or None if the flag is absent."""
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


if __name__ == "__main__":
    raise SystemExit(main())
