"""Resolve a classified Link into TrackRequests — metadata only, no audio (ADR 0009).

Resolution is deliberately split from downloading: expanding a playlist link to
a named tracklist is cheap, so consumers that never want audio can reuse it —
the bulk-judge CLI walks a playlist from this step alone, and ingest prints the
tracklist it's about to fetch. Spotify resolution uses the client-credentials flow (no
user auth — it can only read public catalog metadata, which is all we need:
names, durations, and the ISRC that later matches parked reviews, ADR 0008).
YouTube resolution shells out to yt-dlp in JSON mode; each request carries the
canonical watch URL as its download target for the back half of the pipeline.

Both externals sit behind injectable seams (`runner=` for yt-dlp, `sp=` for
spotipy), and both libraries import lazily — module import needs no network.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

from dj.config import settings
from dj.ingest.links import Link


@dataclass
class TrackRequest:
    """One track the user asked for, in catalog terms — not yet a local file."""

    artist: str = ""
    title: str = ""
    album: str = ""
    duration_s: float | None = None
    isrc: str | None = None
    spotify_id: str | None = None
    video_url: str | None = None  # set for youtube entries: the direct download target
    source: str = "youtube"       # 'spotify' | 'youtube'

    @property
    def display(self) -> str:
        """Human line for approval lists: "Artist — Title (3:45)", missing pieces omitted."""
        name = " — ".join(p for p in (self.artist, self.title) if p)
        name = name or self.video_url or "(unknown track)"
        if self.duration_s:
            minutes, seconds = divmod(int(round(self.duration_s)), 60)
            return f"{name} ({minutes}:{seconds:02d})"
        return name


def resolve(link: Link, *, sp=None, runner=None) -> list[TrackRequest]:
    """Expand a Link into the tracks it names. Metadata only — nothing is downloaded."""
    if link.service == "youtube":
        if link.kind == "video":
            return [_youtube_video(link, runner=runner)]
        return _youtube_playlist(link, runner=runner)

    client = sp if sp is not None else _spotify_client()
    try:
        if link.kind == "track":
            return [_from_spotify_track(client.track(link.id))]
        if link.kind == "playlist":
            return _spotify_playlist(client, link.id)
        if link.kind == "album":
            return _spotify_album(client, link.id)
        return [_from_spotify_track(t) for t in client.artist_top_tracks(link.id)["tracks"]]
    except Exception as exc:
        wrapped = _wrap_spotify_error(link, exc)
        if wrapped is exc:
            raise
        raise wrapped from exc


def _wrap_spotify_error(link: Link, exc: Exception) -> Exception:
    """Turn a spotipy HTTP error into a message that names the real problem.

    The big one: since Spotify's 2024-11 API change, apps created after that
    date get a bare 404 for Spotify-owned editorial/algorithmic playlists
    (Today's Top Hits, Discover Weekly, …) — which reads like a typo'd id.
    Matched by `http_status` (duck-typed) so test fakes need no spotipy import.
    """
    status = getattr(exc, "http_status", None)
    if status == 404:
        return RuntimeError(
            f"Spotify returned 404 for {link.kind} {link.id}. If the link works in the "
            "app, this is almost certainly a Spotify-owned editorial/algorithmic "
            "playlist — those are blocked for API apps created after Nov 2024. "
            "Use a playlist created by a user (e.g. save the tracks to your own), "
            "or paste a YouTube link instead."
        )
    if status is not None:
        return RuntimeError(f"Spotify API error {status} for {link.kind} {link.id}: {exc}")
    return exc  # not a Spotify HTTP error — propagate untouched


# --- yt-dlp ------------------------------------------------------------------


def run_ytdlp(args: list[str], runner=None) -> str:
    """Run yt-dlp with `args` (program name excluded) and return its stdout.

    `runner` is the test seam: Callable[[list[str]], str]. The default invokes
    yt-dlp as a module of *this* interpreter — it's a project dep, so no PATH
    lookup — and surfaces the tail of stderr on failure (yt-dlp's last lines
    name the actual problem: geo-block, age gate, removed video).
    """
    if runner is not None:
        return runner(args)
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "yt_dlp", *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        out = proc.stdout.strip()
        # Partial failures (one dead entry in a playlist/search) exit nonzero but
        # still emit the JSON for everything that worked — use it, don't discard it.
        # But a bare `null` (or empty) stdout is NOT a partial success: it's a total
        # failure — a removed/private/region-locked video or a playlist that doesn't
        # exist — which yt-dlp prints as `null`. Surface that clearly instead of
        # returning `null` for json.loads to turn into a None that crashes callers
        # with an opaque AttributeError.
        if out and out != "null":
            print("yt-dlp warning: " + " | ".join(tail))
            return proc.stdout
        raise RuntimeError(
            "yt-dlp could not fetch this link — it may be removed, private, "
            "region-locked, or not a real playlist: " + " | ".join(tail)
        )
    return proc.stdout


def _youtube_video(link: Link, *, runner=None) -> TrackRequest:
    data = json.loads(run_ytdlp(["-J", "--no-playlist", link.url], runner=runner))
    if not isinstance(data, dict):
        raise RuntimeError(f"yt-dlp returned no video data for {link.url}")
    return _from_youtube_entry(data, fallback_id=link.id)


def _youtube_playlist(link: Link, *, runner=None) -> list[TrackRequest]:
    data = json.loads(run_ytdlp(["-J", "--flat-playlist", link.url], runner=runner))
    if not isinstance(data, dict):
        raise RuntimeError(f"yt-dlp returned no playlist data for {link.url}")
    out: list[TrackRequest] = []
    for entry in data.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        if entry.get("title") in ("[Deleted video]", "[Private video]"):
            continue
        out.append(_from_youtube_entry(entry))
    return out


def _from_youtube_entry(data: dict, fallback_id: str = "") -> TrackRequest:
    uploader = data.get("uploader") or data.get("channel") or ""
    artist, title = _split_title(data.get("title") or "", uploader)
    duration = data.get("duration")  # flat playlist entries may carry it; use if so
    video_id = data.get("id") or fallback_id
    return TrackRequest(
        artist=artist,
        title=title,
        duration_s=float(duration) if duration else None,
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        source="youtube",
    )


# Pure-decoration chunks YouTube titles carry that are NOT title content.
# "(feat. X)" and remix/edit descriptors are kept — they name a real variant.
_SEPARATORS = (" - ", " – ", " — ", " | ")
_NOISE_WORDS = (
    r"(?:official(?:\s+(?:music\s+)?(?:video|audio))?|music\s+video|lyric(?:s)?(?:\s+video)?"
    r"|audio|visuali[sz]er|hq|hd|4k|m/?v)"
)


def _split_title(title: str, uploader: str) -> tuple[str, str]:
    """Heuristic (artist, title) from a YouTube video name.

    The *earliest-occurring* separator splits with artist on the left (position
    beats `_SEPARATORS` order, so "A | B - C" splits at the pipe). With no
    separator, the uploader stands in as the artist — minus the " - Topic"
    suffix YouTube's auto-generated channels carry. Either way the title is
    stripped of pure decoration: "(Official Video)", "[Lyrics]", "[4K]",
    "【...】" go; "(feat. X)" and remix names stay.
    """
    found = [(title.index(sep), sep) for sep in _SEPARATORS if sep in title]
    if found:
        _, sep = min(found)
        artist, rest = title.split(sep, 1)
        return artist.strip(), _clean_title(rest)
    artist = uploader.strip().removesuffix(" - Topic").strip()
    return artist, _clean_title(title)


def _clean_title(title: str) -> str:
    title = re.sub(r"\s*【[^】]*】", "", title)
    title = re.sub(rf"\s*[(\[]\s*{_NOISE_WORDS}\s*[)\]]", "", title, flags=re.IGNORECASE)
    return title.strip()


# --- Spotify -----------------------------------------------------------------


# Reading playlists (public or private) needs a *user* token. These scopes also
# cover tracks/albums/artists, so one login serves every Spotify link type.
_SPOTIFY_SCOPE = "playlist-read-private playlist-read-collaborative"


def _spotify_auth_manager(*, open_browser: bool):
    """Build the Authorization-Code (user OAuth) auth manager. See config + ADR.

    App-only client-credentials stopped returning playlist contents after
    Spotify's Nov-2024 change, so the resolver authenticates as the user.
    """
    if not (settings.spotify_client_id and settings.spotify_client_secret):
        raise RuntimeError(
            "Spotify link resolution needs SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET "
            "in .env — create a free app at developer.spotify.com/dashboard."
        )
    from spotipy.cache_handler import CacheFileHandler
    from spotipy.oauth2 import SpotifyOAuth

    return SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.spotify_redirect_uri,
        scope=_SPOTIFY_SCOPE,
        cache_handler=CacheFileHandler(cache_path=settings.spotify_cache_path),
        open_browser=open_browser,
    )


def _spotify_client():
    """Lazily build a user-authorized spotipy client from the cached token.

    Fails fast with the fix when there's no cached token, rather than blocking on
    an interactive browser prompt (which would hang a background/piped ingest).
    """
    import spotipy

    auth = _spotify_auth_manager(open_browser=False)
    # validate_token auto-refreshes an expired-but-cached token; None = no login yet.
    if not auth.validate_token(auth.cache_handler.get_cached_token()):
        raise RuntimeError(
            "Spotify needs a one-time login — the API no longer reads playlists "
            "with app-only auth. Register the redirect URI "
            f"({settings.spotify_redirect_uri}) in your app at "
            "developer.spotify.com/dashboard, then run:  "
            "uv run python -m dj.ingest --login"
        )
    return spotipy.Spotify(auth_manager=auth)


def spotify_login() -> None:
    """One-time interactive OAuth: open a browser, cache a refreshable token.

    Run once (`python -m dj.ingest --login`); afterwards every ingest — including
    background/piped runs — reads the cached token silently and auto-refreshes it.
    """
    import spotipy

    auth = _spotify_auth_manager(open_browser=True)
    auth.get_access_token(as_dict=False)  # runs the browser flow, caches the token
    who = "your account"
    try:
        me = spotipy.Spotify(auth_manager=auth).current_user()
        who = me.get("display_name") or me.get("id") or who
    except Exception:
        pass  # token is already cached; a verify-only hiccup isn't a login failure
    print(f"Spotify login OK — authorized as {who}. Token cached at "
          f"{settings.spotify_cache_path}.")


def _from_spotify_track(t: dict, album: str | None = None) -> TrackRequest:
    return TrackRequest(
        artist=", ".join(a["name"] for a in t.get("artists") or []),
        title=t.get("name") or "",
        album=album if album is not None else (t.get("album") or {}).get("name", ""),
        duration_s=t["duration_ms"] / 1000 if t.get("duration_ms") else None,
        isrc=(t.get("external_ids") or {}).get("isrc"),
        spotify_id=t.get("id"),
        source="spotify",
    )


def _spotify_playlist(sp, playlist_id: str) -> list[TrackRequest]:
    out: list[TrackRequest] = []
    skipped = 0
    page = sp.playlist_items(playlist_id)
    while True:
        for item in page.get("items") or []:
            # Spotify's playlist-items response moved the entity from "track" to
            # "item" (a track or episode); accept either so we survive the change
            # and keep older/faked "track"-shaped payloads working.
            track = item.get("item") or item.get("track")
            # Deleted tracks come back as None; local files have no catalog id.
            if not track or item.get("is_local") or track.get("is_local"):
                skipped += 1
                continue
            out.append(_from_spotify_track(track))
        if not page.get("next"):
            break
        page = sp.next(page)
    if skipped:
        print(f"skipped {skipped} playlist entr{'y' if skipped == 1 else 'ies'} "
              "(deleted or local-file tracks)")
    return out


def _spotify_album(sp, album_id: str) -> list[TrackRequest]:
    # Album-track items are simplified objects (no external_ids/album), so collect
    # ids and re-fetch full objects in batches of 50 — the ISRC is worth the trip.
    album_name = sp.album(album_id)["name"]
    ids: list[str] = []
    page = sp.album_tracks(album_id)
    while True:
        ids.extend(t["id"] for t in page.get("items") or [] if t.get("id"))
        if not page.get("next"):
            break
        page = sp.next(page)
    out: list[TrackRequest] = []
    for i in range(0, len(ids), 50):
        full = sp.tracks(ids[i : i + 50])["tracks"]
        out.extend(_from_spotify_track(t, album=album_name) for t in full if t)
    return out
