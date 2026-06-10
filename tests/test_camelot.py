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
    assert camelot.distance("8A", "3A") == 2          # +1-semitone energy boost, smooth


def test_energy_boost_detects_semitone_lift():
    assert camelot.energy_boost("8A", "3A")           # +1 semitone (up the wheel)
    assert camelot.energy_boost("8A", "1A")           # -1 semitone (8-7=1)
    assert not camelot.energy_boost("8A", "9A")       # that's a fifth, not a boost
    assert not camelot.energy_boost("8A", "3B")       # mode change → not a clean boost


def test_grade_tiers_moves():
    assert camelot.grade("8A", "8A") == 0             # identical
    assert camelot.grade("8A", "8B") == 0             # relative
    assert camelot.grade("8A", "9A") == 0             # fifth
    assert camelot.grade("8A", "3A") == 1             # energy boost
    assert camelot.grade("8A", "10A") == 2            # two-step
    assert camelot.grade("8A", "11B") == 3            # clash
    # the strict gate is exactly grade 0
    assert camelot.compatible("8A", "9A") == (camelot.grade("8A", "9A") == 0)
    assert not camelot.compatible("8A", "3A")         # boost is NOT in the strict gate
