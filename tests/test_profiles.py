"""Genre profiles: brief detection, name lookup, and genre-specific thresholds."""

from dj import profiles


def test_detect_picks_genre_from_brief_keywords():
    assert profiles.detect("warm dreamy house for a rooftop").name == "house"
    assert profiles.detect("dark warehouse techno marathon").name == "techno"
    assert profiles.detect("old school hip hop bbq").name == "hiphop"
    assert profiles.detect("perreo y reggaeton intenso").name == "latin"
    assert profiles.detect("liquid drum and bass morning").name == "dnb"
    assert profiles.detect("sunset dinner background").name == "downtempo"


def test_detect_order_resolves_overlapping_keywords():
    # 'afro house' must land on afro (most-specific-first), not house.
    assert profiles.detect("afro house golden hour").name == "afro"
    # 'melodic techno' hits techno before house's 'melodic'.
    assert profiles.detect("melodic techno journey").name == "techno"


def test_detect_falls_back_to_open():
    assert profiles.detect("songs that feel like rain on glass").name == "open"
    assert profiles.detect("").name == "open"


def test_get_by_name_case_insensitive_with_open_fallback():
    assert profiles.get("HipHop").name == "hiphop"
    assert profiles.get(" techno ").name == "techno"
    assert profiles.get(None).name == "open"
    assert profiles.get("polka").name == "open"


def test_thresholds_reflect_the_genre():
    hiphop = profiles.get("hiphop").thresholds()
    house = profiles.get("house").thresholds()
    # Hip-hop cuts tolerate big jumps and key clashes; house blends don't.
    assert hiphop.max_bpm_jump > house.max_bpm_jump
    assert hiphop.min_harmonic_compat < house.min_harmonic_compat


def test_mixing_physics_differ_by_genre():
    house, hiphop = profiles.get("house"), profiles.get("hiphop")
    assert house.max_xfade_bars > hiphop.max_xfade_bars   # long blends vs cuts
    assert house.max_stretch > hiphop.max_stretch         # vocals can't bend far
    assert hiphop.avg_slot_minutes < house.avg_slot_minutes
