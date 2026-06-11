"""Bulk taste judging — paste a playlist link, judge every track, download nothing.

A playlist I already know is dozens of taste judgments waiting to happen, and
none of them need the audio: each entry resolves to catalog metadata only (the
front half of link ingestion, ADR 0009), then I say how the track makes me feel.
Tracks already in my library are tagged directly — the note lands in
`tracks.taste_vec` exactly as if typed into `dj.taste.tag`. Tracks I don't own
yet are parked as pending reviews (source='bulk') that auto-apply when the file
later arrives via `dj.ingest` — the Spotify path writes the ISRC into the file,
so the Curator's match is confident (ADR 0008). Extends the taste loop in
docs/taste.md.

    uv run python -m dj.taste.judge <playlist-or-track-url>

Shares the rating/role vocabulary with `dj.taste.tag`; the per-track prompts are
module-level seams (`_prompt` / `_prompt_rating` / `_prompt_role`) so tests can
script a whole session. Store/pending/embed import lazily inside `judge()` —
importing this module needs no DB, model, or network.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dj.config import settings
from dj.ingest.links import classify
from dj.ingest.resolve import resolve
from dj.taste.tag import ROLES


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{label}{suffix}: ").strip()
    except EOFError:
        return default
    return ans or default


def _prompt_note() -> str:
    """The per-track note prompt. EOF (Ctrl-D, exhausted pipe) quits the session
    instead of silently skipping every remaining track."""
    try:
        return input("note (Enter=skip, q=quit): ").strip()
    except EOFError:
        return "q"


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


def judge(url: str, *, sp=None, runner=None) -> int:
    """Walk a link's tracklist judging each track. Returns the count captured.

    Per track: a note (Enter skips, 'q' quits), then rating + role. A track the
    library already has is tagged in place; anything else parks a pending review
    keyed by ISRC/name+duration so ingest applies it later with no extra input.
    `sp`/`runner` are the resolution seams (spotipy client, yt-dlp runner).
    """
    if not settings.db_enabled:
        print("DATABASE_URL not set — judging needs the vibe DB (parked reviews live there).")
        return -1
    from dj.taste import embed, pending  # lazy: keep module import light
    from dj.vibe import store

    link = classify(url)
    reqs = resolve(link, sp=sp, runner=runner)
    print(f"[judge] {len(reqs)} tracks from {link.service} {link.kind}")

    tagged = parked = skipped = 0
    for i, req in enumerate(reqs, 1):
        print(f"\n[{i}/{len(reqs)}] {req.display}")
        note = _prompt_note()
        if note.lower() == "q":
            break
        if not note:
            skipped += 1
            continue
        rating = _prompt_rating()
        role = _prompt_role()

        path = store.find_track_by_meta(req.artist, req.title, isrc=req.isrc)
        if path:
            prior = store.get_track(path)
            store.set_taste(path, note, embed.embed_note(note), rating=rating, role=role)
            if prior and prior.taste_source == "manual":
                # Re-judging overwrites the earlier note/rating/role (last wins) —
                # surface the prior judgment so the overwrite is never silent.
                bits = [b for b in (
                    f"rating {prior.rating}" if prior.rating else "",
                    prior.role or "") if b]
                was = f" (was {', '.join(bits)})" if bits else " (re-judged)"
                print(f"  ✓ updated in library{was}: {Path(path).name}")
            else:
                print(f"  ✓ tagged in library: {Path(path).name}")
            tagged += 1
        else:
            rid = pending.add(
                req.artist, req.title, note,
                isrc=req.isrc, spotify_id=req.spotify_id, album=req.album,
                duration_s=req.duration_s, rating=rating, role=role, source="bulk",
            )
            print(f"  ↗ parked review #{rid} (auto-applies when the file is ingested)")
            parked += 1

    print(f"\n[judge] done — tagged {tagged} directly, parked {parked}, skipped {skipped}.")
    return tagged + parked


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m dj.taste.judge <playlist-or-track-url>")
        print("judging captures taste only (no downloads) — fetch the files with: "
              "python -m dj.ingest <url>")
        return 1
    try:
        return 0 if judge(argv[0]) >= 0 else 1
    except (ValueError, RuntimeError) as exc:  # bad link / missing Spotify creds / API error
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
