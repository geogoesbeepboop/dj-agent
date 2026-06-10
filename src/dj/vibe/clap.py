"""CLAP encoder — the semantic "vibe" representation (audio ↔ text in one space).

CLAP (Contrastive Language-Audio Pretraining) is a neural net trained on
millions of (audio, caption) pairs so that a clip and its description land near
each other in a shared 512-d space. Two consequences we rely on:

  - embed_audio(track) and embed_text("dreamy nocturnal") are *comparable* —
    cosine distance between them is meaningful. That's what makes text→audio
    vibe search work (store.nearest_to_text).
  - It runs entirely locally (the weights are on disk, no API call). On Apple
    Silicon it uses the MPS backend; first run downloads ~1.5 GB of weights.

CLAP was trained on short (~10 s) clips, so for a full track we window the audio,
embed each window, and mean-pool — a standard way to get a whole-song embedding.

torch/transformers are imported lazily so the package imports without them.
"""

from __future__ import annotations

import numpy as np

from dj.config import CLAP_MODEL, CLAP_SAMPLE_RATE, VIBE_DIM

# ~10 s windows match CLAP's training clip length; mean-pooled into one vector.
_WINDOW_SECONDS = 10
_MAX_WINDOWS = 12  # cap work on long tracks (~2 min of audio sampled, evenly)

_model = None
_processor = None
_device = None


def _load():
    """Lazily load the model/processor once and pick the best device."""
    global _model, _processor, _device
    if _model is not None:
        return
    import torch
    from transformers import ClapModel, ClapProcessor

    if torch.backends.mps.is_available():
        _device = "mps"
    elif torch.cuda.is_available():
        _device = "cuda"
    else:
        _device = "cpu"

    _processor = ClapProcessor.from_pretrained(CLAP_MODEL)
    _model = ClapModel.from_pretrained(CLAP_MODEL).to(_device).eval()


def embed_audio(path: str) -> np.ndarray:
    """Encode an audio file into a (VIBE_DIM,) float32 L2-normalized vibe vector."""
    import librosa

    _load()
    y, _ = librosa.load(path, sr=CLAP_SAMPLE_RATE, mono=True)
    _ensure_audio(y, path)
    return _l2(np.mean([_encode_audio_window(w) for w in _window(y, CLAP_SAMPLE_RATE)], axis=0))


def embed_track_and_sections(
    path: str, bounds: list[tuple[float, float]]
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Embed the whole track AND each section in ONE audio load (ADR 0004).

    Mean-pool *all* windows → the discovery vector (`tracks.embedding`); mean-pool
    only the windows inside each (start_s, end_s) span → that section's vector
    (`sections.embedding`). Averaging a 6-min track's ambient intro with its peak
    drop yields a mushy midpoint that represents neither — so the *parts* get
    their own vectors, which is what transition matching actually compares.
    """
    import librosa

    _load()
    y, _ = librosa.load(path, sr=CLAP_SAMPLE_RATE, mono=True)
    _ensure_audio(y, path)
    track_vec = _l2(np.mean([_encode_audio_window(w) for w in _window(y, CLAP_SAMPLE_RATE)], axis=0))
    section_vecs = [_embed_span(y, CLAP_SAMPLE_RATE, s, e) for s, e in bounds]
    return track_vec, section_vecs


def embed_sections(path: str, bounds: list[tuple[float, float]]) -> list[np.ndarray]:
    """One CLAP vector per (start_s, end_s) span — the vibe of each part."""
    return embed_track_and_sections(path, bounds)[1]


def _embed_span(y: np.ndarray, sr: int, start_s: float, end_s: float) -> np.ndarray:
    """Mean-pool the windows inside one time span into a section vibe vector."""
    seg = y[int(start_s * sr): int(end_s * sr)]
    if len(seg) < sr:           # < ~1 s of audio: fall back to the whole signal
        seg = y
    return _l2(np.mean([_encode_audio_window(w) for w in _window(seg, sr)], axis=0))


def embed_text(text: str) -> np.ndarray:
    """Encode a text prompt into the same (VIBE_DIM,) space as embed_audio."""
    import torch

    _load()
    inputs = _processor(text=[text], return_tensors="pt", padding=True).to(_device)
    with torch.no_grad():
        feats = _model.get_text_features(**inputs)
    return _l2(feats[0].cpu().numpy().astype(np.float32))


def _encode_audio_window(window: np.ndarray) -> np.ndarray:
    import torch

    inputs = _processor(
        audios=window, sampling_rate=CLAP_SAMPLE_RATE, return_tensors="pt"
    ).to(_device)
    with torch.no_grad():
        feats = _model.get_audio_features(**inputs)
    return feats[0].cpu().numpy().astype(np.float32)


def _window(y: np.ndarray, sr: int) -> list[np.ndarray]:
    """Split a waveform into up to _MAX_WINDOWS evenly-spaced ~10 s windows."""
    win = _WINDOW_SECONDS * sr
    if len(y) <= win:
        return [y]
    n = min(_MAX_WINDOWS, max(1, len(y) // win))
    starts = np.linspace(0, len(y) - win, n).astype(int)
    return [y[s : s + win] for s in starts]


def _ensure_audio(y: np.ndarray, path: str) -> None:
    """Reject an empty/undecodable load before it reaches CLAP (Curator skips it)."""
    if y is None or len(y) == 0:
        raise ValueError(f"no decodable audio in {path}")


def _l2(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    if v.shape != (VIBE_DIM,):
        raise ValueError(f"CLAP returned {v.shape}, expected ({VIBE_DIM},)")
    norm = np.linalg.norm(v)
    return v / norm if norm > 1e-9 else v
