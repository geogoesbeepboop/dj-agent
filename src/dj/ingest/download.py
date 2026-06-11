"""Download a TrackRequest's audio into the library as FLAC (ADR 0009).

This is the back half of link ingestion: resolve produced catalog-level
TrackRequests, and this module turns each one into a local file the Curator
can ingest. YouTube-sourced requests download their video_url directly;
Spotify-sourced requests have no audio source, so they're matched against a
YouTube search (`ytsearch5`) scored by `pick_best` — duration closeness vs the
catalog duration dominates, because the wrong *cut* (live, sped up, extended)
poisons BPM, sections, and the vibe vector alike.

Downloads are idempotent: the output template ends in "[<video id>]", and a
**finished FLAC** already carrying that id short-circuits the fetch, so
re-pasting a playlist only pulls what's new (interrupted `.part` leftovers
don't count and get resumed). Every download — YouTube-direct or
Spotify-searched — is tag-stamped with the resolved identity, because the
judge/pending match keys and the DB's title/artist columns live or die on it. Scoring is a pure function; yt-dlp runs behind
resolve's `runner=` seam and mutagen imports lazily, so the fast tests need no
network and no downloads.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dj.ingest.resolve import TrackRequest, run_ytdlp

# Title tokens that usually mean "not the studio recording". An entry is only
# penalized for a token the request's own title doesn't carry — if I asked for
# "Song (Live)", live versions ARE the target.
_BAD_TOKENS = ("live", "sped up", "slowed", "music video", "reaction", "cover")

# Off by more than this vs the catalog duration is almost never the same
# recording (radio edit / extended / sped up). Heavily penalized rather than
# excluded, so a sparse result page can still yield the least-bad option.
_DURATION_TOLERANCE_S = 25.0


def fetch(req: TrackRequest, out_dir: str | Path, *, runner=None) -> Path:
    """Materialize one TrackRequest as a local audio file under `out_dir`.

    YouTube requests download their video_url as-is. Spotify requests search
    YouTube, pick the best-scored result, download it, and stamp the catalog's
    own tags over the rip (`_write_tags`) so identity survives the detour.
    Raises RuntimeError when the search finds nothing usable or yt-dlp fails.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if req.video_url:
        path = _download(req.video_url, out, runner=runner)
        _write_tags(path, req)  # rip carries no tags; the resolved (artist, title)
        return path             # is the identity judge/pending matched on

    stdout = run_ytdlp(["-J", f"ytsearch5:{req.artist} {req.title}"], runner=runner)
    entries = json.loads(stdout).get("entries") or []
    choice = pick_best(entries, req.duration_s, request_title=req.title)
    if choice is None:
        raise RuntimeError(f"no YouTube match for {req.display}")
    path = _download(f"https://www.youtube.com/watch?v={choice['id']}", out, runner=runner)
    _write_tags(path, req)
    return path


def pick_best(entries: list[dict], target_duration_s: float | None,
              *, request_title: str = "") -> dict | None:
    """Pick the search result most likely to be the studio recording. Pure.

    Score = duration closeness (dominant when the catalog duration is known:
    >25s off costs more than any bonus can recover) + a bonus for YouTube's
    auto-generated " - Topic" uploads and "(official) audio" titles − a penalty
    per red-flag token ("live", "cover", …). `request_title` is the allow-list
    seam: a red-flag token appearing in the request's own title (e.g. asking
    for "X (Live)") is not penalized. Ties keep YouTube's relevance order
    (first wins). Returns None when no usable entry exists.
    """
    best, best_score = None, float("-inf")
    for entry in entries:
        if not entry or not entry.get("id"):
            continue
        score = _score(entry, target_duration_s, request_title.lower())
        if score > best_score:
            best, best_score = entry, score
    return best


def _score(entry: dict, target_s: float | None, request_title: str) -> float:
    score = 0.0
    duration = entry.get("duration")
    if target_s is not None:
        if duration:
            delta = abs(float(duration) - target_s)
            score -= delta
            if delta > _DURATION_TOLERANCE_S:
                score -= 500.0
        else:
            score -= 50.0  # length unknown: can't confirm it's the right cut
    title = (entry.get("title") or "").lower()
    uploader = entry.get("uploader") or entry.get("channel") or ""
    if uploader.endswith(" - Topic"):
        score += 30.0
    if "official audio" in title:
        score += 20.0
    elif _has_token(title, "audio"):
        score += 10.0
    for token in _BAD_TOKENS:
        if _has_token(title, token) and not _has_token(request_title, token):
            score -= 40.0
    return score


def _has_token(text: str, token: str) -> bool:
    """Whole-word match, so 'live' doesn't fire on 'Alive' or 'Delivered'."""
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def _download(video_url: str, out_dir: Path, *, runner=None) -> Path:
    """Download one video as FLAC; skip if a file with its id already exists.

    `--print after_move:filepath` makes yt-dlp's last stdout line the final
    file path — no directory diffing or extension guessing needed.
    """
    vid = _video_id(video_url)
    existing = _existing(out_dir, vid)
    if existing is not None:
        return existing
    stdout = run_ytdlp([
        "-x", "--audio-format", "flac", "--audio-quality", "0",
        "--no-playlist", "--no-simulate",
        "--print", "after_move:filepath",
        "-o", str(out_dir / "%(title)s [%(id)s].%(ext)s"),
        video_url,
    ], runner=runner)
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError(f"yt-dlp printed no output path for {video_url}")
    return Path(lines[-1])


def _video_id(watch_url: str) -> str:
    parsed = urlparse(watch_url)
    v = parse_qs(parsed.query).get("v")
    if v and v[0]:
        return v[0]
    parts = [p for p in parsed.path.split("/") if p]  # youtu.be/<id>, /shorts/<id>
    if parts:
        return parts[-1]
    raise ValueError(f"can't find a video id in {watch_url!r}")


def _existing(out_dir: Path, video_id: str) -> Path | None:
    """The finished FLAC already carrying "[<id>]" — the idempotence key.

    Matched by suffix rather than Path.glob (glob would treat the bracketed id
    as a character class), and only a completed `[<id>].flac` counts: yt-dlp
    leaves `.part`/pre-conversion intermediates with the same stem in this dir
    when interrupted, and short-circuiting on those would pin a truncated file
    forever instead of letting a re-run resume the download.
    """
    needle = f"[{video_id}].flac"
    for p in sorted(out_dir.iterdir()):
        if p.is_file() and p.name.endswith(needle):
            return p
    return None


def _write_tags(path: Path, req: TrackRequest) -> None:
    """Stamp the resolved identity onto a download.

    A bare rip carries no usable tags (and a video's embedded metadata names
    the *video*, not the recording) — so write artist/title/album from the
    resolve step, and above all the ISRC when Spotify supplied one: it's the
    confident match key that lets a parked taste review auto-apply at ingest
    (ADR 0008). For YouTube-direct downloads the stamped (artist, title) is
    the same heuristic split the judge parked under, so name+duration matching
    stays aligned. Fields are written exactly where
    dj.metadata reads them — for FLAC that's Vorbis comments, including a
    plain "isrc" comment (`metadata._read_isrc`). Best-effort: an unwritable
    file is still worth ingesting, so failures degrade silently.
    """
    from mutagen import File as MutagenFile

    try:
        mf = MutagenFile(str(path), easy=True)
    except Exception:
        return
    if mf is None:
        return
    if mf.tags is None:
        mf.add_tags()
    if req.artist:
        mf["artist"] = req.artist
    if req.title:
        mf["title"] = req.title
    if req.album:
        mf["album"] = req.album
    if req.isrc:
        try:
            mf["isrc"] = req.isrc  # Vorbis comment (flac/ogg); TSRC via EasyID3 (mp3)
        except (KeyError, ValueError):
            pass  # format with no easy isrc mapping — downloads are FLAC, so moot
    mf.save()
