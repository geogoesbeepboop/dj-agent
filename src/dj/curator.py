"""The Curator: ingest tracks from any SourceProvider → analyze → embed → vibe DB.

This is the background, all-day agent of the DJ system (the "gold mine"
builder). It's a deterministic pipeline, not an LLM agent. Per track it produces
two representations:
  - structured mixing features (BPM, key→Camelot, energy arc) via analyze()
  - a CLAP semantic vibe vector via clap.embed_audio()
plus file metadata, then upserts the lot. The LLM agents (Architect, Selector)
come in Phase 2 and *query* what the Curator builds.

    uv run python -m dj.curator ~/Music/some-folder [--favorites ~/Music/faves]
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_core.tracing import trace

from dj import metadata
from dj.audio.analyze import analyze
from dj.sources import LocalFolderProvider, SourceProvider, TrackRef
from dj.vibe import clap, store


def ingest_track(ref: TrackRef) -> None:
    """Analyze + embed + tag one track and upsert it into the vibe DB."""
    with trace("curate-track", path=ref.path, source=ref.source):
        features = analyze(ref.path)
        vector = clap.embed_audio(ref.path)
        tags = metadata.read_tags(ref.path).merged_with(ref.extra_tags)
        store.upsert_track(
            features, tags, vector, source=ref.source, is_favorite=ref.is_favorite
        )


def ingest(provider: SourceProvider) -> int:
    """Ingest every track a provider yields. Returns the count successfully done."""
    store.ensure_schema()
    refs = list(provider.iter_tracks())
    print(f"[curator] {provider.source_id}: {len(refs)} tracks to ingest")
    done = 0
    for i, ref in enumerate(refs, 1):
        name = Path(ref.path).name
        try:
            ingest_track(ref)
            done += 1
            print(f"[curator] ingested ({i}/{len(refs)}): {name}")
        except Exception as exc:  # keep going; one bad file shouldn't stop the run
            print(f"[curator] FAILED {name}: {exc}")
    print(f"[curator] done — {done}/{len(refs)} ingested. total in DB: {store.count()}")
    return done


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m dj.curator <audio-folder> [--favorites <folder>]")
        return 1

    if argv[0] == "--favorites":
        if len(argv) < 2:
            print("usage: python -m dj.curator --favorites <folder>")
            return 1
        ingest(LocalFolderProvider(argv[1], is_favorite=True))
    else:
        ingest(LocalFolderProvider(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
