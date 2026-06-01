from dj.audio import camelot


def test_to_camelot_known_keys():
    assert camelot.to_camelot(0, "major") == "8B"   # C major
    assert camelot.to_camelot(9, "minor") == "8A"   # A minor (relative)
    assert camelot.to_camelot(7, "major") == "9B"   # G major


def test_parse_roundtrip():
    assert camelot.parse("8B") == (8, "B")
    assert camelot.parse("12a") == (12, "A")


def test_compatible_rules():
    assert camelot.compatible("8A", "8A")    # identical
    assert camelot.compatible("8A", "8B")    # relative major/minor
    assert camelot.compatible("8A", "9A")    # +1 same letter
    assert camelot.compatible("8A", "7A")    # -1 same letter
    assert not camelot.compatible("8A", "11B")
    assert camelot.compatible("12B", "1B")   # wheel wraps 12 -> 1


def test_distance_orders_smoothness():
    assert camelot.distance("8A", "8A") == 0
    assert camelot.distance("8A", "8B") == 1
    assert camelot.distance("8A", "9A") == 1
    assert camelot.distance("8A", "11A") > camelot.distance("8A", "9A")
