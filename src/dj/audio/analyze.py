"""Audio analysis: a track file → TrackFeatures (the structured mixing data).

This produces the *hard mixing constraints* — BPM, key→Camelot, duration, and
the cross-track energy (LUFS) — that the Selector filters on and the Architect's
arc is scored against. The *semantic* "what does this feel like" representation
is handled separately by CLAP (vibe/clap.py), and the beat grid + section bounds
come from `audio/segment.py` (the detector), so this module deliberately does
NOT compute timbre features or its own structure.

Two calibration choices land here (ADR 0005):
  - **BPM is downbeat-derived.** The Curator passes the detector's tempo via
    `bpm=`; only when called standalone (or in tests) does this fall back to
    librosa `beat_track` with octave correction.
  - **Energy is cross-track-comparable LUFS** (`pyloudnorm`), not per-track
    min-max RMS — because "plan an energy arc across a set" is a cross-track
    comparison. A normalized 0..1 `energy_curve` is kept only for *display*.

librosa/pyloudnorm are imported lazily; the core `_features_from_signal` takes a
raw waveform, so it's unit-testable with a synthetic numpy signal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dj.audio import camelot
from dj.audio.segment import Section, octave_correct

# Krumhansl-Schmuckler key profiles (correlate chroma against these).
_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)

ENERGY_CURVE_POINTS = 8  # downsample the RMS envelope to this many points (display)
# A quiet floor returned when LUFS is undefined (silence) — well below any music.
_SILENCE_LUFS = -70.0


@dataclass
class TrackFeatures:
    """Structured mixing features. Semantics live in the CLAP vector, not here."""

    path: str
    duration_s: float
    bpm: float
    pitch_class: int
    mode: str
    camelot: str
    loudness_lufs: float            # integrated LUFS — cross-track comparable
    energy_curve: list[float]       # ENERGY_CURVE_POINTS, normalized 0..1 (display only)


def analyze(path: str, sample_rate: int = 22050, bpm: float | None = None) -> TrackFeatures:
    """Load an audio file and extract structured features. Needs librosa.

    `bpm`, when supplied by the Curator from the segment detector (ADR 0005), is
    used verbatim — the detector's downbeat-derived tempo beats a second librosa
    estimate. Omitted (standalone/tests) → librosa beat_track + octave correction.
    """
    import librosa

    y, sr = librosa.load(path, sr=sample_rate, mono=True)
    feats = _features_from_signal(y, sr, bpm=bpm)
    feats.path = path
    return feats


def measure_sections(
    path: str, sections: list[Section], sample_rate: int = 22050
) -> list[float]:
    """Short-term LUFS for each section — the cross-track-comparable per-part energy.

    Loads the waveform once and slices it per section (ADR 0005). Returned list is
    aligned to `sections`; the Curator copies each value onto `Section.energy_lufs`.
    """
    import librosa

    y, sr = librosa.load(path, sr=sample_rate, mono=True)
    out: list[float] = []
    for s in sections:
        seg = y[int(s.start_s * sr): int(s.end_s * sr)]
        out.append(short_term_lufs(seg, sr))
    return out


def _features_from_signal(y: np.ndarray, sr: int, bpm: float | None = None) -> TrackFeatures:
    """Core feature extraction from a mono waveform (testable without files)."""
    import librosa

    duration_s = float(len(y) / sr)

    # --- tempo / BPM (detector value if given, else corrected librosa estimate) ---
    if bpm is None:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = octave_correct(float(np.atleast_1d(tempo)[0]))

    # --- key → Camelot (Krumhansl correlation over 12 rotations) ---
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    pitch_class, mode = _estimate_key(chroma)
    code = camelot.to_camelot(pitch_class, mode)

    # --- energy: comparable LUFS (the real measure) + a normalized display arc ---
    loudness = integrated_lufs(y, sr)
    rms = librosa.feature.rms(y=y)[0]
    energy_curve = _downsample_normalized(rms, ENERGY_CURVE_POINTS)

    return TrackFeatures(
        path="",
        duration_s=duration_s,
        bpm=float(bpm),
        pitch_class=pitch_class,
        mode=mode,
        camelot=code,
        loudness_lufs=loudness,
        energy_curve=energy_curve,
    )


def integrated_lufs(y: np.ndarray, sr: int) -> float:
    """Integrated loudness in LUFS via pyloudnorm; RMS-dBFS fallback if absent.

    LUFS is the perceptual, cross-track loudness unit mastering/broadcast use, so
    "-18 warm-up, -9 peak" is a meaningful, schedulable target (ADR 0005). When
    pyloudnorm isn't installed we approximate with RMS in dBFS — same monotonic
    ordering across tracks, just not ITU-weighted.
    """
    y = np.asarray(y, dtype=np.float64)
    if y.size < int(sr * 0.4):  # < one 400 ms K-weighted block: no stable reading
        return _silence_or_rms(y)
    try:
        import pyloudnorm as pyln

        meter = pyln.Meter(sr)
        loudness = float(meter.integrated_loudness(y))
        return loudness if np.isfinite(loudness) else _silence_or_rms(y)
    except Exception:
        return _silence_or_rms(y)


def short_term_lufs(y: np.ndarray, sr: int) -> float:
    """Ungated short-term loudness for ONE section (ADR 0005 wants short-term here).

    Integrated loudness applies ITU *relative gating* — right for whole-program
    loudness, wrong for ranking a track's own sections, where a quiet break must
    read quiet next to the drop. We measure ~3 s windows independently and average
    them (ungated), so the result tracks the section's sustained level. Falls back
    to RMS-dBFS when pyloudnorm is absent or the span is too short for a block.
    """
    y = np.asarray(y, dtype=np.float64)
    if y.size < int(sr * 0.4):
        return _silence_or_rms(y)
    try:
        import pyloudnorm as pyln
    except Exception:
        return _silence_or_rms(y)
    meter = pyln.Meter(sr)
    win = int(3.0 * sr)
    if y.size <= win:
        val = float(meter.integrated_loudness(y))
        return val if np.isfinite(val) else _silence_or_rms(y)
    step = max(1, win // 2)
    vals = [float(meter.integrated_loudness(y[s:s + win])) for s in range(0, y.size - win + 1, step)]
    vals = [v for v in vals if np.isfinite(v)]
    return float(np.mean(vals)) if vals else _silence_or_rms(y)


def _silence_or_rms(y: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(y.astype(np.float64) ** 2))) if y.size else 0.0
    if rms <= 1e-9:
        return _SILENCE_LUFS
    return float(20.0 * np.log10(rms))


def _estimate_key(chroma: np.ndarray) -> tuple[int, str]:
    """Return (pitch_class, 'major'|'minor') best matching the chroma vector."""
    best = (-2.0, 0, "major")
    for pc in range(12):
        maj = np.corrcoef(np.roll(_MAJOR_PROFILE, pc), chroma)[0, 1]
        minr = np.corrcoef(np.roll(_MINOR_PROFILE, pc), chroma)[0, 1]
        if maj > best[0]:
            best = (maj, pc, "major")
        if minr > best[0]:
            best = (minr, pc, "minor")
    return best[1], best[2]


def _downsample_normalized(arr: np.ndarray, n: int) -> list[float]:
    """Downsample to n points and scale to 0..1 (the display shape of the arc)."""
    if len(arr) == 0:
        return [0.0] * n
    idx = np.linspace(0, len(arr) - 1, n).astype(int)
    pts = arr[idx].astype(float)
    lo, hi = float(pts.min()), float(pts.max())
    if hi - lo < 1e-9:
        return [0.5] * n
    return [float((p - lo) / (hi - lo)) for p in pts]
