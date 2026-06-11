"""LinkProvider + CLI dispatch tests — resolve/fetch monkeypatched; no network, no DB, no audio."""

import types
from pathlib import Path

import pytest

import dj.curator as curator_mod
import dj.ingest.__main__ as ingest_main
import dj.ingest.provider as provider_mod
from dj.ingest.provider import LinkProvider
from dj.ingest.resolve import TrackRequest
from dj.sources import LocalFolderProvider


def _req(title, **kw):
    return TrackRequest(artist="A", title=title, source="spotify", **kw)


# --- LinkProvider.iter_tracks --------------------------------------------------------


def test_iter_tracks_yields_refs_and_skips_failed_fetch(tmp_path, capsys, monkeypatch):
    reqs = [_req("One"), _req("Bad"), _req("Two")]
    monkeypatch.setattr(provider_mod, "resolve", lambda link, sp=None, runner=None: reqs)

    def fake_fetch(req, out_dir, *, runner=None):
        if req.title == "Bad":
            raise RuntimeError("no YouTube match for A — Bad")
        return Path(out_dir) / f"{req.title}.flac"

    monkeypatch.setattr(provider_mod, "fetch", fake_fetch)

    provider = LinkProvider("https://open.spotify.com/playlist/pl1", is_favorite=True,
                            out_dir=str(tmp_path))
    refs = list(provider.iter_tracks())

    assert [r.path for r in refs] == [str(tmp_path / "One.flac"), str(tmp_path / "Two.flac")]
    assert all(r.source == "spotify" and r.is_favorite for r in refs)
    out = capsys.readouterr().out
    assert "[ingest] spotify playlist: 3 tracks to fetch" in out
    assert "[ingest] FAILED A — Bad: no YouTube match" in out


def test_iter_tracks_defaults_out_dir_to_library_per_source(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_mod, "settings",
                        types.SimpleNamespace(library_dir=str(tmp_path / "lib")))
    req = TrackRequest(title="T", video_url="https://www.youtube.com/watch?v=v1",
                       source="youtube")
    monkeypatch.setattr(provider_mod, "resolve", lambda link, sp=None, runner=None: [req])
    seen = []

    def fake_fetch(req, out_dir, *, runner=None):
        seen.append(Path(out_dir))
        return Path(out_dir) / "T [v1].flac"

    monkeypatch.setattr(provider_mod, "fetch", fake_fetch)

    refs = list(LinkProvider("https://youtu.be/v1").iter_tracks())
    assert seen == [tmp_path / "lib" / "youtube"]
    assert refs[0].source == "youtube" and refs[0].is_favorite is False


def test_iter_tracks_rejects_unsupported_url():
    with pytest.raises(ValueError, match="soundcloud"):
        list(LinkProvider("https://soundcloud.com/a/t").iter_tracks())


# --- curator main(): URL vs folder dispatch ------------------------------------------


def test_curator_main_dispatches_url_vs_folder(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(curator_mod, "ingest", lambda provider: captured.append(provider))

    assert curator_mod.main(["https://open.spotify.com/playlist/x"]) == 0
    assert curator_mod.main([str(tmp_path)]) == 0
    assert curator_mod.main(["--favorites", "https://open.spotify.com/playlist/x"]) == 0

    assert isinstance(captured[0], LinkProvider) and captured[0].is_favorite is False
    assert isinstance(captured[1], LocalFolderProvider)
    assert isinstance(captured[2], LinkProvider) and captured[2].is_favorite is True


# --- python -m dj.ingest --------------------------------------------------------------


def test_ingest_main_wires_url_and_favorites_flag(monkeypatch):
    captured = []
    monkeypatch.setattr(ingest_main, "ingest", lambda provider: captured.append(provider))

    assert ingest_main.main(["https://youtu.be/v1"]) == 0
    assert ingest_main.main(["https://youtu.be/v1", "--favorites"]) == 0

    assert isinstance(captured[0], LinkProvider) and captured[0].is_favorite is False
    assert captured[1].url == "https://youtu.be/v1" and captured[1].is_favorite is True


def test_ingest_main_usage_and_bad_link(capsys, monkeypatch):
    assert ingest_main.main([]) == 1
    assert "usage:" in capsys.readouterr().out

    def raising_ingest(provider):
        list(provider.iter_tracks())  # classify runs here and rejects the URL

    monkeypatch.setattr(ingest_main, "ingest", raising_ingest)
    assert ingest_main.main(["https://soundcloud.com/a/t"]) == 1
    assert "Supported:" in capsys.readouterr().out


def test_curator_main_dispatches_schemeless_link(monkeypatch):
    captured = []
    monkeypatch.setattr(curator_mod, "ingest", lambda provider: captured.append(provider))
    assert curator_mod.main(["open.spotify.com/playlist/x"]) == 0  # pasted without https://
    assert type(captured[0]).__name__ == "LinkProvider"
