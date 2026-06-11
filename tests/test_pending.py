"""Parked-review matching (ADR 0008): pure normalization + find_match tiers (no DB).

The matcher decides what the Curator may auto-apply, so the confident/ambiguous
boundary is the thing under test. The last test exercises the Curator's
precedence guard with fakes (still no DB/model/audio).
"""

import dj.curator as curator
from dj.taste import pending
from dj.taste.pending import (
    Candidate,
    MatchResult,
    PendingReview,
    existing_match,
    find_match,
    match_key,
    normalize_artist,
    normalize_title,
)


def test_existing_match_dedupes_by_isrc_then_normalized_name():
    rows = [
        PendingReview(artist="Bicep", title="Glue", note="n1", id=1, isrc="GB1234567890"),
        PendingReview(artist="Other", title="Song", note="n2", id=2),
    ]
    assert existing_match("GB1234567890", "X", "Y", rows).id == 1        # ISRC wins
    assert existing_match(None, "bicep", "Glue (Radio Edit)", rows).id == 1  # normalized name
    assert existing_match(None, "Nobody", "Nothing", rows) is None


def _rev(artist, title, **kw):
    return PendingReview(artist=artist, title=title, note="hands in the air", **kw)


# --- normalization ----------------------------------------------------------


def test_normalize_title_drops_version_qualifiers():
    assert normalize_title("Strobe (Radio Edit)") == "strobe"
    assert normalize_title("Strobe - 2011 Remaster") == "strobe"
    assert normalize_title("Glue [Extended Mix]") == "glue"
    assert normalize_title("Glue feat. Someone") == "glue"


def test_normalize_title_diacritics_and_ampersand():
    assert normalize_title("Café del Mar") == "cafe del mar"
    assert normalize_title("You & Me") == "you and me"


def test_normalize_artist_takes_primary_keeps_single_acts():
    assert normalize_artist("Calvin Harris, Dua Lipa") == "calvin harris"
    assert normalize_artist("Bicep feat. Someone") == "bicep"
    assert normalize_artist("RÜFÜS DU SOL") == "rufus du sol"
    # "&"/"and" are an act's name, not a credit list — keep them.
    assert normalize_artist("Above & Beyond") == "above and beyond"
    assert normalize_artist("Above and Beyond") == "above and beyond"


def test_match_key_collapses_variants():
    assert match_key("Bicep", "Glue (Original Mix)") == match_key("bicep", "Glue")


# --- find_match tiers -------------------------------------------------------


def test_isrc_match_is_confident_even_with_a_different_name():
    rows = [_rev("Whatever", "Different Title", id=1, isrc="GBABC1200001")]
    cand = Candidate(artist="Bicep", title="Glue", isrc="gbabc1200001")  # case-insensitive
    m = find_match(cand, rows)
    assert m is not None and m.confident and m.reason == "isrc" and m.review.id == 1


def test_name_plus_duration_is_confident():
    rows = [_rev("Bicep", "Glue", id=2, duration_s=270.0)]
    cand = Candidate(artist="Bicep", title="Glue (Original Mix)", duration_s=272.5)
    m = find_match(cand, rows)
    assert m is not None and m.confident and m.reason == "name+duration"


def test_missing_duration_still_confident_on_unique_name():
    rows = [_rev("Bicep", "Glue", id=3, duration_s=None)]
    cand = Candidate(artist="Bicep", title="Glue", duration_s=270.0)
    m = find_match(cand, rows)
    assert m is not None and m.confident


def test_duration_mismatch_is_ambiguous():
    rows = [_rev("Bicep", "Glue", id=4, duration_s=200.0)]
    cand = Candidate(artist="Bicep", title="Glue", duration_s=420.0)  # extended? different cut
    m = find_match(cand, rows)
    assert m is not None and not m.confident and m.reason == "duration-mismatch"


def test_multiple_name_hits_is_ambiguous():
    rows = [
        _rev("Bicep", "Glue", id=5, duration_s=270.0),
        _rev("Bicep", "Glue", id=6, duration_s=271.0),
    ]
    cand = Candidate(artist="Bicep", title="Glue", duration_s=270.5)  # within tol of both
    m = find_match(cand, rows)
    assert m is not None and not m.confident and m.reason == "multiple-name"


def test_duration_disambiguates_two_same_name_rows():
    rows = [
        _rev("Bicep", "Glue", id=7, duration_s=200.0),
        _rev("Bicep", "Glue", id=8, duration_s=400.0),
    ]
    cand = Candidate(artist="Bicep", title="Glue", duration_s=201.0)  # only the first fits
    m = find_match(cand, rows)
    assert m is not None and m.confident and m.review.id == 7


def test_no_match_returns_none():
    rows = [_rev("Bicep", "Glue", id=9)]
    cand = Candidate(artist="Caribou", title="Odessa")
    assert find_match(cand, rows) is None


# --- Curator precedence guard (fakes, no DB) --------------------------------


class _Tags:
    isrc = ""
    artist = "Bicep"
    title = "Glue"


class _Feat:
    duration_s = 270.0


def test_drain_does_not_clobber_a_manual_label(monkeypatch):
    """A confident match on a track I already hand-tagged is left untouched."""
    monkeypatch.setattr(
        pending, "match",
        lambda **kw: MatchResult(_rev("Bicep", "Glue", id=1), confident=True, reason="isrc"),
    )
    applied = []
    monkeypatch.setattr(pending, "apply_review", lambda review, path: applied.append(review.id))
    monkeypatch.setattr(curator.store, "get_cards",
                        lambda paths: {paths[0]: {"taste_source": "manual"}})

    span = {"metadata": {}}
    curator._drain_pending("/music/glue.mp3", _Tags(), _Feat(), span)
    assert applied == []  # guarded: did not overwrite my own note


def test_drain_applies_confident_match_when_untagged(monkeypatch):
    monkeypatch.setattr(
        pending, "match",
        lambda **kw: MatchResult(_rev("Bicep", "Glue", id=1), confident=True, reason="isrc"),
    )
    applied = []
    monkeypatch.setattr(pending, "apply_review", lambda review, path: applied.append((review.id, path)))
    monkeypatch.setattr(curator.store, "get_cards",
                        lambda paths: {paths[0]: {"taste_source": None}})

    span = {"metadata": {}}
    curator._drain_pending("/music/glue.mp3", _Tags(), _Feat(), span)
    assert applied == [(1, "/music/glue.mp3")]
    assert span["metadata"]["applied_review"] == 1


def test_drain_parks_ambiguous_match(monkeypatch):
    monkeypatch.setattr(
        pending, "match",
        lambda **kw: MatchResult(_rev("Bicep", "Glue", id=2), confident=False, reason="multiple-name"),
    )
    applied = []
    monkeypatch.setattr(pending, "apply_review", lambda review, path: applied.append(review.id))
    # get_cards must not even be consulted for an ambiguous match
    monkeypatch.setattr(curator.store, "get_cards",
                        lambda paths: (_ for _ in ()).throw(AssertionError("should not query")))

    curator._drain_pending("/music/glue.mp3", _Tags(), _Feat(), {"metadata": {}})
    assert applied == []
