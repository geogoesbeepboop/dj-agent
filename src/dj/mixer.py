"""The Mixer (Phase 4 — the wow): render a SetPlan into a continuous beatmatched mix.

The payoff of everything upstream: because the Selector chose *sections* with
downbeat-anchored cue points (ADR 0004), the Mixer can cue on **musical
boundaries** — "mix out of A's outro into B's chorus" is literal, not an
arbitrary offset. Per transition it:
  - time-stretches the incoming track to the arc's target tempo (`pyrubberband`),
    so the set rides the planned BPM curve,
  - overlaps on a **phrase-derived** crossfade length (bars from the section grid,
    not a fixed wall-clock time — ADR 0007), and
  - blends equal-power, with an optional bass/EQ swap so the two basslines don't
    clash through the overlap.

The transition *planning* (`plan_transitions`, `crossfade_seconds`,
`stretch_ratio`, `section_bars`) is pure and unit-tested. Audio rendering imports
librosa/soundfile/pyrubberband lazily and degrades gracefully if a stretch/EQ dep
is missing (it just concatenates at native tempo with an equal-power crossfade).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from dj.config import settings
from dj.plan import SetPlan, Slot

WORKING_SR = 44100
BEATS_PER_BAR = 4
MAX_XFADE_BARS = 8            # cap the overlap so a transition never drags (ADR 0007)
_TARGET_PEAK = 0.97          # normalize the finished mix to this ceiling
_NOMINAL_SLOT_S = 210.0      # assumed airtime for a slot with no cue length (~3.5 min)


@dataclass
class Transition:
    """One A→B mix: where to cross, how long, and the tempo to beatmatch B to."""

    from_idx: int
    to_idx: int
    from_path: str
    to_path: str
    crossfade_s: float
    target_bpm: float


# --- pure planning (unit-tested; no audio) ----------------------------------


def section_bars(length_s: float | None, bpm: float) -> int | None:
    """How many bars a span of audio is, at this tempo (4/4). None if unknown."""
    if not length_s or bpm <= 0:
        return None
    return max(1, int(round(length_s * bpm / (60 * BEATS_PER_BAR))))


def crossfade_bars(out_section_len_s: float | None, bpm: float, max_bars: int = MAX_XFADE_BARS) -> int:
    """Phrase-derived overlap length: ~half the outgoing section, capped (ADR 0007).

    A fixed 8-bar crossfade swallows a 4-bar outro and underuses a 32-bar one.
    Deriving it from the section grid keeps the blend inside one phrase."""
    bars = section_bars(out_section_len_s, bpm)
    if bars is None:
        return max(1, min(max_bars, 4))
    return max(1, min(max_bars, bars // 2 or 1))


def crossfade_seconds(bpm: float, bars: int) -> float:
    """Seconds occupied by `bars` bars at `bpm` (4/4) — the overlap duration."""
    if bpm <= 0:
        return 0.0
    return float(bars * BEATS_PER_BAR * 60.0 / bpm)


def stretch_ratio(src_bpm: float, dst_bpm: float) -> float:
    """pyrubberband time-stretch rate to beatmatch src → dst (>1 = play faster)."""
    if src_bpm <= 0 or dst_bpm <= 0:
        return 1.0
    return float(dst_bpm / src_bpm)


def plan_transitions(plan: SetPlan) -> list[Transition]:
    """Plan every adjacent A→B mix from the slots' sections + the arc tempo."""
    transitions: list[Transition] = []
    for i, (a, b) in enumerate(zip(plan.slots, plan.slots[1:])):
        a_len = _slot_length_s(a)
        bars = crossfade_bars(a_len, a.bpm)
        target_bpm = plan.arc.target_at(b.position).bpm or b.bpm
        transitions.append(Transition(
            from_idx=i, to_idx=i + 1, from_path=a.path, to_path=b.path,
            crossfade_s=crossfade_seconds(target_bpm, bars), target_bpm=target_bpm,
        ))
    return transitions


def _slot_length_s(slot: Slot) -> float | None:
    if slot.cue_start_s is not None and slot.cue_end_s is not None:
        return max(0.0, slot.cue_end_s - slot.cue_start_s)
    return None


def set_duration_seconds(plan: SetPlan) -> float:
    """Estimated runtime of the rendered mix: played section lengths minus overlaps.

    Lets the HITL gate show 'estimated length' so a set built to fill `minutes`
    can be sanity-checked before render (a slot with no cue length is assumed to
    air for ~3.5 min)."""
    overlap = sum(t.crossfade_s for t in plan_transitions(plan))
    played = sum((_slot_length_s(s) or _NOMINAL_SLOT_S) for s in plan.slots)
    return max(0.0, played - overlap)


# --- audio rendering (lazy deps; degrades gracefully) -----------------------


def render_set(plan: SetPlan, out_path: str | None = None, sr: int = WORKING_SR) -> str:
    """Render the approved SetPlan to a continuous mix file. Returns the path."""
    if not plan.slots:
        raise ValueError("empty plan — nothing to render")
    out_path = out_path or _default_out_path(plan)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    transitions = plan_transitions(plan)
    target_bpms = [plan.arc.target_at(s.position).bpm or s.bpm for s in plan.slots]

    mix = np.zeros(0, dtype=np.float32)
    for i, slot in enumerate(plan.slots):
        seg = _load_slot_audio(slot, sr)
        seg = _stretch(seg, sr, slot.bpm, target_bpms[i])
        if mix.size == 0:
            mix = seg
            continue
        cross_s = transitions[i - 1].crossfade_s if i - 1 < len(transitions) else 0.0
        mix = _crossfade_join(mix, seg, sr, cross_s)

    _write(out_path, mix, sr)
    return out_path


def _load_slot_audio(slot: Slot, sr: int) -> np.ndarray:
    """Load the chosen section of a track (cue_start..cue_end), or the whole file."""
    import librosa

    offset = float(slot.cue_start_s or 0.0)
    duration = None
    if slot.cue_start_s is not None and slot.cue_end_s is not None:
        duration = max(0.0, slot.cue_end_s - slot.cue_start_s)
    y, _ = librosa.load(slot.path, sr=sr, mono=True, offset=offset, duration=duration)
    return y.astype(np.float32)


def _stretch(y: np.ndarray, sr: int, src_bpm: float, dst_bpm: float) -> np.ndarray:
    """Beatmatch by time-stretching to the target tempo; no-op if deps/bpm absent."""
    ratio = stretch_ratio(src_bpm, dst_bpm)
    if abs(ratio - 1.0) < 1e-3:
        return y
    try:
        import pyrubberband as pyrb

        return pyrb.time_stretch(y, sr, ratio).astype(np.float32)
    except Exception:
        return y  # pyrubberband/rubberband-cli missing → play at native tempo


def _crossfade_join(mix: np.ndarray, seg: np.ndarray, sr: int, cross_s: float) -> np.ndarray:
    """Equal-power overlap of `mix`'s tail with `seg`'s head, with a bass swap."""
    n = int(min(cross_s * sr, len(mix), len(seg)))
    if n <= 0:
        return np.concatenate([mix, seg])
    fade_out = np.cos(np.linspace(0, np.pi / 2, n)) ** 1  # equal-power (sin²+cos²=1)
    fade_in = np.sin(np.linspace(0, np.pi / 2, n))
    tail = mix[-n:] * fade_out + _bass_swap_in(seg[:n], sr) * fade_in
    return np.concatenate([mix[:-n], tail, seg[n:]])


def _bass_swap_in(seg: np.ndarray, sr: int, cutoff: float = 180.0) -> np.ndarray:
    """High-pass the incoming track through the overlap so basslines don't clash.

    Uses scipy if present (a real Butterworth high-pass); otherwise returns the
    signal unfiltered (still an equal-power crossfade, just no EQ swap)."""
    try:
        from scipy.signal import butter, sosfilt

        sos = butter(2, cutoff / (sr / 2), btype="highpass", output="sos")
        return sosfilt(sos, seg).astype(np.float32)
    except Exception:
        return seg


def _normalize(mix: np.ndarray) -> np.ndarray:
    """Loudness-preserving normalize: scale by a *robust* peak, then hard-limit.

    Dividing by the absolute max lets one stray transient crush the whole set's
    level (and flatten the energy arc). Instead we anchor the 99.9th-percentile
    sample to the ceiling — preserving the arc's relative dynamics — and clip only
    the rare overs."""
    if not mix.size:
        return mix
    ref = float(np.percentile(np.abs(mix), 99.9))
    if ref > 1e-6:
        mix = mix * (_TARGET_PEAK / ref)
    return np.clip(mix, -1.0, 1.0).astype(np.float32)


def _write(out_path: str, mix: np.ndarray, sr: int) -> None:
    import soundfile as sf

    sf.write(out_path, _normalize(mix), sr)


def _default_out_path(plan: SetPlan) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in plan.arc.name).strip("-") or "set"
    return os.path.join(settings.output_dir, f"{safe}.wav")
