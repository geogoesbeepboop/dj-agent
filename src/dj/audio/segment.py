"""Structure-aware segmentation: a track → functional sections + a beat grid.

This is the prerequisite for two headline capabilities (ADR 0004 + 0005):
  - the Selector mixing *part* of a track (just the chorus + outro), because
    sections are first-class objects with their own vibe vector and energy, and
  - the Mixer cueing on *musical* boundaries (phrase-aligned via downbeats),
    because every section carries the downbeat index it starts on.

Detector strategy (ADR 0005): target **allin1** (one pass → beats, downbeats,
functional segment labels, tempo). If allin1 isn't installed or fails, fall back
to **librosa** beat tracking (octave-corrected) + a self-similarity boundary
detector + heuristic labels. Every heavy import is lazy and wrapped, so the
package imports and the fast tests run with none of them present.

The labeling/flagging *logic* (`label_sections`, `assign_mix_flags`,
`octave_correct`, `beats_to_downbeats`) is pure numpy/Python — unit-tested with
synthetic inputs, no audio files or models.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Functional section vocabulary (ADR 0004). v1 labels are heuristic; a dedicated
# model can refine chorus-vs-drop later. Order is intentional: roughly low → high
# → resolve, which `label_sections` leans on.
LABELS = ("intro", "verse", "build", "chorus", "drop", "break", "bridge", "outro")

# Sections shorter than this are too brief to mix on; merged into a neighbor.
_MIN_SECTION_S = 8.0
# Assume 4/4 unless the detector says otherwise — true for nearly all DJ material.
BEATS_PER_BAR = 4


@dataclass
class Section:
    """One functional part of a track — a first-class, mixable object."""

    idx: int
    label: str
    start_s: float
    end_s: float
    start_beat: int | None = None        # downbeat index → phrase-aligned cueing
    bars: int | None = None
    energy_lufs: float | None = None     # filled by analyze.measure_sections
    camelot: str | None = None           # local key if it differs from the track
    is_mixin: bool = False               # clean entry point for the *next* track
    is_mixout: bool = False              # clean exit point out of *this* track
    loopable: bool = False

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


@dataclass
class Structure:
    """Everything the detector knows about a track's time grid + form."""

    bpm: float
    beats: list[float] = field(default_factory=list)      # beat onset times (s)
    downbeats: list[float] = field(default_factory=list)  # bar onset times (s)
    sections: list[Section] = field(default_factory=list)
    source: str = "unknown"  # 'allin1' | 'librosa' — provenance for debugging


def segment(path: str, sample_rate: int = 22050) -> Structure:
    """Detect a track's beat grid + functional sections. Never raises on a bad
    detector — it degrades to the librosa fallback, then to a single section."""
    try:
        return _segment_allin1(path)
    except Exception:
        pass
    try:
        return _segment_librosa(path, sample_rate)
    except Exception as exc:  # last resort: one section spanning the whole file
        return _single_section(path, sample_rate, reason=str(exc))


# --- pure logic (unit-tested without audio) ---------------------------------


def octave_correct(bpm: float, lo: float = 84.0, hi: float = 184.0) -> float:
    """Fold a tempo estimate into a sane DJ band, fixing librosa's octave errors.

    librosa's `beat_track` frequently reports half/double tempo (62 for 124, 70
    for 140). Folding into [lo, hi) recovers the tempo DJs actually count. The band
    runs to 184 so genuinely fast genres (DnB/jungle ~170–180, footwork ~160) are
    NOT mistakenly halved — only true octave errors below 84 or at/above 184 fold.
    """
    if bpm <= 0:
        return 0.0
    while bpm < lo:
        bpm *= 2
    while bpm >= hi:
        bpm /= 2
    return float(bpm)


def beats_to_downbeats(beats: list[float], beats_per_bar: int = BEATS_PER_BAR) -> list[float]:
    """Pick every Nth beat as a downbeat — the 4/4 fallback when the detector
    gives beats but not bar firsts."""
    return [float(b) for b in beats[::beats_per_bar]]


def nearest_beat_index(t: float, beats: list[float]) -> int | None:
    """Index of the beat closest to time t (the cue point's phrase anchor)."""
    if not beats:
        return None
    return int(np.argmin([abs(b - t) for b in beats]))


def label_sections(
    bounds: list[tuple[float, float]], energies: list[float], duration: float
) -> list[str]:
    """Assign functional labels to ordered segments from energy + position.

    Heuristic, deterministic, and pure (ADR 0004 accepts approximate labels in
    v1). Shape: the first part is the `intro`, the last is the `outro`, the
    highest-energy interior part is the `drop`, parts that ramp *into* a peak are
    `build`s, deep low-energy interior parts are `break`s, and everything else
    alternates `verse`/`chorus`.
    """
    n = len(bounds)
    if n == 0:
        return []
    if n == 1:
        return ["drop"]  # a whole track treated as one part: its own peak
    e = np.asarray(energies, dtype=float)
    rng = e.max() - e.min()
    norm = (e - e.min()) / rng if rng > 1e-9 else np.full(n, 0.5)

    labels = ["verse"] * n
    labels[0] = "intro"
    labels[-1] = "outro"

    interior = list(range(1, n - 1))
    if interior:
        peak = max(interior, key=lambda i: norm[i])
        labels[peak] = "drop"
        for i in interior:
            if i == peak:
                continue
            if i + 1 < n and norm[i + 1] - norm[i] > 0.25 and norm[i] < 0.6:
                labels[i] = "build"            # ramps up into a louder neighbor
            elif norm[i] < 0.3:
                labels[i] = "break"            # a deep dip
            elif norm[i] > 0.7:
                labels[i] = "chorus"           # a secondary high point
        # The 'bridge': the calm mid-energy connector right before the outro —
        # the classic exit ramp a DJ mixes out on (assign_mix_flags marks it
        # is_mixout). Without this the librosa fallback could never emit 'bridge'.
        pre_outro = n - 2
        if pre_outro >= 1 and labels[pre_outro] == "verse" and norm[pre_outro] < 0.6:
            labels[pre_outro] = "bridge"
    return labels


def assign_mix_flags(sections: list[Section]) -> None:
    """Set is_mixin / is_mixout / loopable in place from each section's label.

    Entry points (mix the *next* track in here): intros, breaks, verses — clean,
    low-clutter spots. Exit points (mix *out* here): outros, breaks, bridges.
    Loopable: breaks and intros, the steady low-energy beds a DJ can ride.
    """
    for s in sections:
        s.is_mixin = s.label in ("intro", "break", "verse")
        s.is_mixout = s.label in ("outro", "break", "bridge")
        s.loopable = s.label in ("break", "intro")


def build_sections(
    bounds: list[tuple[float, float]],
    energies: list[float],
    duration: float,
    beats: list[float],
    downbeats: list[float],
    bpm: float,
) -> list[Section]:
    """Assemble labeled, flagged Section rows with phrase-aligned beat anchors."""
    labels = label_sections(bounds, energies, duration)
    sections: list[Section] = []
    for idx, ((start, end), label) in enumerate(zip(bounds, labels)):
        start_beat = nearest_beat_index(start, downbeats) if downbeats else None
        bars = int(round((end - start) * bpm / (60 * BEATS_PER_BAR))) if bpm > 0 else None
        sections.append(
            Section(idx=idx, label=label, start_s=float(start), end_s=float(end),
                    start_beat=start_beat, bars=bars or None)
        )
    assign_mix_flags(sections)
    return sections


def merge_short_bounds(
    bounds: list[tuple[float, float]], min_s: float = _MIN_SECTION_S
) -> list[tuple[float, float]]:
    """Fold sub-`min_s` segments into the previous one — nothing too short to mix."""
    if not bounds:
        return []
    merged = [list(bounds[0])]
    for start, end in bounds[1:]:
        if end - start < min_s:
            merged[-1][1] = end           # absorb into the previous span
        else:
            merged.append([start, end])
    # A too-short FIRST span has no previous to absorb it — fold it forward into
    # the next span instead (otherwise a tiny intro survives as its own section).
    if len(merged) > 1 and merged[0][1] - merged[0][0] < min_s:
        merged[1][0] = merged[0][0]
        merged.pop(0)
    return [(float(a), float(b)) for a, b in merged]


# --- detectors (lazy, audio-dependent) --------------------------------------


def _segment_allin1(path: str) -> Structure:
    """One-pass detector: beats, downbeats, functional segments, tempo (ADR 0005)."""
    import allin1  # lazy; the heavy/uncertain dep — absence triggers the fallback

    result = allin1.analyze(path)
    beats = [float(b) for b in getattr(result, "beats", [])]
    downbeats = [float(b) for b in getattr(result, "downbeats", [])]
    bpm = float(getattr(result, "bpm", 0) or 0)
    if bpm <= 0 and len(downbeats) > 1:
        bpm = octave_correct(60.0 * BEATS_PER_BAR / np.median(np.diff(downbeats)))

    sections: list[Section] = []
    for idx, seg in enumerate(getattr(result, "segments", [])):
        start, end = float(seg.start), float(seg.end)
        label = str(getattr(seg, "label", "verse")).lower()
        if label not in LABELS:
            label = "verse"
        start_beat = nearest_beat_index(start, downbeats) if downbeats else None
        bars = int(round((end - start) * bpm / (60 * BEATS_PER_BAR))) if bpm > 0 else None
        sections.append(Section(idx, label, start, end, start_beat, bars or None))
    if not sections:
        raise RuntimeError("allin1 returned no segments")
    assign_mix_flags(sections)
    return Structure(bpm=bpm, beats=beats, downbeats=downbeats, sections=sections,
                     source="allin1")


def _segment_librosa(path: str, sr: int) -> Structure:
    """Fallback: librosa beat grid + self-similarity boundaries + heuristic labels."""
    import librosa

    y, sr = librosa.load(path, sr=sr, mono=True)
    duration = float(len(y) / sr)

    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    bpm = octave_correct(float(np.atleast_1d(tempo)[0]))
    beats = list(map(float, librosa.frames_to_time(beat_frames, sr=sr)))
    downbeats = beats_to_downbeats(beats)

    bounds = _boundaries_librosa(y, sr, duration)
    bounds = merge_short_bounds(bounds)
    energies = [_segment_rms(y, sr, a, b) for a, b in bounds]
    sections = build_sections(bounds, energies, duration, beats, downbeats, bpm)
    return Structure(bpm=bpm, beats=beats, downbeats=downbeats, sections=sections,
                     source="librosa")


def _boundaries_librosa(y: np.ndarray, sr: int, duration: float) -> list[tuple[float, float]]:
    """Detect structural boundaries via agglomerative clustering of chroma+MFCC.

    Pure librosa (no msaf dependency): build a beat-synchronous feature matrix
    and let `librosa.segment.agglomerative` cut it into ~k contiguous segments,
    where k scales with track length (~one section per 25 s, clamped).
    """
    import librosa

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    feats = np.vstack([librosa.util.normalize(chroma, axis=1),
                       librosa.util.normalize(mfcc, axis=1)])
    k = int(np.clip(round(duration / 25.0), 3, 10))
    frame_bounds = librosa.segment.agglomerative(feats, k)
    times = list(map(float, librosa.frames_to_time(frame_bounds, sr=sr)))
    edges = [0.0, *[t for t in times if 0.0 < t < duration], duration]
    edges = sorted(set(round(e, 3) for e in edges))
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def _segment_rms(y: np.ndarray, sr: int, start_s: float, end_s: float) -> float:
    """Mean RMS over a span — the energy `label_sections` ranks parts by."""
    seg = y[int(start_s * sr): int(end_s * sr)]
    if len(seg) == 0:
        return 0.0
    return float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))


def _single_section(path: str, sr: int, reason: str) -> Structure:
    """Degenerate fallback: the whole file as one section (detector unavailable)."""
    import librosa

    duration = float(librosa.get_duration(path=path))
    sec = Section(0, "drop", 0.0, duration, start_beat=0, bars=None)
    assign_mix_flags([sec])
    # Keep WHY we fell all the way through in the provenance — the Curator copies
    # structure.source onto the trace span, so a bad file is debuggable post-hoc.
    source = f"single ({reason[:60]})" if reason else "single"
    return Structure(bpm=0.0, beats=[], downbeats=[0.0], sections=[sec], source=source)
