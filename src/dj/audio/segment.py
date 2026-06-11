"""Structure-aware segmentation: a track → functional sections + a beat grid.

This is the prerequisite for two headline capabilities (ADR 0004 + 0005):
  - the Selector mixing *part* of a track (just the chorus + outro), because
    sections are first-class objects with their own vibe vector and energy, and
  - the Mixer cueing on *musical* boundaries (phrase-aligned via downbeats),
    because every section carries the downbeat index it starts on.

Detector strategy (ADR 0005): target **allin1** (one pass → beats, downbeats,
functional segment labels, tempo). If allin1 isn't installed or fails, fall back
to **librosa** beat tracking (octave-corrected) + accent-based downbeat *phase*
estimation + a self-similarity boundary detector + heuristic labels informed by
section recurrence. Every heavy import is lazy and wrapped, so the package
imports and the fast tests run with none of them present.

Two grid guarantees both detectors uphold (ADR 0011):
  - downbeats are the *estimated* bar firsts, not blindly `beats[::4]` — the
    fallback scores all four candidate phases by musical accent (onset strength,
    bass energy, harmonic change) and picks the one that behaves like a "1";
  - section bounds are snapped to the downbeat grid, so every cue point
    downstream (MIX IN/OUT marks, mixer splices) lands on a bar start.

The labeling/flagging *logic* (`label_sections`, `assign_mix_flags`,
`octave_correct`, `beats_to_downbeats`, `estimate_downbeat_phase`,
`snap_bounds_to_downbeats`, `section_recurrence`, `beat_accents`) is pure
numpy/Python — unit-tested with synthetic inputs, no audio files or models.
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

# allin1 (Harmonix) emits a few labels outside our vocabulary; map them onto the
# nearest functional equivalent instead of flattening everything to 'verse':
# 'start'/'end' are the lead-in/lead-out moments, 'inst' is an instrumental bed
# (the ideal blend zone → break: mixable + loopable), 'solo' is a featured
# passage you can ride out of (→ bridge: mixout). Known labels pass through.
ALLIN1_LABEL_MAP = {"start": "intro", "end": "outro", "inst": "break", "solo": "bridge"}


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


def beats_to_downbeats(
    beats: list[float], beats_per_bar: int = BEATS_PER_BAR, phase: int = 0
) -> list[float]:
    """Every Nth beat starting at `phase` — the bar firsts once the phase is known.

    `phase` comes from `estimate_downbeat_phase`; the old assume-beat-0-is-the-1
    behavior is just phase=0."""
    if beats_per_bar <= 0:
        return [float(b) for b in beats]
    return [float(b) for b in beats[phase % beats_per_bar:: beats_per_bar]]


def estimate_downbeat_phase(accents, beats_per_bar: int = BEATS_PER_BAR) -> int:
    """Which beat offset (0..beats_per_bar-1) is the bar "1"?

    `accents` is one musical-accent score per beat (see `beat_accents`). In 4/4
    dance music the "1" carries the kick, the bass re-entry, and the harmonic
    change, so the candidate phase whose beats average the most accent is the
    downbeat. This is what fixes phase-shifted grids (thinking beat 3 is the
    "1") that put every cue a beat or two off."""
    a = np.asarray(accents, dtype=float)
    if beats_per_bar <= 1 or a.size < beats_per_bar:
        return 0
    scores = [float(a[p::beats_per_bar].mean()) for p in range(beats_per_bar)]
    return int(np.argmax(scores))


def beat_accents(
    onset_env: np.ndarray,
    bass_env: np.ndarray,
    chroma: np.ndarray,
    beat_frames: np.ndarray,
) -> np.ndarray:
    """Per-beat accent strength — how much each beat "behaves like" a bar start.

    Three cues, each normalized 0..1 then summed, all frame-aligned arrays:
      - onset strength at the beat (the transient/kick),
      - low-frequency energy at the beat (the bass weight of a "1"),
      - harmonic change INTO the beat (chords move on bar boundaries): the
        distance between the mean chroma of the previous inter-beat span and
        this one.
    Pure numpy — callers supply the envelopes, so this is unit-testable."""
    frames = np.asarray(beat_frames, dtype=int)
    nb = len(frames)
    if nb == 0:
        return np.zeros(0)

    def _at(env: np.ndarray, f: int) -> float:
        if env.size == 0:
            return 0.0
        return float(env[min(int(f), env.size - 1)])

    onset = np.array([_at(onset_env, f) for f in frames])
    bass = np.array([_at(bass_env, f) for f in frames])

    flux = np.zeros(nb)
    if chroma.size and chroma.ndim == 2:
        n_frames = chroma.shape[1]
        spans = []
        for i in range(nb):
            a = min(int(frames[i]), n_frames - 1)
            b = int(frames[i + 1]) if i + 1 < nb else n_frames
            b = min(max(b, a + 1), n_frames)
            spans.append(chroma[:, a:b].mean(axis=1))
        for i in range(1, nb):
            flux[i] = float(np.linalg.norm(spans[i] - spans[i - 1]))
        if nb > 1:
            flux[0] = float(np.median(flux[1:]))  # no "previous span" for beat 0

    def _norm01(v: np.ndarray) -> np.ndarray:
        m = float(v.max()) if v.size else 0.0
        return v / m if m > 1e-9 else np.zeros_like(v)

    return _norm01(onset) + _norm01(bass) + _norm01(flux)


def snap_time_to_grid(t: float, grid: list[float], tolerance_s: float) -> float:
    """Nearest grid time within tolerance, else t unchanged."""
    if not grid:
        return float(t)
    g = min(grid, key=lambda d: abs(d - t))
    return float(g) if abs(g - t) <= tolerance_s else float(t)


def snap_bounds_to_downbeats(
    bounds: list[tuple[float, float]],
    downbeats: list[float],
    tolerance_s: float = 2.0,
) -> list[tuple[float, float]]:
    """Quantize contiguous section boundaries onto the downbeat grid.

    Interior edges move to the nearest downbeat within `tolerance_s`; the first
    start and last end stay put (track edges are track edges). Section bounds
    are what becomes every cue point downstream — snapping here is what makes a
    MIX IN marker land exactly on a "1" instead of at an arbitrary
    feature-clustering frame time. Edges that collapse onto the same downbeat
    merge their sections (then `merge_short_bounds` handles lengths).

    Callers pass a ~2-beat tolerance, half the bar spacing — i.e. inside the
    grid every edge IS within tolerance and quantization is deliberate policy
    (an off-grid cue is useless to a DJ even when the boundary estimate was
    mid-bar). The tolerance only stops edges beyond the first/last downbeat
    from teleporting onto a distant grid."""
    if not bounds or not downbeats:
        return [(float(a), float(b)) for a, b in bounds]
    start0, end_last = float(bounds[0][0]), float(bounds[-1][1])
    snapped = sorted(set(
        snap_time_to_grid(end, downbeats, tolerance_s) for _, end in bounds[:-1]
    ))
    edges = [start0, *[e for e in snapped if start0 < e < end_last], end_last]
    return [
        (float(edges[i]), float(edges[i + 1]))
        for i in range(len(edges) - 1)
        if edges[i + 1] - edges[i] > 1e-6
    ]


def snap_labeled_segments_to_grid(
    segments: list[tuple[float, float, str]],
    downbeats: list[float],
    tolerance_s: float,
) -> list[tuple[float, float, str]]:
    """Snap labeled (start, end, label) segments onto the grid, monotonically.

    The labeled-segment counterpart of `snap_bounds_to_downbeats` (which loses
    labels): the global first start / last end are pinned, every other edge
    snaps to its nearest downbeat within tolerance, a start is clamped to the
    previous snapped end so sections can never overlap, and a segment whose
    span collapses (both edges onto one downbeat) is dropped — its neighbors'
    snapped edges absorb it."""
    out: list[tuple[float, float, str]] = []
    prev_end: float | None = None
    last = len(segments) - 1
    for i, (start, end, label) in enumerate(segments):
        s = float(start) if i == 0 else snap_time_to_grid(start, downbeats, tolerance_s)
        e = float(end) if i == last else snap_time_to_grid(end, downbeats, tolerance_s)
        if prev_end is not None:
            s = max(s, prev_end)
        if e - s <= 1e-6:
            continue
        out.append((s, e, label))
        prev_end = e
    return out


def section_recurrence(features: list[np.ndarray]) -> list[float]:
    """How strongly each section's content recurs elsewhere in the track (0..1).

    Per section: max cosine similarity against every NON-adjacent section (the
    neighbor on each side is excluded — adjacent parts always resemble each
    other). A chorus/hook repeats; a bridge doesn't. Feeds `label_sections` so
    "chorus" can mean "this part comes back", not just "this part is loud"."""
    n = len(features)
    if n < 3:
        return [0.0] * n
    f = np.asarray(features, dtype=float)
    norms = np.linalg.norm(f, axis=1)
    norms[norms < 1e-9] = 1.0
    f = f / norms[:, None]
    sim = f @ f.T
    out = []
    for i in range(n):
        others = [sim[i, j] for j in range(n) if abs(i - j) > 1]
        out.append(float(np.clip(max(others), 0.0, 1.0)) if others else 0.0)
    return out


def nearest_beat_index(t: float, beats: list[float]) -> int | None:
    """Index of the beat closest to time t (the cue point's phrase anchor)."""
    if not beats:
        return None
    return int(np.argmin([abs(b - t) for b in beats]))


def label_sections(
    bounds: list[tuple[float, float]],
    energies: list[float],
    duration: float,
    recurrence: list[float] | None = None,
) -> list[str]:
    """Assign functional labels to ordered segments from energy, position, and
    (when available) how much each part RECURS elsewhere in the track.

    Heuristic, deterministic, and pure (ADR 0004 accepts approximate labels in
    v1). Shape: the first part is the `intro` — unless the track opens cold on
    the hook (near-peak energy), then it's a `chorus`; same for a hot ending vs
    the `outro`. The highest-energy interior part is the `drop`, parts that ramp
    *into* a peak are `build`s, deep low-energy interior parts are `break`s, and
    a part is a `chorus` if it's a secondary high point OR its content repeats
    elsewhere (`recurrence` from `section_recurrence` — hooks come back, bridges
    don't). Everything else stays `verse`.
    """
    n = len(bounds)
    if n == 0:
        return []
    if n == 1:
        return ["drop"]  # a whole track treated as one part: its own peak
    e = np.asarray(energies, dtype=float)
    rng = e.max() - e.min()
    norm = (e - e.min()) / rng if rng > 1e-9 else np.full(n, 0.5)
    rec = np.asarray(recurrence, dtype=float) \
        if recurrence is not None and len(recurrence) == n else np.zeros(n)

    labels = ["verse"] * n
    # Cold open / hot ending: a first (last) part at near-peak energy is the hook
    # playing, not an intro (outro) — mislabeling it would cue the mix into the
    # loudest bar of the track. Only meaningful with ≥4 parts: min-max norm
    # forces an endpoint to 1.0 on tiny section counts, which would make every
    # 2–3-part track read as a cold open or hot ending.
    labels[0] = "chorus" if n >= 4 and norm[0] > 0.75 else "intro"
    labels[-1] = "chorus" if n >= 4 and norm[-1] > 0.75 else "outro"

    interior = list(range(1, n - 1))
    if interior:
        peak = max(interior, key=lambda i: norm[i])
        labels[peak] = "drop"
        for i in interior:
            if i == peak:
                continue
            if i + 1 < n and norm[i + 1] - norm[i] > 0.25 and norm[i] < 0.6:
                labels[i] = "build"            # ramps up into a louder neighbor
            elif norm[i] > 0.7 or (rec[i] >= 0.6 and norm[i] >= 0.45):
                labels[i] = "chorus"           # a high point, or a part that recurs
            elif norm[i] < 0.3:
                labels[i] = "break"            # a deep dip
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

    Guarantee: every track keeps at least one entry and one exit point. A track
    that opens AND closes on the hook (cold open + hot ending) could otherwise
    end up with no mixable section at all — fall back to first-in/last-out,
    which is what a DJ does with such a record anyway.
    """
    for s in sections:
        s.is_mixin = s.label in ("intro", "break", "verse")
        s.is_mixout = s.label in ("outro", "break", "bridge")
        s.loopable = s.label in ("break", "intro")
    if sections:
        if not any(s.is_mixin for s in sections):
            sections[0].is_mixin = True
        if not any(s.is_mixout for s in sections):
            sections[-1].is_mixout = True


def build_sections(
    bounds: list[tuple[float, float]],
    energies: list[float],
    duration: float,
    beats: list[float],
    downbeats: list[float],
    bpm: float,
    recurrence: list[float] | None = None,
) -> list[Section]:
    """Assemble labeled, flagged Section rows with phrase-aligned beat anchors."""
    labels = label_sections(bounds, energies, duration, recurrence=recurrence)
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


def normalize_allin1_label(label: str) -> str:
    """allin1's Harmonix vocabulary → ours (unknowns degrade to 'verse')."""
    lab = ALLIN1_LABEL_MAP.get(str(label).lower(), str(label).lower())
    return lab if lab in LABELS else "verse"


def structure_from_allin1(
    bpm: float,
    beats: list[float],
    downbeats: list[float],
    raw_segments: list[tuple[float, float, str]],
    source: str = "allin1",
) -> Structure:
    """Build a Structure from allin1's outputs — shared by the in-process module
    path and the CLI bridge, and pure (unit-tested with synthetic inputs)."""
    if bpm <= 0 and len(downbeats) > 1:
        bpm = octave_correct(60.0 * BEATS_PER_BAR / np.median(np.diff(downbeats)))

    # allin1's segment edges are usually already musical, but not guaranteed to
    # sit on its own downbeat grid — snap within a 2-beat tolerance so the cue
    # points derived from these bounds land on a "1" (ADR 0011).
    tol = 2 * 60.0 / bpm if bpm > 0 else 1.0
    raw = [(float(s), float(e), normalize_allin1_label(lab)) for s, e, lab in raw_segments]
    sections: list[Section] = []
    for idx, (start, end, label) in enumerate(
        snap_labeled_segments_to_grid(raw, downbeats, tol)
    ):
        start_beat = nearest_beat_index(start, downbeats) if downbeats else None
        bars = int(round((end - start) * bpm / (60 * BEATS_PER_BAR))) if bpm > 0 else None
        sections.append(Section(idx, label, start, end, start_beat, bars or None))
    if not sections:
        raise RuntimeError("allin1 returned no segments")
    assign_mix_flags(sections)
    return Structure(bpm=bpm, beats=[float(b) for b in beats],
                     downbeats=[float(b) for b in downbeats], sections=sections,
                     source=source)


def allin1_json_to_structure(data: dict) -> Structure:
    """Parse one allin1 CLI JSON result (`<out_dir>/<stem>.json`) into a Structure."""
    raw = [
        (float(seg["start"]), float(seg["end"]), str(seg.get("label", "verse")))
        for seg in data.get("segments") or []
    ]
    return structure_from_allin1(
        bpm=float(data.get("bpm") or 0),
        beats=[float(b) for b in data.get("beats") or []],
        downbeats=[float(b) for b in data.get("downbeats") or []],
        raw_segments=raw,
        source="allin1-cli",
    )


def _segment_allin1(path: str) -> Structure:
    """One-pass detector: beats, downbeats, functional segments, tempo (ADR 0005).

    Two routes to the same model: the in-process package if it's importable, else
    the `allin1` CLI of a *different* Python install via subprocess (ADR 0012) —
    torch/NATTEN pins make the package hard to keep inside this venv."""
    try:
        import allin1  # lazy; the heavy/uncertain dep
    except ImportError:
        return _segment_allin1_cli(path)

    result = allin1.analyze(path)
    raw = [
        (float(seg.start), float(seg.end), str(getattr(seg, "label", "verse")))
        for seg in getattr(result, "segments", [])
    ]
    return structure_from_allin1(
        bpm=float(getattr(result, "bpm", 0) or 0),
        beats=[float(b) for b in getattr(result, "beats", [])],
        downbeats=[float(b) for b in getattr(result, "downbeats", [])],
        raw_segments=raw,
    )


def _segment_allin1_cli(
    path: str,
    *,
    bin: str | None = None,
    cache_dir: str | None = None,
    device: str | None = None,
) -> Structure:
    """Run the allin1 CLI (possibly from another Python install) and parse its JSON.

    Results cache under `cache_dir` keyed by file stem — allin1 itself skips
    tracks it has already analyzed, and we check first so a cached track never
    even spawns the subprocess (re-ingest stays cheap). Failures raise; the
    `segment()` wrapper degrades to the librosa fallback."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    from dj.config import settings

    bin = settings.allin1_bin if bin is None else bin
    if not bin:
        raise RuntimeError("allin1 CLI bridge disabled (DJ_ALLIN1_BIN is empty)")
    cache = Path(cache_dir or settings.allin1_cache_dir).expanduser()
    out_json = cache / (Path(path).stem + ".json")
    if not out_json.exists():
        exe = shutil.which(bin)
        if exe is None:
            raise RuntimeError(f"allin1 binary {bin!r} not found on PATH")
        cache.mkdir(parents=True, exist_ok=True)
        device = settings.allin1_device if device is None else device
        cmd = [exe, "--no-multiprocess", "-o", str(cache)]
        if device:
            cmd += ["-d", device]
        # Heavy: demucs + the transformer, minutes per track on CPU. Output is
        # captured so a curator batch log stays readable; check=True surfaces
        # failures to segment()'s fallback chain.
        subprocess.run(cmd + [path], check=True, capture_output=True, timeout=1800)
        if not out_json.exists():
            raise RuntimeError(f"allin1 ran but produced no JSON at {out_json}")
    return allin1_json_to_structure(json.loads(out_json.read_text()))


def _segment_librosa(path: str, sr: int) -> Structure:
    """Fallback: librosa beat grid + accent-estimated downbeat phase +
    self-similarity boundaries snapped to the grid + recurrence-aware labels."""
    import librosa

    y, sr = librosa.load(path, sr=sr, mono=True)
    duration = float(len(y) / sr)

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
    bpm = octave_correct(float(np.atleast_1d(tempo)[0]))
    beats = list(map(float, librosa.frames_to_time(beat_frames, sr=sr)))

    # Shared frame-aligned features (hop 512 everywhere): chroma+MFCC drive the
    # boundary detector AND the per-section recurrence; chroma + the bass band
    # also feed the downbeat-phase accents. Computed once.
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    feats = np.vstack([librosa.util.normalize(chroma, axis=1),
                       librosa.util.normalize(mfcc, axis=1)])
    freqs = librosa.fft_frequencies(sr=sr)
    # kick + bass weight per frame; collapse the STFT immediately — the full
    # magnitude matrix is tens of MB on a long track and only this row-sum lives on
    bass_env = np.abs(librosa.stft(y))[freqs < 150.0].sum(axis=0)

    # Real downbeats: score the 4 candidate phases by musical accent and take
    # the winner — not "every 4th beat from wherever beat_track started".
    accents = beat_accents(onset_env, bass_env, chroma, np.asarray(beat_frames))
    phase = estimate_downbeat_phase(accents)
    downbeats = beats_to_downbeats(beats, phase=phase)

    bounds = _boundaries_from_features(feats, sr, duration)
    tol = 2 * 60.0 / bpm if bpm > 0 else 2.0  # snap within 2 beats
    bounds = snap_bounds_to_downbeats(bounds, downbeats, tolerance_s=tol)
    bounds = merge_short_bounds(bounds)
    energies = [_segment_rms(y, sr, a, b) for a, b in bounds]
    recurrence = section_recurrence(_section_feature_means(feats, sr, bounds))
    sections = build_sections(bounds, energies, duration, beats, downbeats, bpm,
                              recurrence=recurrence)
    return Structure(bpm=bpm, beats=beats, downbeats=downbeats, sections=sections,
                     source="librosa")


def _boundaries_from_features(
    feats: np.ndarray, sr: int, duration: float
) -> list[tuple[float, float]]:
    """Structural boundaries via agglomerative clustering of a chroma+MFCC matrix.

    Pure librosa (no msaf dependency): `librosa.segment.agglomerative` cuts the
    frame-level feature matrix into ~k contiguous segments, where k scales with
    track length (~one section per 25 s, clamped).
    """
    import librosa

    k = int(np.clip(round(duration / 25.0), 3, 10))
    frame_bounds = librosa.segment.agglomerative(feats, k)
    times = list(map(float, librosa.frames_to_time(frame_bounds, sr=sr)))
    edges = [0.0, *[t for t in times if 0.0 < t < duration], duration]
    edges = sorted(set(round(e, 3) for e in edges))
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def _section_feature_means(
    feats: np.ndarray, sr: int, bounds: list[tuple[float, float]]
) -> list[np.ndarray]:
    """Mean feature column per section — the inputs to `section_recurrence`."""
    import librosa

    n_frames = feats.shape[1]
    out: list[np.ndarray] = []
    for start, end in bounds:
        fa = int(np.clip(librosa.time_to_frames(start, sr=sr), 0, n_frames - 1))
        fb = int(np.clip(librosa.time_to_frames(end, sr=sr), fa + 1, n_frames))
        out.append(feats[:, fa:fb].mean(axis=1))
    return out


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
