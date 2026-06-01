"""Audio analysis: a track file → TrackFeatures (BPM, key, energy, timbre).

librosa is imported lazily so the package imports fine without it (and tests
that don't touch audio stay fast). The real signal-processing lives in
`_features_from_signal`, which takes a raw waveform — so it's unit-testable with
a synthetic numpy signal, no audio files required.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from dj.audio import camelot

# Krumhansl-Schmuckler key profiles (correlate chroma against these).
_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)

ENERGY_CURVE_POINTS = 8  # downsample the RMS envelope to this many points


@dataclass
class TrackFeatures:
    path: str
    duration_s: float
    bpm: float
    pitch_class: int
    mode: str
    camelot: str
    energy_mean: float
    energy_curve: list[float]       # ENERGY_CURVE_POINTS, normalized 0..1
    spectral_centroid: float
    spectral_rolloff: float
    zero_crossing_rate: float
    mfcc: list[float] = field(default_factory=list)  # 13 coefficients


def analyze(path: str, sample_rate: int = 22050) -> TrackFeatures:
    """Load an audio file and extract features. Requires librosa + soundfile."""
    import librosa

    y, sr = librosa.load(path, sr=sample_rate, mono=True)
    feats = _features_from_signal(y, sr)
    feats.path = path
    return feats


def _features_from_signal(y: np.ndarray, sr: int) -> TrackFeatures:
    """Core feature extraction from a mono waveform (testable without files)."""
    import librosa

    duration_s = float(len(y) / sr)

    # --- tempo / BPM ---
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])

    # --- key → Camelot (Krumhansl correlation over 12 rotations) ---
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    pitch_class, mode = _estimate_key(chroma)
    code = camelot.to_camelot(pitch_class, mode)

    # --- energy envelope ---
    rms = librosa.feature.rms(y=y)[0]
    energy_mean = float(np.mean(rms))
    energy_curve = _downsample_normalized(rms, ENERGY_CURVE_POINTS)

    # --- timbre / brightness ---
    centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
    rolloff = float(np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr)))
    zcr = float(np.mean(librosa.feature.zero_crossing_rate(y=y)))
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13).mean(axis=1)

    return TrackFeatures(
        path="",
        duration_s=duration_s,
        bpm=bpm,
        pitch_class=pitch_class,
        mode=mode,
        camelot=code,
        energy_mean=energy_mean,
        energy_curve=energy_curve,
        spectral_centroid=centroid,
        spectral_rolloff=rolloff,
        zero_crossing_rate=zcr,
        mfcc=[float(x) for x in mfcc],
    )


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
    """Downsample to n points and scale to 0..1 (the shape of the energy arc)."""
    if len(arr) == 0:
        return [0.0] * n
    idx = np.linspace(0, len(arr) - 1, n).astype(int)
    pts = arr[idx].astype(float)
    lo, hi = float(pts.min()), float(pts.max())
    if hi - lo < 1e-9:
        return [0.5] * n
    return [float((p - lo) / (hi - lo)) for p in pts]
