"""Tag parsing tests — pure logic, no audio files or mutagen needed."""

from dj.metadata import TrackTags, _dedupe, _norm, _split, read_tags


def test_split_normalizes_and_separates():
    assert _split("Deep House, Melodic; Dreamy") == ["deep house", "melodic", "dreamy"]
    assert _split("Techno/Driving") == ["techno", "driving"]
    assert _split("") == []


def test_norm_lowercases_and_strips():
    assert _norm("  Dreamy  ") == "dreamy"


def test_dedupe_preserves_order():
    assert _dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_merged_with_folds_in_extra_tags_deduped():
    base = TrackTags(title="X", tags=["deep house", "melodic"])
    merged = base.merged_with(["Dreamy", "deep house"])
    assert merged.tags == ["deep house", "melodic", "dreamy"]
    # original is untouched
    assert base.tags == ["deep house", "melodic"]


def test_read_tags_missing_file_is_graceful():
    # Nonexistent path → empty tags, never raises.
    t = read_tags("/no/such/file.mp3")
    assert isinstance(t, TrackTags)
    assert t.title == "" and t.tags == []
