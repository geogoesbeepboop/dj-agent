"""Ingest a pasted link straight into the library: download → analyze → vibe DB.

The one-command front door for link ingestion (ADR 0009). It wraps LinkProvider
in the same Curator pipeline local folders go through, so a pasted playlist
ends up segmented, analyzed, and embedded exactly like files I already own:

    uv run python -m dj.ingest "https://open.spotify.com/playlist/<your-playlist-id>"
    uv run python -m dj.ingest https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b
    uv run python -m dj.ingest "https://www.youtube.com/playlist?list=PL..." --favorites

--favorites marks every fetched track as a favorite (a taste signal, ADR 0003).

Spotify needs a one-time user login before playlists resolve (the app-only flow
stopped returning playlist contents after Nov 2024). Register the redirect URI
from `.env` in your app dashboard, then:

    uv run python -m dj.ingest --login

Note: Spotify-owned editorial/algorithmic playlists (ids starting 37i9dQZF1DX…,
Discover Weekly, …) may still be restricted even with user auth — paste
playlists made by users (yours included), or the YouTube equivalent.
"""

from __future__ import annotations

import sys

from dj.curator import ingest
from dj.ingest.provider import LinkProvider


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--login" in argv:
        # One-time Spotify user OAuth (Authorization Code). Reading playlists needs
        # a user token now; this caches a refreshable one for all later ingests.
        from dj.ingest.resolve import spotify_login

        try:
            spotify_login()
        except (ValueError, RuntimeError) as exc:
            print(exc)
            return 1
        return 0
    favorite = "--favorites" in argv
    urls = [a for a in argv if a not in ("--favorites", "--login")]
    if len(urls) != 1:
        print("usage: python -m dj.ingest <url> [--favorites]  |  --login")
        return 1
    try:
        ingest(LinkProvider(urls[0], is_favorite=favorite))
    except (ValueError, RuntimeError) as exc:
        # unsupported link / missing Spotify creds / Spotify API errors — the
        # message already names the fix; a traceback would bury it.
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
