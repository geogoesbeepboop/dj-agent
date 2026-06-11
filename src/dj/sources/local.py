"""LocalFolderProvider — ingest audio files from a folder tree."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from dj.sources.base import TrackRef

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}


class LocalFolderProvider:
    """Walk `folder` recursively, yielding a TrackRef per audio file.

    Tracks whose path contains a `favorites/` segment are marked is_favorite,
    so you can flag favorites just by foldering them — no sidecar needed. Pass
    is_favorite=True to mark an entire folder as favorites.
    """

    source_id = "local"

    def __init__(self, folder: str, is_favorite: bool = False) -> None:
        self.folder = Path(folder)
        self.force_favorite = is_favorite

    def iter_tracks(self) -> Iterable[TrackRef]:
        for p in sorted(self.folder.rglob("*")):
            if p.suffix.lower() not in AUDIO_EXTS:
                continue
            fav = self.force_favorite or "favorites" in {part.lower() for part in p.parts}
            yield TrackRef(path=str(p), source=self.source_id, is_favorite=fav)
