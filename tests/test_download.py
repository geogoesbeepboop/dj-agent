"""Download tests — recording fake yt-dlp runners + one real tiny FLAC; no network, no DB.

The tag round-trip test writes 0.2 s of silence with soundfile (a core dep) so
mutagen has a real FLAC to stamp — still no network and no model downloads.
"""

import json

import pytest

import dj.ingest.download as download_mod
from dj.ingest.download import _write_tags, fetch, pick_best
from dj.ingest.resolve import TrackRequest
from dj.metadata import read_tags


class _Runner:
    """Recording fake yt-dlp runner: captures arg lists, replays canned stdout in order."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        return self.outputs.pop(0)


def _entry(vid, title, duration=None, uploader="some channel"):
    return {"id": vid, "title": title, "duration": duration, "uploader": uploader}


# --- direct download (video_url set) ---------------------------------------------


def test_fetch_direct_passes_exact_ytdlp_args_and_parses_path(tmp_path):
    out = tmp_path / "lib"
    final = out / "Song [abc123].flac"
    runner = _Runner(f"[pre] some progress line\n{final}\n\n")
    req = TrackRequest(video_url="https://www.youtube.com/watch?v=abc123", source="youtube")
    path = fetch(req, out, runner=runner)
    assert runner.calls == [[
        "-x", "--audio-format", "flac", "--audio-quality", "0",
        "--no-playlist", "--no-simulate",
        "--print", "after_move:filepath",
        "-o", str(out / "%(title)s [%(id)s].%(ext)s"),
        "https://www.youtube.com/watch?v=abc123",
    ]]
    assert path == final          # last non-empty stdout line is the filepath
    assert out.is_dir()           # out_dir was created


def test_fetch_direct_raises_when_stdout_has_no_path(tmp_path):
    runner = _Runner("\n   \n")
    req = TrackRequest(video_url="https://www.youtube.com/watch?v=abc123")
    with pytest.raises(RuntimeError, match="no output path"):
        fetch(req, tmp_path, runner=runner)


def test_fetch_direct_skips_when_id_already_on_disk(tmp_path):
    existing = tmp_path / "Whatever Title [abc123].flac"
    existing.write_bytes(b"")
    runner = _Runner()  # any call would pop from empty and blow up
    req = TrackRequest(video_url="https://www.youtube.com/watch?v=abc123")
    assert fetch(req, tmp_path, runner=runner) == existing
    assert runner.calls == []


# --- search download (spotify-sourced, no video_url) ------------------------------


def test_fetch_search_queries_then_downloads_best_and_tags(tmp_path, monkeypatch):
    tagged = []
    monkeypatch.setattr(download_mod, "_write_tags", lambda p, r: tagged.append((p, r)))
    final = tmp_path / "Artist - Song [v2].flac"
    search = {"entries": [
        _entry("v1", "Artist - Song (Live)", 230),
        _entry("v2", "Artist - Song", 224, uploader="Artist - Topic"),
    ]}
    runner = _Runner(json.dumps(search), f"{final}\n")
    req = TrackRequest(artist="Artist", title="Song", album="LP", duration_s=224.0,
                       isrc="USRC12345678", source="spotify")
    path = fetch(req, tmp_path, runner=runner)
    assert runner.calls[0] == ["-J", "ytsearch5:Artist Song"]
    assert runner.calls[1] == [
        "-x", "--audio-format", "flac", "--audio-quality", "0",
        "--no-playlist", "--no-simulate",
        "--print", "after_move:filepath",
        "-o", str(tmp_path / "%(title)s [%(id)s].%(ext)s"),
        "https://www.youtube.com/watch?v=v2",
    ]
    assert path == final
    assert tagged == [(final, req)]


def test_fetch_search_skips_download_when_choice_already_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(download_mod, "_write_tags", lambda p, r: None)
    existing = tmp_path / "Artist - Song [v2].flac"
    existing.write_bytes(b"")
    search = {"entries": [_entry("v2", "Artist - Song", 224)]}
    runner = _Runner(json.dumps(search))  # only the search call is budgeted
    req = TrackRequest(artist="Artist", title="Song", duration_s=224.0, source="spotify")
    assert fetch(req, tmp_path, runner=runner) == existing
    assert len(runner.calls) == 1


def test_fetch_search_raises_naming_the_track_on_no_entries(tmp_path):
    runner = _Runner(json.dumps({"entries": []}))
    req = TrackRequest(artist="Bicep", title="Glue", source="spotify")
    with pytest.raises(RuntimeError, match="Bicep — Glue"):
        fetch(req, tmp_path, runner=runner)


# --- pick_best ---------------------------------------------------------------------


@pytest.mark.parametrize("entries,target,title,winner", [
    # duration dominance: close-but-plain beats far-but-bonused (Topic + official audio)
    ([_entry("near", "Artist - Song", 226),
      _entry("far", "Artist - Song (Official Audio)", 280, uploader="Artist - Topic")],
     224.0, "Song", "near"),
    # Topic-channel bonus breaks a duration tie (uploader key)
    ([_entry("plain", "Artist - Song", 224),
      _entry("topic", "Artist - Song", 224, uploader="Artist - Topic")],
     224.0, "Song", "topic"),
    # ... and via the channel key too
    ([_entry("plain", "Artist - Song", 224),
      {"id": "topic", "title": "Artist - Song", "duration": 224, "channel": "Artist - Topic"}],
     224.0, "Song", "topic"),
    # "official audio" in the title outranks a plain title at equal duration
    ([_entry("plain", "Artist - Song", 224),
      _entry("oa", "Artist - Song (Official Audio)", 224)],
     224.0, "Song", "oa"),
    # live penalty: the live cut loses even when its duration is closer
    ([_entry("live", "Artist - Song (Live)", 224),
      _entry("studio", "Artist - Song", 230)],
     224.0, "Song", "studio"),
    # allow-token passthrough: asking for "Song (Live)" un-penalizes live entries
    ([_entry("live", "Artist - Song (Live)", 224),
      _entry("studio", "Artist - Song", 230)],
     224.0, "Song (Live)", "live"),
    # word boundary: "Alive" is not "live"
    ([_entry("alive", "Artist - Alive", 224),
      _entry("other", "Artist - Alive", 230)],
     224.0, "Alive", "alive"),
    # no target duration: bonuses decide, length ignored
    ([_entry("plain", "Artist - Song", 100),
      _entry("topic", "Artist - Song", 500, uploader="Artist - Topic")],
     None, "Song", "topic"),
])
def test_pick_best_scoring_table(entries, target, title, winner):
    choice = pick_best(entries, target, request_title=title)
    assert choice is not None and choice["id"] == winner


def test_pick_best_ties_keep_youtube_order():
    a, b = _entry("first", "Artist - Song", 224), _entry("second", "Artist - Song", 224)
    assert pick_best([a, b], 224.0, request_title="Song") is a


def test_pick_best_skips_dead_entries_and_handles_empty():
    assert pick_best([], 224.0) is None
    assert pick_best([None, {"title": "no id"}], 224.0) is None
    [ok] = [e for e in [None, _entry("ok", "Artist - Song", 224)] if e]
    assert pick_best([None, ok], 224.0) is ok


# --- tag round-trip ------------------------------------------------------------------


def test_write_tags_round_trips_through_read_tags(tmp_path):
    import numpy as np
    import soundfile as sf

    path = tmp_path / "Artist - Song [v2].flac"
    sf.write(path, np.zeros(int(0.2 * 22050), dtype="float32"), 22050)

    req = TrackRequest(artist="Bicep", title="Glue", album="Bicep",
                       isrc="USRC12345678", source="spotify")
    _write_tags(path, req)

    tags = read_tags(str(path))
    assert tags.artist == "Bicep"
    assert tags.title == "Glue"
    assert tags.isrc == "USRC12345678"  # the parked-review match key (ADR 0008)


# --- review fixes: tag stamping on the direct path + .part-safe idempotence ----------


def test_fetch_direct_stamps_resolved_identity(tmp_path, monkeypatch):
    stamped = []
    monkeypatch.setattr(download_mod, "_write_tags", lambda p, r: stamped.append((p, r)))
    runner = _Runner(f"{tmp_path}/Video Title [abc123].flac\n")
    req = TrackRequest(artist="Boards of Canada", title="Roygbiv",
                       video_url="https://www.youtube.com/watch?v=abc123")
    path = fetch(req, tmp_path, runner=runner)
    assert stamped == [(path, req)]  # YouTube rips carry no tags — identity must be stamped


def test_fetch_ignores_interrupted_part_files(tmp_path):
    # yt-dlp leaves same-stem intermediates on Ctrl-C; only a finished FLAC may
    # short-circuit, otherwise a truncated rip gets pinned forever.
    (tmp_path / "Title [abc123].webm.part").write_bytes(b"")
    (tmp_path / "Title [abc123].webm").write_bytes(b"")
    runner = _Runner(f"{tmp_path}/Title [abc123].flac\n")
    req = TrackRequest(title="Title", video_url="https://www.youtube.com/watch?v=abc123")
    assert fetch(req, tmp_path, runner=runner).name == "Title [abc123].flac"
    assert len(runner.calls) == 1  # it really downloaded despite the leftovers
