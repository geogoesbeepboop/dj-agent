"""The SourceProvider contract the Curator ingests from."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol, runtime_checkable


@dataclass
class TrackRef:
    """A pointer to one ingestible audio file, plus what the source knows about it.

    `path` must be a local, decodable file (remote providers download first and
    yield the local path). `extra_tags` are source-supplied descriptors (e.g.
    Jamendo mood tags) merged with the file's own ID3 tags at ingest.
    """

    path: str
    source: str = "local"
    is_favorite: bool = False
    extra_tags: list[str] = field(default_factory=list)


@runtime_checkable
class SourceProvider(Protocol):
    """Anything the Curator can ingest: yields TrackRefs."""

    source_id: str

    def iter_tracks(self) -> Iterable[TrackRef]:
        ...
