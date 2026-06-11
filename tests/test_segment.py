"""Segmentation logic — pure heuristics, no audio files or detectors (ADR 0004/0005/0011)."""

import numpy as np

from dj.audio.segment import (
    Section,
    assign_mix_flags,
    beat_accents,
    beats_to_downbeats,
    build_sections,
    estimate_downbeat_phase,
    label_sections,
    merge_short_bounds,
    nearest_beat_index,
    octave_correct,
    section_recurrence,
    snap_bounds_to_downbeats,
    snap_labeled_segments_to_grid,
    snap_time_to_grid,
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


def test_beats_to_downbeats_respects_phase():
    beats = [float(i) for i in range(10)]
    assert beats_to_downbeats(beats, beats_per_bar=4, phase=2) == [2.0, 6.0]
    assert beats_to_downbeats(beats, beats_per_bar=4, phase=6) == [2.0, 6.0]  # mod bpb


def test_estimate_downbeat_phase_finds_the_accented_beat():
    # 4 bars of 4 beats; the true "1" is at offset 2 (a phase-shifted grid).
    accents = np.zeros(16)
    accents[2::4] = 1.0
    assert estimate_downbeat_phase(accents) == 2
    # phase 0 wins ties / too-short input
    assert estimate_downbeat_phase(np.ones(16)) == 0
    assert estimate_downbeat_phase(np.array([1.0, 2.0]), beats_per_bar=4) == 0


def test_estimate_downbeat_phase_survives_noisy_accents():
    rng = np.random.default_rng(7)
    accents = rng.uniform(0.0, 0.4, 64)
    accents[1::4] += 1.0                     # consistent accent on offset 1
    assert estimate_downbeat_phase(accents) == 1


def test_beat_accents_combines_onset_bass_and_harmonic_change():
    # 8 beats, one frame apart. Onset+bass peak on frames 2 and 6; the chroma
    # changes on those same beats — so accents at beats 2 and 6 dominate.
    beat_frames = np.arange(8)
    onset = np.full(8, 0.1)
    onset[[2, 6]] = 1.0
    bass = np.full(8, 0.1)
    bass[[2, 6]] = 1.0
    chroma = np.zeros((12, 8))
    chroma[0, :2] = 1.0
    chroma[5, 2:6] = 1.0                    # chord change INTO beat 2
    chroma[9, 6:] = 1.0                     # and into beat 6
    acc = beat_accents(onset, bass, chroma, beat_frames)
    assert len(acc) == 8
    assert acc[2] == max(acc) and acc[6] > acc[3]
    assert estimate_downbeat_phase(acc) == 2


def test_beat_accents_degenerate_inputs():
    assert len(beat_accents(np.zeros(0), np.zeros(0), np.zeros((12, 0)), np.zeros(0))) == 0
    # constant envelopes → all-equal accents, never NaN
    acc = beat_accents(np.ones(8), np.ones(8), np.ones((12, 8)), np.arange(8))
    assert np.all(np.isfinite(acc))


def test_snap_time_to_grid_respects_tolerance():
    grid = [0.0, 2.0, 4.0]
    assert snap_time_to_grid(2.3, grid, tolerance_s=0.5) == 2.0
    assert snap_time_to_grid(3.0, grid, tolerance_s=0.5) == 3.0   # too far → unchanged
    assert snap_time_to_grid(1.0, [], tolerance_s=0.5) == 1.0


def test_snap_bounds_to_downbeats_quantizes_interior_edges_only():
    downbeats = [0.0, 2.0, 4.0, 6.0, 8.0]
    bounds = [(0.0, 2.3), (2.3, 5.4), (5.4, 9.1)]
    snapped = snap_bounds_to_downbeats(bounds, downbeats, tolerance_s=1.0)
    # interior edges 2.3→2.0 and 5.4→6.0; the track edges 0.0/9.1 stay put
    assert snapped == [(0.0, 2.0), (2.0, 6.0), (6.0, 9.1)]


def test_snap_bounds_merges_edges_that_collapse_onto_one_downbeat():
    downbeats = [0.0, 4.0, 8.0]
    bounds = [(0.0, 3.6), (3.6, 4.4), (4.4, 8.0)]   # both interior edges → 4.0
    snapped = snap_bounds_to_downbeats(bounds, downbeats, tolerance_s=1.0)
    assert snapped == [(0.0, 4.0), (4.0, 8.0)]      # tiny middle absorbed, no 0-length


def test_snap_labeled_segments_never_overlap_and_drop_collapsed():
    # Middle segment collapses onto downbeat 12; its neighbors absorb it — the
    # naive per-segment snap would keep B at (12.0, 12.5) UNDER C's snapped start.
    downbeats = [10.0, 12.0, 14.0]
    segs = [(8.0, 12.1, "intro"), (12.1, 12.5, "break"), (12.5, 20.0, "outro")]
    out = snap_labeled_segments_to_grid(segs, downbeats, tolerance_s=1.0)
    assert out == [(8.0, 12.0, "intro"), (12.0, 20.0, "outro")]
    # contiguous, monotonic, labels preserved on survivors
    assert all(a < b for a, b, _ in out)


def test_snap_labeled_segments_pins_track_edges():
    downbeats = [0.9, 4.9, 8.9]
    segs = [(0.0, 5.2, "intro"), (5.2, 9.4, "outro")]
    out = snap_labeled_segments_to_grid(segs, downbeats, tolerance_s=1.0)
    # first start and last end untouched; the shared interior edge snapped
    assert out == [(0.0, 4.9, "intro"), (4.9, 9.4, "outro")]


def test_section_recurrence_scores_repeats_high_and_unique_low():
    a = np.array([1.0, 0.0, 0.0])
    b = np.array([0.0, 1.0, 0.0])
    c = np.array([0.0, 0.0, 1.0])
    # layout a b a c b: both a's and b's recur non-adjacently; c never does
    rec = section_recurrence([a, b, a, c, b])
    assert rec[0] > 0.99 and rec[2] > 0.99          # a ↔ a
    assert rec[1] > 0.99 and rec[4] > 0.99          # b ↔ b
    assert rec[3] < 0.01                            # c is unique
    # fewer than 3 sections → no non-adjacent pairs → all zero
    assert section_recurrence([a, b]) == [0.0, 0.0]


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


def test_label_sections_cold_open_is_not_an_intro():
    # The track opens ON the hook at near-peak energy — calling that "intro"
    # (a clean mix-in point) would cue the next mix into the loudest bar.
    bounds = [(0, 25), (25, 50), (50, 75), (75, 100)]
    energies = [0.95, 0.3, 0.9, 0.2]
    labels = label_sections(bounds, energies, 100)
    assert labels[0] == "chorus"
    assert labels[-1] == "outro"             # the quiet ending is still an outro


def test_label_sections_hot_ending_is_not_an_outro():
    bounds = [(0, 25), (25, 50), (50, 75), (75, 100)]
    energies = [0.1, 0.5, 0.4, 0.95]         # ends loud (radio edit cuts on the hook)
    labels = label_sections(bounds, energies, 100)
    assert labels[0] == "intro"
    assert labels[-1] == "chorus"


def test_label_sections_recurring_section_becomes_chorus():
    bounds = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
    energies = [0.1, 0.6, 0.9, 0.6, 0.1]     # mid-energy parts, neither loud nor dips
    # without recurrence the mid-energy interior parts stay verses
    plain = label_sections(bounds, energies, 100)
    assert plain[1] == "verse"
    # but if part 1 and part 3 are the same material, they're the chorus
    rec = [0.0, 0.9, 0.0, 0.9, 0.0]
    labeled = label_sections(bounds, energies, 100, recurrence=rec)
    assert labeled[1] == "chorus" and labeled[3] == "chorus"


def test_assign_mix_flags_guarantees_an_entry_and_exit():
    # A cold-open + hot-ending record: all hooks, no natural mix points.
    secs = [Section(0, "chorus", 0, 30), Section(1, "drop", 30, 60),
            Section(2, "chorus", 60, 90)]
    assign_mix_flags(secs)
    assert secs[0].is_mixin                  # fall back to first-in…
    assert secs[-1].is_mixout                # …last-out, like a DJ would
    assert not secs[1].is_mixin and not secs[1].is_mixout  # never on the drop


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


# --- allin1 bridge (ADR 0012): vocabulary mapping + structure building --------


def test_normalize_allin1_label_maps_harmonix_vocabulary():
    from dj.audio.segment import normalize_allin1_label

    assert normalize_allin1_label("start") == "intro"
    assert normalize_allin1_label("end") == "outro"
    assert normalize_allin1_label("inst") == "break"     # instrumental bed → mixable
    assert normalize_allin1_label("solo") == "bridge"
    assert normalize_allin1_label("Chorus") == "chorus"  # case-insensitive passthrough
    assert normalize_allin1_label("???") == "verse"      # unknowns degrade safely


def test_structure_from_allin1_snaps_derives_bpm_and_flags():
    from dj.audio.segment import structure_from_allin1

    beats = [i * 0.5 for i in range(80)]                 # 120 BPM, 40 s
    downbeats = [i * 2.0 for i in range(20)]             # a bar every 2 s
    raw = [(0.0, 8.3, "start"), (8.3, 24.2, "verse"), (24.2, 39.5, "chorus")]
    s = structure_from_allin1(0.0, beats, downbeats, raw)

    assert s.bpm == 120.0                                # derived from the downbeat grid
    assert s.source == "allin1"
    assert [sec.label for sec in s.sections] == ["intro", "verse", "chorus"]
    # interior bounds snapped onto downbeats (8.3 → 8.0, 24.2 → 24.0)
    assert s.sections[1].start_s == 8.0 and s.sections[2].start_s == 24.0
    assert s.sections[0].is_mixin                        # flags assigned
    assert s.sections[-1].is_mixout                      # hot-ending fallback


def test_allin1_json_to_structure_parses_the_cli_format():
    from dj.audio.segment import allin1_json_to_structure

    data = {
        "path": "/m/track.flac", "bpm": 124,
        "beats": [0.1, 0.58, 1.07, 1.55], "downbeats": [0.1, 2.03],
        "beat_positions": [1, 2, 3, 4],                  # present in real files; ignored
        "segments": [{"start": 0.1, "end": 2.0, "label": "intro"},
                     {"start": 2.0, "end": 30.0, "label": "chorus"}],
    }
    s = allin1_json_to_structure(data)
    assert s.bpm == 124.0 and s.source == "allin1-cli"
    assert [sec.label for sec in s.sections] == ["intro", "chorus"]


def test_segment_allin1_cli_uses_cached_json_without_running(tmp_path):
    import json

    from dj.audio.segment import _segment_allin1_cli

    cached = {
        "bpm": 100, "beats": [0.0, 0.6], "downbeats": [0.0, 2.4],
        "segments": [{"start": 0.0, "end": 30.0, "label": "verse"}],
    }
    (tmp_path / "My Track.json").write_text(json.dumps(cached))
    # bin doesn't exist — proves the cache short-circuits the subprocess entirely
    s = _segment_allin1_cli("/music/My Track.flac",
                            bin="definitely-not-a-binary", cache_dir=str(tmp_path))
    assert s.bpm == 100.0 and len(s.sections) == 1


def test_segment_allin1_cli_raises_cleanly_when_unavailable(tmp_path):
    import pytest

    from dj.audio.segment import _segment_allin1_cli

    with pytest.raises(RuntimeError, match="not found"):
        _segment_allin1_cli("/music/x.flac", bin="definitely-not-a-binary",
                            cache_dir=str(tmp_path / "empty"))
    with pytest.raises(RuntimeError, match="disabled"):
        _segment_allin1_cli("/music/x.flac", bin="", cache_dir=str(tmp_path))
