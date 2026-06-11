"""Link ingestion: paste a Spotify/YouTube link, get library tracks (ADR 0009).

The pipeline is classify → resolve → download → curate. `links` turns a pasted
URL into a typed Link (pure parsing); `resolve` expands it into TrackRequests —
metadata only, so the user can see and approve a tracklist before any audio is
fetched. Spotify is metadata-only (client credentials, no user auth); YouTube
entries carry the direct download target. `download` then materializes each
request as FLAC in the library (YouTube directly, Spotify via a scored YouTube
search) and `provider.LinkProvider` wraps the whole chain as a SourceProvider
the Curator ingests from. Heavy deps (yt-dlp, spotipy, mutagen) are lazy and
behind injectable seams, so importing the package and the fast tests need no
network.

Only `LinkProvider` is re-exported — the one name outsiders need (the Curator's
URL dispatch). Nothing else: `resolve` the function would shadow `resolve` the
submodule on the package object, so the rest is imported from its submodule.
"""

from dj.ingest.provider import LinkProvider

__all__ = ["LinkProvider"]
