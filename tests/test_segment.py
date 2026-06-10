"""Segmentation logic — pure heuristics, no audio files or detectors (ADR 0004/0005)."""


from dj.audio.segment import (
    assign_mix_flags,
    beats_to_downbeats,
    build_sections,
    label_sections,
    merge_short_bounds,
    nearest_beat_index,
    octave_correct,
)


def test_octave_correct_folds_into_dj_band():
    assert octave_correct(62) == 124.0       # half-tempo error doubled back
    assert octave_correct(240) == 120.0      # double-tempo error halved
    assert octave_correct(124) == 124.0      # already in band → unchanged
    assert octave_correct(0) == 0.0          # silence / no estimate


def test_octave_correct_preserves_fast_genres():
    assert octave_correct(174) == 174.0      # DnB is NOT folded to 87
    assert octave_correct(160) == 160.0      # footwork stays put
    assert octave_correct(350) == 175.0      # a true double of DnB still halves


def test_beats_to_downbeats_picks_every_fourth():
    beats = [float(i) for i in range(8)]
    assert beats_to_downbeats(beats, beats_per_bar=4) == [0.0, 4.0]


def test_nearest_beat_index():
    beats = [0.0, 1.0, 2.0, 3.0]
    assert nearest_beat_index(2.1, beats) == 2
    assert nearest_beat_index(0.4, beats) == 0
    assert nearest_beat_index(5.0, []) is None


def test_label_sections_endpoints_and_peak():
    bounds = [(0, 20), (20, 40), (40, 80), (80, 100)]
    energies = [0.1, 0.3, 0.9, 0.2]
    labels = label_sections(bounds, energies, 100)
    assert len(labels) == 4
    assert labels[0] == "intro"
    assert labels[-1] == "outro"
    assert "drop" in labels                   # the loud interior part is the peak


def test_label_sections_degenerate_counts():
    assert label_sections([], [], 0) == []
    assert label_sections([(0, 60)], [0.5], 60) == ["drop"]


def test_assign_mix_flags_from_labels():
    from dj.audio.segment import Section

    secs = [
        Section(0, "intro", 0, 10),
        Section(1, "break", 10, 20),
        Section(2, "drop", 20, 30),
        Section(3, "outro", 30, 40),
    ]
    assign_mix_flags(secs)
    assert secs[0].is_mixin and secs[0].loopable       # intro: clean entry, rideable
    assert secs[1].is_mixin and secs[1].is_mixout      # break: both + loopable
    assert not secs[2].is_mixin and not secs[2].is_mixout  # drop: never mix on the peak
    assert secs[3].is_mixout and not secs[3].is_mixin   # outro: clean exit only


def test_merge_short_bounds_absorbs_tiny_segments():
    bounds = [(0, 20), (20, 23), (23, 50)]   # the 3 s middle is too short to mix
    merged = merge_short_bounds(bounds, min_s=8)
    assert merged == [(0.0, 23.0), (23.0, 50.0)]


def test_merge_short_bounds_folds_a_tiny_first_segment_forward():
    bounds = [(0, 3), (3, 30), (30, 60)]     # the 3 s intro has no previous to absorb it
    merged = merge_short_bounds(bounds, min_s=8)
    assert merged == [(0.0, 30.0), (30.0, 60.0)]


def test_label_sections_can_emit_bridge_before_outro():
    bounds = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
    energies = [0.1, 0.9, 0.2, 0.4, 0.15]    # peak early; calm mid-energy pre-outro
    labels = label_sections(bounds, energies, 100)
    assert labels[0] == "intro" and labels[-1] == "outro"
    assert "bridge" in labels                # the pre-outro connector is now reachable
    # and a bridge is a clean exit point a DJ can mix out on
    from dj.audio.segment import Section
    secs = [Section(i, lbl, 0, 1) for i, lbl in enumerate(labels)]
    assign_mix_flags(secs)
    assert any(s.is_mixout for s in secs if s.label == "bridge")


def test_build_sections_sets_beat_anchor_and_bars():
    bounds = [(0, 16), (16, 48)]
    energies = [0.2, 0.8]
    downbeats = [i * 2.0 for i in range(40)]      # a downbeat every 2 s
    secs = build_sections(bounds, energies, 48, beats=[], downbeats=downbeats, bpm=120)
    assert [s.label for s in secs] == ["intro", "outro"]
    # 120 BPM, 4/4 → 2 s per bar; a 32 s section is 16 bars, anchored on downbeat 8.
    assert secs[1].bars == 16
    assert secs[1].start_beat == 8
