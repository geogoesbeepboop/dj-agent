"""review CLI — park a taste review for a track I don't own yet (ADR 0008).

The companion to `dj.taste.tag`: that tags tracks already in the library; this
captures how I feel about a track *before* the file exists, so the note is ready
to apply the moment I ingest it. The seamless capture flows ("review what's
playing", chat) run through the agent (`agents/tools.save_review` + the Spotify
MCP); this CLI is the no-Spotify manual entry + the parked-review inbox.

    uv run python -m dj.taste.review                 # type a review by hand
    uv run python -m dj.taste.review --pending       # list parked reviews (want-list)
    uv run python -m dj.taste.review --apply <id> <track_path>   # resolve a match by hand

Shares the rating/role vocabulary and prompts with `dj.taste.tag`.
"""

from __future__ import annotations

import sys

from dj.config import settings
from dj.taste.tag import _prompt, _prompt_rating, _prompt_role


def _capture() -> int:
    from dj.taste import pending

    print("Park a review for a track you don't own yet (Ctrl-C to cancel).\n")
    artist = _prompt("artist")
    title = _prompt("title")
    if not artist or not title:
        print("  ! need both artist and title; nothing saved")
        return 1
    note = _prompt("your note")
    if not note:
        print("  ! a review needs a note; nothing saved")
        return 1
    rating = _prompt_rating()
    role = _prompt_role()

    rid = pending.add(artist, title, note, rating=rating, role=role, source="manual")
    print(f"  ✓ parked review #{rid} — applies automatically when you ingest "
          f"{artist} – {title}")
    return 0


def _list_pending() -> int:
    from dj.taste import pending

    rows = pending.list_pending()
    if not rows:
        print("no parked reviews.")
        return 0
    print(f"{len(rows)} parked review(s) — auto-apply on ingest; use #id with --apply:\n")
    for r in rows:
        key = "isrc" if r.isrc else ("spotify" if r.spotify_id else "name")
        meta = " ".join(filter(None, [
            f"★{r.rating}" if r.rating else "", r.role or "", f"[{key}]",
        ]))
        print(f"#{r.id:<4} {r.artist} – {r.title}  {meta}")
        print(f"      “{r.note}”")
    return 0


def _apply(review_id_raw: str, path: str) -> int:
    from dj.taste import pending
    from dj.vibe import store

    if not review_id_raw.isdigit():
        print(f"  ! review id must be a number: {review_id_raw}")
        return 1
    if store.get_track(path) is None:
        print(f"  ! not in the DB (ingest it first): {path}")
        return 1
    if not pending.apply_manual(int(review_id_raw), path):
        print(f"  ! no parked review #{review_id_raw}")
        return 1
    print(f"  ✓ applied review #{review_id_raw} to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not settings.db_enabled:
        print("DATABASE_URL not set — reviews live in the vibe DB.")
        return 1

    if argv and argv[0] == "--pending":
        return _list_pending()
    if argv and argv[0] == "--apply":
        if len(argv) < 3:
            print("usage: python -m dj.taste.review --apply <review_id> <track_path>")
            return 1
        return _apply(argv[1], argv[2])
    return _capture()


if __name__ == "__main__":
    raise SystemExit(main())
