"""The Curator: ingest any audio folder → analyze → embed → vibe DB.

This is the background, all-day agent of the DJ system (the "gold mine"
builder). It's a deterministic pipeline, not an LLM agent — analysis +
embedding + storage. The LLM agents (Architect, Selector) come in Phase 2 and
*query* what the Curator builds.

    uv run python -m dj.curator ~/Music/some-folder
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_core.tracing import trace

from dj.audio.analyze import analyze
from dj.vibe import store
from dj.vibe.embed import embed

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}


def ingest_file(path: str, is_favorite: bool = False) -> None:
    """Analyze one track and upsert it into the vibe DB."""
    with trace("curate-track", path=path):
        features = analyze(path)
        vector = embed(features)
        store.upsert_track(features, vector, is_favorite=is_favorite)


def ingest_folder(folder: str) -> int:
    """Recursively ingest every audio file under `folder`. Returns count."""
    store.ensure_schema()
    files = [p for p in Path(folder).rglob("*") if p.suffix.lower() in AUDIO_EXTS]
    print(f"[curator] found {len(files)} audio files under {folder}")
    done = 0
    for p in files:
        try:
            ingest_file(str(p))
            done += 1
            print(f"[curator] ingested ({done}/{len(files)}): {p.name}")
        except Exception as exc:  # keep going; one bad file shouldn't stop the run
            print(f"[curator] FAILED {p.name}: {exc}")
    print(f"[curator] done — {done}/{len(files)} ingested. total in DB: {store.count()}")
    return done


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print("usage: python -m dj.curator <audio-folder>")
        return 1
    ingest_folder(argv[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
