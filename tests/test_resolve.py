"""Link resolution tests — fake yt-dlp runner + fake spotipy client; no network, no DB.

run_ytdlp's default path is exercised by monkeypatching subprocess.run, so no
process is ever spawned and yt-dlp itself need not be installed to test.
"""

import json
import sys
import types

import pytest

import dj.ingest.resolve as resolve_mod
from dj.ingest.links import Link
from dj.ingest.resolve import TrackRequest, _split_title, resolve, run_ytdlp


class _Runner:
    """Recording fake yt-dlp runner: captures arg lists, returns canned JSON."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        return json.dumps(self.payload)


# --- run_ytdlp ----------------------------------------------------------------


def test_run_ytdlp_invokes_module_of_this_interpreter(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    assert run_ytdlp(["-J", "--no-playlist", "URL"]) == "{}"
    assert seen["argv"] == [sys.executable, "-m", "yt_dlp", "-J", "--no-playlist", "URL"]


def test_run_ytdlp_raises_with_stderr_tail(monkeypatch):
    proc = types.SimpleNamespace(
        returncode=1, stdout="", stderr="warning: noise\nERROR: Video unavailable\n"
    )
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: proc)
    with pytest.raises(RuntimeError, match="Video unavailable"):
        run_ytdlp(["-J", "URL"])


# --- youtube: title splitting ---------------------------------------------------


@pytest.mark.parametrize("title,uploader,artist,clean", [
    ("Boards of Canada - Roygbiv (Official Audio)", "WARP", "Boards of Canada", "Roygbiv"),
    ("Artist – Song", "ch", "Artist", "Song"),               # en dash
    ("Artist — Song", "ch", "Artist", "Song"),               # em dash
    ("Artist | Song [Official Video]", "ch", "Artist", "Song"),
    ("Artist - Song (feat. MC) (Lyrics)", "ch", "Artist", "Song (feat. MC)"),  # feat kept
    ("Artist - Song (Bicep Remix) [4K]", "ch", "Artist", "Song (Bicep Remix)"),  # remix kept
    ("Artist - Song【MV】", "ch", "Artist", "Song"),
    ("Roygbiv (Official Audio)", "Some Channel - Topic", "Some Channel", "Roygbiv"),
    ("Roygbiv", "Some Channel", "Some Channel", "Roygbiv"),  # no separator, plain channel
])
def test_split_title_handles_separators_and_decoration(title, uploader, artist, clean):
    assert _split_title(title, uploader) == (artist, clean)


# --- youtube: resolve -----------------------------------------------------------


def test_resolve_video_builds_one_request_with_canonical_url():
    runner = _Runner({
        "id": "abc123",
        "title": "Boards of Canada - Roygbiv (Official Audio)",
        "uploader": "WARP Records",
        "duration": 150,
    })
    link = Link("youtube", "video", "abc123", "https://youtu.be/abc123")
    [req] = resolve(link, runner=runner)
    assert runner.calls == [["-J", "--no-playlist", "https://youtu.be/abc123"]]
    assert req.artist == "Boards of Canada"
    assert req.title == "Roygbiv"
    assert req.duration_s == pytest.approx(150.0)
    assert req.video_url == "https://www.youtube.com/watch?v=abc123"
    assert req.source == "youtube" and req.spotify_id is None and req.isrc is None


def test_resolve_video_falls_back_to_topic_uploader():
    runner = _Runner({"id": "x1", "title": "Roygbiv (Official Audio)",
                      "uploader": "Some Channel - Topic", "duration": 150})
    [req] = resolve(Link("youtube", "video", "x1", "u"), runner=runner)
    assert (req.artist, req.title) == ("Some Channel", "Roygbiv")


def test_resolve_playlist_skips_dead_entries_and_keeps_flat_durations():
    runner = _Runner({"entries": [
        {"id": "v1", "title": "A - B", "duration": 100, "uploader": "ch"},
        {"id": "v2", "title": "[Deleted video]"},
        {"id": "v3", "title": "[Private video]"},
        {"id": None, "title": "C - D"},                       # missing id
        {"id": "v4", "title": "E - F", "uploader": "ch"},     # flat entry, no duration
    ]})
    link = Link("youtube", "playlist", "PL1", "https://www.youtube.com/playlist?list=PL1")
    reqs = resolve(link, runner=runner)
    assert runner.calls == [["-J", "--flat-playlist", link.url]]
    assert [(r.artist, r.title) for r in reqs] == [("A", "B"), ("E", "F")]
    assert reqs[0].duration_s == pytest.approx(100.0)
    assert reqs[1].duration_s is None
    assert reqs[1].video_url == "https://www.youtube.com/watch?v=v4"


# --- spotify ---------------------------------------------------------------------


def _full(tid, name, *artists, ms=200_000, isrc=None, album="Some LP"):
    """A full Spotify track object as sp.track/sp.tracks/playlist items return it."""
    return {
        "id": tid,
        "name": name,
        "artists": [{"name": a} for a in artists],
        "duration_ms": ms,
        "external_ids": {"isrc": isrc} if isrc else {},
        "album": {"name": album},
    }


class _FakeSpotify:
    """Minimal spotipy stand-in: canned pages keyed by their 'next' token."""

    def __init__(self, *, track=None, first_pages=None, more_pages=None,
                 album=None, full_tracks=None, top_tracks=None):
        self._track = track
        self._first = first_pages or {}      # method name -> first page
        self._pages = more_pages or {}       # next-token -> page
        self._album = album
        self._full = full_tracks or {}       # id -> full track object
        self._top = top_tracks or []
        self.tracks_calls = []

    def track(self, tid):
        return self._track

    def playlist_items(self, pid):
        return self._first["playlist_items"]

    def album(self, aid):
        return self._album

    def album_tracks(self, aid):
        return self._first["album_tracks"]

    def tracks(self, ids):
        self.tracks_calls.append(list(ids))
        return {"tracks": [self._full[i] for i in ids]}

    def artist_top_tracks(self, aid):
        return {"tracks": self._top}

    def next(self, page):
        return self._pages[page["next"]]


def test_resolve_track_maps_all_fields():
    sp = _FakeSpotify(track=_full("t1", "Glue", "Bicep", "Guest", ms=225_000,
                                  isrc="GBABC1200001", album="Bicep"))
    [req] = resolve(Link("spotify", "track", "t1", "url"), sp=sp)
    assert req.artist == "Bicep, Guest"
    assert req.title == "Glue"
    assert req.album == "Bicep"
    assert req.duration_s == pytest.approx(225.0)
    assert req.isrc == "GBABC1200001"
    assert req.spotify_id == "t1"
    assert req.source == "spotify" and req.video_url is None


def test_resolve_playlist_pages_and_skips_unplayable(capsys):
    page1 = {"items": [
        {"track": _full("t1", "X", "A")},
        {"track": None},                                     # deleted
        {"track": {"is_local": True, "name": "rip.mp3"}},    # local file
    ], "next": "p2"}
    page2 = {"items": [{"track": _full("t2", "Y", "B")}], "next": None}
    sp = _FakeSpotify(first_pages={"playlist_items": page1}, more_pages={"p2": page2})
    reqs = resolve(Link("spotify", "playlist", "pl1", "url"), sp=sp)
    assert [r.title for r in reqs] == ["X", "Y"]
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and "2" in out[0]                   # one summary line, both skips


def test_resolve_playlist_reads_new_item_key(capsys):
    # Spotify now nests the entity under "item" (not "track"); resolve must read it.
    page = {"items": [
        {"item": _full("t1", "KING", "Kanye West")},
        {"item": None},                                      # deleted
        {"is_local": True, "item": {"is_local": True, "name": "rip.mp3"}},
    ], "next": None}
    sp = _FakeSpotify(first_pages={"playlist_items": page})
    reqs = resolve(Link("spotify", "playlist", "pl1", "url"), sp=sp)
    assert [r.title for r in reqs] == ["KING"]
    assert "2" in capsys.readouterr().out                    # two skipped


def test_resolve_album_batches_full_lookups_and_names_album():
    # 60 simplified items (no external_ids/album) -> two sp.tracks batches (50 + 10).
    ids = [f"t{i}" for i in range(60)]
    simplified = [{"id": i, "name": f"Song {i}"} for i in ids]
    page1 = {"items": simplified[:40], "next": "p2"}
    page2 = {"items": simplified[40:], "next": None}
    sp = _FakeSpotify(
        first_pages={"album_tracks": page1},
        more_pages={"p2": page2},
        album={"name": "Isles"},
        full_tracks={i: _full(i, f"Song {i}", "Bicep", isrc=f"ISRC{i}") for i in ids},
    )
    reqs = resolve(Link("spotify", "album", "al1", "url"), sp=sp)
    assert [len(c) for c in sp.tracks_calls] == [50, 10]
    assert len(reqs) == 60
    assert reqs[0].album == "Isles"                          # from sp.album, not the item
    assert reqs[0].isrc == "ISRCt0"                          # from the batched full object
    assert reqs[59].spotify_id == "t59"


def test_resolve_artist_uses_top_tracks():
    sp = _FakeSpotify(top_tracks=[_full("t1", "X", "A"), _full("t2", "Y", "A")])
    reqs = resolve(Link("spotify", "artist", "ar1", "url"), sp=sp)
    assert [r.title for r in reqs] == ["X", "Y"]
    assert all(r.source == "spotify" for r in reqs)


def test_resolve_spotify_without_creds_raises(monkeypatch):
    monkeypatch.setattr(resolve_mod, "settings", types.SimpleNamespace(
        spotify_client_id="", spotify_client_secret=""))
    with pytest.raises(RuntimeError, match="SPOTIFY_CLIENT_ID"):
        resolve(Link("spotify", "track", "t1", "url"))


def test_resolve_spotify_without_login_points_to_login_command(monkeypatch, tmp_path):
    # Creds present but no cached user token → fail fast with the --login fix,
    # never block on an interactive browser prompt (would hang a piped ingest).
    monkeypatch.setattr(resolve_mod, "settings", types.SimpleNamespace(
        spotify_client_id="id", spotify_client_secret="sec",
        spotify_redirect_uri="http://127.0.0.1:8888/callback",
        spotify_cache_path=str(tmp_path / "no-such-cache")))
    with pytest.raises(RuntimeError, match="--login"):
        resolve(Link("spotify", "track", "t1", "url"))


# --- display ---------------------------------------------------------------------


def test_display_omits_missing_pieces():
    assert TrackRequest(artist="A", title="T", duration_s=225).display == "A — T (3:45)"
    assert TrackRequest(artist="A", title="T").display == "A — T"
    assert TrackRequest(title="T", duration_s=61).display == "T (1:01)"
    assert TrackRequest(video_url="https://youtu.be/x").display == "https://youtu.be/x"


# --- review fixes: tolerant run_ytdlp, earliest separator, Spotify 404 hint ----------


def test_run_ytdlp_keeps_partial_json_on_nonzero_exit(monkeypatch, capsys):
    proc = types.SimpleNamespace(
        returncode=1, stdout='{"entries": []}', stderr="ERROR: one dead entry\n"
    )
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: proc)
    assert run_ytdlp(["-J", "URL"]) == '{"entries": []}'   # usable output wins
    assert "one dead entry" in capsys.readouterr().out      # but the failure is surfaced


def test_run_ytdlp_raises_clear_error_on_null_stdout(monkeypatch):
    # Unfetchable links (removed/private/region-locked video, nonexistent playlist)
    # exit nonzero and print a bare `null` — not a partial success. Surface clearly.
    proc = types.SimpleNamespace(
        returncode=1, stdout="null\n", stderr="ERROR: The playlist does not exist\n"
    )
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: proc)
    with pytest.raises(RuntimeError, match="could not fetch this link"):
        run_ytdlp(["-J", "--flat-playlist", "URL"])


def test_resolve_video_raises_clean_error_when_ytdlp_returns_null():
    # Defense in depth: even if `null` reaches a caller, fail with a clear message
    # rather than `AttributeError: 'NoneType' object has no attribute 'get'`.
    runner = _Runner(None)  # json.dumps(None) == "null"
    with pytest.raises(RuntimeError, match="no video data"):
        resolve(Link("youtube", "video", "x1", "https://youtu.be/x1"), runner=runner)


def test_resolve_playlist_raises_clean_error_when_ytdlp_returns_null():
    runner = _Runner(None)
    with pytest.raises(RuntimeError, match="no playlist data"):
        resolve(Link("youtube", "playlist", "PLx", "url"), runner=runner)


def test_split_title_uses_earliest_separator():
    assert _split_title("A | B - C", "ch") == ("A", "B - C")


def test_resolve_spotify_404_names_the_editorial_playlist_restriction():
    class _Sp:
        def playlist_items(self, _id):
            raise type("SpotifyException", (Exception,), {"http_status": 404})("404")

    link = Link("spotify", "playlist", "37i9dQZF1DXdead", "url")
    with pytest.raises(RuntimeError, match="editorial/algorithmic"):
        resolve(link, sp=_Sp())
