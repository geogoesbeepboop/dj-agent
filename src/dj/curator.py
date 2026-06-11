"""The Curator: ingest tracks from any SourceProvider → analyze → embed → vibe DB.

This is the background, all-day agent of the DJ system (the "gold mine"
builder). It's a deterministic pipeline, not an LLM agent. Per track it produces
three representations (docs/architecture.md):
  - the **beat grid + functional sections** (segment.py: downbeats, section bounds)
  - **structured mixing features** (analyze.py: BPM, key→Camelot, LUFS)
  - **CLAP semantic vectors** — one for the whole track (discovery) AND one per
    section (transition matching) via clap.embed_track_and_sections
plus file metadata, then upserts the track row + its section rows in one txn. The
LLM agents (Architect, Selector) come in Phase 3 and *query* what this builds.

    uv run python -m dj.curator ~/Music/some-folder [--favorites ~/Music/faves]
"""

from __future__ import annotations

import sys
from pathlib import Path

from dj.tracing import trace

from dj import metadata
from dj.audio import segment as segment_mod
from dj.audio.analyze import analyze, measure_sections
from dj.sources import LocalFolderProvider, SourceProvider, TrackRef
from dj.vibe import clap, store


def ingest_track(ref: TrackRef) -> None:
    """Segment + analyze + embed (track & sections) one track and upsert it."""
    with trace("curate-track", path=ref.path, source=ref.source) as span:
        structure = segment_mod.segment(ref.path)
        features = analyze(ref.path, bpm=structure.bpm or None)

        bounds = [(s.start_s, s.end_s) for s in structure.sections]
        track_vec, section_vecs = clap.embed_track_and_sections(ref.path, bounds)
        section_lufs = measure_sections(ref.path, structure.sections)

        sections = [
            store.SectionInput(
                idx=s.idx, label=s.label, start_s=s.start_s, end_s=s.end_s,
                embedding=vec, start_beat=s.start_beat, bars=s.bars,
                energy_lufs=lufs, camelot=s.camelot,
                is_mixin=s.is_mixin, is_mixout=s.is_mixout, loopable=s.loopable,
            )
            for s, vec, lufs in zip(structure.sections, section_vecs, section_lufs)
        ]
        span["metadata"]["sections"] = len(sections)
        span["metadata"]["detector"] = structure.source

        tags = metadata.read_tags(ref.path).merged_with(ref.extra_tags)
        store.upsert_track(
            features, tags, track_vec, sections=sections,
            source=ref.source, is_favorite=ref.is_favorite,
        )
        _drain_pending(ref.path, tags, features, span)


def _drain_pending(path: str, tags, features, span) -> None:
    """Apply a parked taste review (ADR 0008) if this track confidently matches one.

    Confident match (ISRC, or unique name + duration) on a track I haven't tagged
    by hand → the note embeds into taste_vec as a 'manual' label, no extra input.
    Ambiguous matches are left parked and surfaced for a one-line manual resolve.
    """
    from dj.taste import pending  # lazy: only ingest needs the pending store

    m = pending.match(
        isrc=tags.isrc or None, artist=tags.artist or "",
        title=tags.title or "", duration_s=features.duration_s,
    )
    if m is None:
        return
    if not m.confident:
        print(f"[curator] parked review #{m.review.id} for "
              f"{tags.artist or '?'} – {tags.title or '?'} ({m.reason}) — "
              f"resolve with: python -m dj.taste.review --apply {m.review.id} {path}")
        return
    if store.get_cards([path]).get(path, {}).get("taste_source") == "manual":
        return  # never clobber a label I wrote by hand
    pending.apply_review(m.review, path)
    span["metadata"]["applied_review"] = m.review.id
    print(f"[curator] applied parked review #{m.review.id} ({m.reason}) to {Path(path).name}")


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
        print("usage: python -m dj.curator <audio-folder-or-url> [--favorites <folder-or-url>]")
        return 1

    def provider(target: str, is_favorite: bool = False) -> SourceProvider:
        """Anything classify() recognizes — incl. scheme-less pastes like
        "open.spotify.com/…" — ingests via LinkProvider (ADR 0009); the rest is a folder."""
        from dj.ingest.links import classify  # lazy: only link ingestion needs the chain

        try:
            classify(target)
        except ValueError:
            return LocalFolderProvider(target, is_favorite=is_favorite)
        from dj.ingest import LinkProvider

        return LinkProvider(target, is_favorite=is_favorite)

    if argv[0] == "--favorites":
        if len(argv) < 2:
            print("usage: python -m dj.curator --favorites <folder-or-url>")
            return 1
        ingest(provider(argv[1], is_favorite=True))
    else:
        ingest(provider(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
