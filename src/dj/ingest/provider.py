"""LinkProvider — the SourceProvider that turns a pasted link into local files (ADR 0009).

This is where link ingestion meets the Curator: classify → resolve → fetch,
yielding a TrackRef per downloaded file so a pasted playlist flows through the
exact same segment/analyze/embed pipeline as a local folder. One bad track
(geo-blocked video, no search match) prints a FAILED line and is skipped —
mirroring the Curator's own keep-going policy — so a 40-track playlist never
dies on entry 3.

Downloads land under `settings.library_dir/<source>` unless `out_dir` says
otherwise; `sp=` and `runner=` pass straight through to resolve/fetch as the
test seams.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from dj.config import settings
from dj.ingest.download import fetch
from dj.ingest.links import classify
from dj.ingest.resolve import resolve
from dj.sources.base import TrackRef


class LinkProvider:
    """Resolve one pasted Spotify/YouTube link and yield its tracks as local files."""

    source_id = "link"

    def __init__(self, url: str, is_favorite: bool = False, *,
                 out_dir: str | None = None, sp=None, runner=None) -> None:
        self.url = url
        self.is_favorite = is_favorite
        self.out_dir = out_dir
        self.sp = sp
        self.runner = runner

    def iter_tracks(self) -> Iterable[TrackRef]:
        link = classify(self.url)
        requests = resolve(link, sp=self.sp, runner=self.runner)
        print(f"[ingest] {link.service} {link.kind}: {len(requests)} tracks to fetch")
        for req in requests:
            out = self.out_dir or Path(settings.library_dir) / req.source
            try:
                path = fetch(req, out, runner=self.runner)
            except Exception as exc:  # keep going; one dead video shouldn't kill the link
                print(f"[ingest] FAILED {req.display}: {exc}")
                continue
            yield TrackRef(path=str(path), source=req.source, is_favorite=self.is_favorite)
