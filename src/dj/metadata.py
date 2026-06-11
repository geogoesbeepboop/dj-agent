"""Read file tags (genre, mood, title, artist) via mutagen.

These are a *cheap* semantic bridge: storing genre + descriptor tags lets the
Selector do keyword filtering ("dreamy" works if a human/source tagged it
"dreamy") to complement CLAP's audio-inferred semantics. Unlike CLAP, tags are
sparse and inconsistent — they're a bonus filter, not the primary signal.

mutagen is imported lazily and every field degrades gracefully to empty, so an
untagged file still ingests fine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TrackTags:
    title: str = ""
    artist: str = ""
    genre: str = ""
    year: str = ""
    tags: list[str] = field(default_factory=list)  # normalized descriptor keywords
    isrc: str = ""                                  # recording id → match parked reviews (ADR 0008)

    def merged_with(self, extra: list[str]) -> "TrackTags":
        """Return a copy with extra source-supplied tags folded in (deduped)."""
        combined = list(dict.fromkeys([*self.tags, *(_norm(t) for t in extra)]))
        return TrackTags(self.title, self.artist, self.genre, self.year, combined, self.isrc)


def read_tags(path: str) -> TrackTags:
    """Best-effort tag extraction. Never raises on missing/garbled tags."""
    try:
        from mutagen import File as MutagenFile

        mf = MutagenFile(path, easy=True)
    except Exception:
        return TrackTags()
    if mf is None or not getattr(mf, "tags", None):
        return TrackTags()

    title = _first(mf, "title")
    artist = _first(mf, "artist")
    genre = _first(mf, "genre")
    year = _first(mf, "date") or _first(mf, "year")

    # Descriptor tags: genre + any 'mood'/'comment' fields, split and normalized.
    raw = [genre, _first(mf, "mood"), _first(mf, "comment")]
    tags = _dedupe(t for chunk in raw for t in _split(chunk))
    return TrackTags(title=title, artist=artist, genre=genre, year=year, tags=tags,
                     isrc=_read_isrc(path))


def _read_isrc(path: str) -> str:
    """Best-effort ISRC across ID3 (TSRC), MP4 (freeform atom), and Vorbis (ISRC).

    EasyID3 doesn't surface ISRC, so this re-opens the file in raw mode. Degrades
    to "" on anything missing or garbled — an untagged file still ingests fine.
    """
    try:
        from mutagen import File as MutagenFile

        mf = MutagenFile(path)
    except Exception:
        return ""
    tags = getattr(mf, "tags", None)
    if not tags:
        return ""

    getall = getattr(tags, "getall", None)
    if getall:  # ID3 (mp3): TSRC frame
        try:
            frames = getall("TSRC")
            if frames and frames[0].text:
                return str(frames[0].text[0]).strip()
        except Exception:
            pass
    for key in ("----:com.apple.iTunes:ISRC", "----:com.apple.iTunes:isrc",  # MP4
                "isrc", "ISRC"):                                             # Vorbis (flac/ogg)
        try:
            val = tags.get(key)
        except Exception:
            val = None
        if val:
            v = val[0]
            return (v.decode("utf-8", "ignore") if isinstance(v, (bytes, bytearray))
                    else str(v)).strip()
    return ""


def _first(mf, key: str) -> str:
    try:
        val = mf.get(key)
    except Exception:
        return ""
    if not val:
        return ""
    return str(val[0] if isinstance(val, list) else val).strip()


def _split(chunk: str) -> list[str]:
    if not chunk:
        return []
    parts = re.split(r"[,;/|]+|\s{2,}", chunk)
    return [_norm(p) for p in parts if _norm(p)]


def _norm(s: str) -> str:
    return s.strip().lower()


def _dedupe(items) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))
