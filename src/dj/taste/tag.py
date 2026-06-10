"""vibe-tagging CLI — capture how *I* feel about a track (the taste loop's input).

    uv run python -m dj.taste.tag                 # walk the active-learning queue
    uv run python -m dj.taste.tag <path>          # tag one specific track
    uv run python -m dj.taste.tag --queue [N]     # just list what to label next
    uv run python -m dj.taste.tag --propagate     # spread labels to untagged tracks

Per track it shows what we already know (BPM, key, acoustic neighbors for
context) and asks for a note + rating + role. The note embeds into
`tracks.taste_vec`; rating/role are stored as columns. CLI-first by design — once
the flow feels right it can be wrapped as a `vibe-tagging` skill. See docs/taste.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dj.config import settings

ROLES = ("warmup", "build", "peak", "closer", "tool", "wildcard")


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{label}{suffix}: ").strip()
    except EOFError:
        return default
    return ans or default


def _prompt_rating() -> int | None:
    raw = _prompt("rating 1-5 (enter to skip)")
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= 5:
        return int(raw)
    print("  ! rating must be 1-5; skipping")
    return None


def _prompt_role() -> str | None:
    raw = _prompt(f"role {'/'.join(ROLES)} (enter to skip)")
    if not raw:
        return None
    if raw in ROLES:
        return raw
    print(f"  ! unknown role '{raw}'; skipping")
    return None


def tag_one(path: str) -> bool:
    """Interactively tag a single track. Returns True if a note was saved."""
    from dj.taste import embed
    from dj.vibe import store

    track = store.get_track(path)
    if track is None:
        print(f"  ! not in the DB (ingest it first): {path}")
        return False

    name = track.title or Path(path).name
    print(f"\n♪ {name} — {track.artist or '?'}")
    print(f"  {track.bpm:.0f} BPM · {track.camelot} · {track.genre or 'no genre'}")
    if track.taste_note:
        print(f"  current note ({track.taste_source}): {track.taste_note!r}")

    neighbors = store.nearest(track.embedding, k=5, exclude_path=path)
    if neighbors:
        print("  sounds like:")
        for n in neighbors:
            print(f"    - {n.title or Path(n.path).name} ({n.artist or '?'}, d={n.distance:.2f})")

    note = _prompt("\nyour note (enter to skip this track)")
    if not note:
        print("  skipped")
        return False
    rating = _prompt_rating()
    role = _prompt_role()

    store.set_taste(path, note, embed.embed_note(note), rating=rating, role=role)
    print("  ✓ saved")
    return True


def walk_queue(n: int) -> int:
    """Walk the active-learning queue, tagging until I quit or it's exhausted."""
    from dj.taste import propagate

    queue = propagate.labeling_queue(n=n)
    if not queue:
        print("nothing to label — ingest a library first (everything's tagged?)")
        return 0
    print(f"{len(queue)} tracks to label (most useful first). Ctrl-C to stop.\n")
    saved = 0
    for path in queue:
        try:
            saved += tag_one(path)
        except KeyboardInterrupt:
            print("\nstopped.")
            break
    print(f"\ndone — {saved} tagged this session.")
    return saved


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not settings.db_enabled:
        print("DATABASE_URL not set — tagging needs the vibe DB. Ingest first.")
        return 1

    if argv and argv[0] == "--queue":
        from dj.taste import propagate

        n = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 20
        for i, path in enumerate(propagate.labeling_queue(n=n), 1):
            print(f"{i:3}. {path}")
        return 0

    if argv and argv[0] == "--propagate":
        from dj.taste import propagate

        count = propagate.propagate_library()
        print(f"propagated provisional taste to {count} untagged tracks.")
        return 0

    if argv:
        tag_one(argv[0])
        return 0

    walk_queue(n=20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
