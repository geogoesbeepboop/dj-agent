"""Bulk judging tests — scripted prompts + recording sinks; no DB, no model, no network.

Link resolution is faked at judge's seams (judge.classify / judge.resolve return
canned TrackRequests); the lazy-imported collaborators are monkeypatched at their
source modules (dj.vibe.store, dj.taste.pending, dj.taste.embed), so the routing
decision — tag directly vs park a pending review — is the only thing under test.
"""

import types

import numpy as np
import pytest

import dj.taste.judge as judge
from dj.ingest.links import Link
from dj.ingest.resolve import TrackRequest


class _Script:
    """Feed canned answers to a prompt seam in order; empty after exhaustion."""

    def __init__(self, answers):
        self.answers = list(answers)

    def __call__(self, label="", default=""):
        return self.answers.pop(0) if self.answers else default


def _boom(*args, **kwargs):
    raise AssertionError("should not be called")


def _reqs():
    """Two Spotify requests (with ISRC) + one YouTube request."""
    return [
        TrackRequest(artist="Bicep", title="Glue", album="Bicep", duration_s=269.0,
                     isrc="GBABC1200001", spotify_id="t1", source="spotify"),
        TrackRequest(artist="Caribou", title="Odessa", album="Swim", duration_s=312.0,
                     isrc="GBABC1000002", spotify_id="t2", source="spotify"),
        TrackRequest(artist="Boards of Canada", title="Roygbiv", duration_s=150.0,
                     video_url="https://www.youtube.com/watch?v=abc", source="youtube"),
    ]


def _wire(monkeypatch, known=()):
    """Standard rig: db enabled, canned playlist, recording store/pending/embed fakes."""
    monkeypatch.setattr(judge, "settings", types.SimpleNamespace(db_enabled=True))
    monkeypatch.setattr(judge, "classify", lambda url: Link("spotify", "playlist", "pl1", url))
    monkeypatch.setattr(judge, "resolve", lambda link, sp=None, runner=None: _reqs())

    calls = {"set_taste": [], "add": []}
    monkeypatch.setattr(
        "dj.vibe.store.find_track_by_meta",
        lambda artist, title, isrc=None: f"/lib/{title}.mp3" if (artist, title) in known else None,
    )
    monkeypatch.setattr(
        "dj.vibe.store.set_taste",
        lambda path, note, vec, rating=None, role=None:
            calls["set_taste"].append((path, note, vec, rating, role)),
    )
    monkeypatch.setattr("dj.vibe.store.get_track", lambda path: None)  # no prior taste

    def _add(artist, title, note, **kw):
        calls["add"].append((artist, title, note, kw))
        return 41 + len(calls["add"])

    monkeypatch.setattr("dj.taste.pending.add", _add)
    monkeypatch.setattr(
        "dj.taste.embed.embed_note", lambda note: np.zeros(384, dtype=np.float32)
    )
    return calls


def test_judge_tags_known_track_directly(monkeypatch, capsys):
    calls = _wire(monkeypatch, known={("Bicep", "Glue")})
    monkeypatch.setattr(judge, "_prompt_note", _Script(["hands in the air", "", ""]))
    monkeypatch.setattr(judge, "_prompt_rating", lambda: 5)
    monkeypatch.setattr(judge, "_prompt_role", lambda: "peak")

    assert judge.judge("https://open.spotify.com/playlist/pl1") == 1
    [(path, note, vec, rating, role)] = calls["set_taste"]
    assert (path, note, rating, role) == ("/lib/Glue.mp3", "hands in the air", 5, "peak")
    assert vec.shape == (384,)
    assert calls["add"] == []                       # owned tracks never park a review
    assert "✓ tagged in library: Glue.mp3" in capsys.readouterr().out


def test_judge_rejudging_warns_with_prior_rating(monkeypatch, capsys):
    calls = _wire(monkeypatch, known={("Bicep", "Glue")})
    prior = types.SimpleNamespace(taste_source="manual", rating=3, role="build")
    monkeypatch.setattr("dj.vibe.store.get_track", lambda path: prior)
    monkeypatch.setattr(judge, "_prompt_note", _Script(["actually a peak", "", ""]))
    monkeypatch.setattr(judge, "_prompt_rating", lambda: 5)
    monkeypatch.setattr(judge, "_prompt_role", lambda: "peak")

    assert judge.judge("url") == 1
    assert calls["set_taste"][0][3:] == (5, "peak")     # new judgment still overwrites
    out = capsys.readouterr().out
    assert "updated in library (was rating 3, build)" in out


def test_judge_parks_unknown_track_with_identity_passthrough(monkeypatch, capsys):
    calls = _wire(monkeypatch)                      # nothing in the library
    monkeypatch.setattr(judge, "_prompt_note", _Script(["warm rolling bassline", "", ""]))
    monkeypatch.setattr(judge, "_prompt_rating", lambda: 4)
    monkeypatch.setattr(judge, "_prompt_role", lambda: "build")

    assert judge.judge("url") == 1
    assert calls["set_taste"] == []
    [(artist, title, note, kw)] = calls["add"]
    assert (artist, title, note) == ("Bicep", "Glue", "warm rolling bassline")
    assert kw["isrc"] == "GBABC1200001"             # identity rides along for ADR 0008 matching
    assert kw["spotify_id"] == "t1"
    assert kw["album"] == "Bicep"
    assert kw["duration_s"] == pytest.approx(269.0)
    assert kw["rating"] == 4 and kw["role"] == "build"
    assert kw["source"] == "bulk"
    assert "parked review #42" in capsys.readouterr().out


def test_judge_matches_in_library_by_isrc(monkeypatch, capsys):
    # The judged item's ISRC must reach the matcher so a title mismatch still
    # tags the owned file instead of parking a redundant pending review.
    _wire(monkeypatch)
    seen = {}
    monkeypatch.setattr(
        "dj.vibe.store.find_track_by_meta",
        lambda artist, title, isrc=None: (seen.update(isrc=isrc) or "/lib/Glue.mp3"),
    )
    monkeypatch.setattr(judge, "_prompt_note", _Script(["driving", "q"]))
    monkeypatch.setattr(judge, "_prompt_rating", lambda: 4)
    monkeypatch.setattr(judge, "_prompt_role", lambda: "build")

    judge.judge("url")
    assert seen["isrc"] == "GBABC1200001"               # first req's ISRC threaded through


def test_judge_empty_note_skips_track(monkeypatch):
    calls = _wire(monkeypatch, known={("Bicep", "Glue")})
    monkeypatch.setattr(judge, "_prompt_note", _Script(["", "", ""]))
    monkeypatch.setattr(judge, "_prompt_rating", _boom)   # skip = no rating/role prompts
    monkeypatch.setattr(judge, "_prompt_role", _boom)

    assert judge.judge("url") == 0
    assert calls["set_taste"] == [] and calls["add"] == []


def test_judge_quit_stops_iteration_early(monkeypatch):
    calls = _wire(monkeypatch)
    prompts = _Script(["like a sunrise", "q", "never reached"])
    monkeypatch.setattr(judge, "_prompt_note", prompts)
    monkeypatch.setattr(judge, "_prompt_rating", lambda: None)
    monkeypatch.setattr(judge, "_prompt_role", lambda: None)

    assert judge.judge("url") == 1                  # first parked, then quit
    assert len(calls["add"]) == 1
    assert prompts.answers == ["never reached"]     # the third track was never prompted


def test_judge_db_disabled_returns_minus_one(monkeypatch, capsys):
    monkeypatch.setattr(judge, "settings", types.SimpleNamespace(db_enabled=False))
    monkeypatch.setattr(judge, "classify", _boom)   # guard fires before any resolution

    assert judge.judge("url") == -1
    assert "DATABASE_URL" in capsys.readouterr().out


def test_judge_summary_counts_printed(monkeypatch, capsys):
    _wire(monkeypatch, known={("Bicep", "Glue")})
    monkeypatch.setattr(judge, "_prompt_note", _Script(["big", "soft", ""]))  # tag, park, skip
    monkeypatch.setattr(judge, "_prompt_rating", lambda: None)
    monkeypatch.setattr(judge, "_prompt_role", lambda: None)

    assert judge.judge("url") == 2
    out = capsys.readouterr().out
    assert "[judge] 3 tracks from spotify playlist" in out
    assert "tagged 1 directly, parked 1, skipped 1" in out


def test_main_without_args_prints_usage(capsys):
    assert judge.main([]) == 1
    out = capsys.readouterr().out
    assert "usage: python -m dj.taste.judge" in out
    assert "python -m dj.ingest" in out             # points at the path that fetches files


def test_prompt_note_eof_quits_session(monkeypatch):
    def _eof(_label):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert judge._prompt_note() == "q"  # closed stdin ends the session, not skip-all
