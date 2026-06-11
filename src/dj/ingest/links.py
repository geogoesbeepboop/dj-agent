"""Classify a pasted link into (service, kind, id) — pure URL parsing, no I/O.

The classifier is the front door of link ingestion (ADR 0009): everything
downstream (resolve → download → curate) branches on the Link it returns, so
the supported shapes are enumerated here and anything else fails fast with a
message that names the URL and what *is* supported. Spotify gives four entity
kinds (track/album/playlist/artist, plus `spotify:` URIs and /intl-xx/ locale
prefixes); YouTube gives videos (watch/youtu.be/shorts) and playlists.

One deliberate rule: a watch URL that *also* carries a `list=` param classifies
as 'video', not 'playlist' — the user pasted a specific video, and the playlist
context is just how YouTube decorated the share link.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

_SPOTIFY_KINDS = {"track", "album", "playlist", "artist"}
_YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com"}

_SUPPORTED = (
    "Spotify track/album/playlist/artist links (open.spotify.com or spotify: URIs) "
    "and YouTube video/shorts/playlist links (youtube.com, youtu.be, music.youtube.com)"
)


@dataclass(frozen=True)
class Link:
    service: str  # 'spotify' | 'youtube'
    kind: str     # 'track' | 'album' | 'playlist' | 'artist' | 'video'
    id: str       # the service's id for the entity
    url: str      # the original input url


def classify(url: str) -> Link:
    """Map a pasted link to a typed Link. Raises ValueError on anything else."""
    raw = url.strip()
    if raw.lower().startswith("spotify:"):
        return _from_spotify_uri(raw)

    # Pasted links often arrive without a scheme ("open.spotify.com/track/x").
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "open.spotify.com":
        return _from_spotify_url(parsed.path, raw)
    if host in _YOUTUBE_HOSTS:
        return _from_youtube_url(parsed.path, parsed.query, raw)
    if host == "youtu.be":
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            return Link("youtube", "video", parts[0], raw)
    raise ValueError(f"Unsupported link: {raw!r}. Supported: {_SUPPORTED}.")


def _from_spotify_uri(raw: str) -> Link:
    parts = raw.split(":")
    if len(parts) == 3 and parts[1].lower() in _SPOTIFY_KINDS and parts[2]:
        return Link("spotify", parts[1].lower(), parts[2], raw)  # ids are case-sensitive; kinds aren't
    raise ValueError(f"Unsupported Spotify URI: {raw!r}. Supported: {_SUPPORTED}.")


def _from_spotify_url(path: str, raw: str) -> Link:
    parts = [p for p in path.split("/") if p]
    if parts and parts[0].lower().startswith("intl-"):  # locale prefix, e.g. /intl-pt/
        parts = parts[1:]
    if len(parts) >= 2 and parts[0] in _SPOTIFY_KINDS and parts[1]:
        return Link("spotify", parts[0], parts[1], raw)
    raise ValueError(f"Unsupported Spotify link: {raw!r}. Supported: {_SUPPORTED}.")


def _from_youtube_url(path: str, query: str, raw: str) -> Link:
    params = parse_qs(query)
    parts = [p for p in path.split("/") if p]
    if parts == ["watch"] and params.get("v"):
        # A &list= param may also be present — still a video (see module docstring).
        return Link("youtube", "video", params["v"][0], raw)
    if len(parts) >= 2 and parts[0] == "shorts" and parts[1]:
        return Link("youtube", "video", parts[1], raw)
    if parts == ["playlist"] and params.get("list"):
        return Link("youtube", "playlist", params["list"][0], raw)
    raise ValueError(f"Unsupported YouTube link: {raw!r}. Supported: {_SUPPORTED}.")
