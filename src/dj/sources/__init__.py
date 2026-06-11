"""Track sources: where the Curator gets audio to ingest.

The SourceProvider seam decouples the Curator from *where* audio comes from.
Phase 1 ships LocalFolderProvider; CC-licensed remote providers (Jamendo, FMA)
can be added later with no Curator change — so the agent need not "run out" of
local tracks. Streaming services are deliberately excluded: they can't hand over
downloadable audio, which is what beatmatching requires.
"""

from dj.sources.base import SourceProvider, TrackRef
from dj.sources.local import LocalFolderProvider

__all__ = ["SourceProvider", "TrackRef", "LocalFolderProvider"]
