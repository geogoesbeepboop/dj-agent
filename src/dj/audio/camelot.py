"""The Camelot wheel — harmonic mixing, as pure logic (no audio deps).

DJs mix in compatible keys to avoid clashing. The Camelot wheel maps every
musical key to a code like "8B" (number 1-12 + letter A=minor, B=major). Two
tracks mix harmonically if their codes are:
  - identical,
  - same number, different letter (relative major/minor), or
  - adjacent number (±1), same letter.

This module converts a (pitch_class, mode) estimate into a Camelot code and
answers "are these two compatible?" — the constraint the Selector enforces.
"""

from __future__ import annotations

# Pitch classes: 0=C, 1=C#, 2=D, ... 11=B  (librosa/chroma convention)
# mode: "major" or "minor".

# Camelot codes by (pitch_class, mode).
_CAMELOT: dict[tuple[int, str], str] = {
    # Major keys → "B" side
    (0, "major"): "8B",   # C
    (7, "major"): "9B",   # G
    (2, "major"): "10B",  # D
    (9, "major"): "11B",  # A
    (4, "major"): "12B",  # E
    (11, "major"): "1B",  # B
    (6, "major"): "2B",   # F#/Gb
    (1, "major"): "3B",   # Db
    (8, "major"): "4B",   # Ab
    (3, "major"): "5B",   # Eb
    (10, "major"): "6B",  # Bb
    (5, "major"): "7B",   # F
    # Minor keys → "A" side
    (9, "minor"): "8A",   # Am
    (4, "minor"): "9A",   # Em
    (11, "minor"): "10A", # Bm
    (6, "minor"): "11A",  # F#m
    (1, "minor"): "12A",  # C#m
    (8, "minor"): "1A",   # G#m
    (3, "minor"): "2A",   # D#/Ebm
    (10, "minor"): "3A",  # Bbm
    (5, "minor"): "4A",   # Fm
    (0, "minor"): "5A",   # Cm
    (7, "minor"): "6A",   # Gm
    (2, "minor"): "7A",   # Dm
}

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def to_camelot(pitch_class: int, mode: str) -> str:
    """Map (pitch_class 0-11, 'major'|'minor') to a Camelot code like '8B'."""
    mode = mode.lower()
    if mode not in ("major", "minor"):
        raise ValueError(f"mode must be major|minor, got {mode!r}")
    return _CAMELOT[(pitch_class % 12, mode)]


def key_name(code: str) -> str:
    """Map a Camelot code back to a musical key name: '8A' → 'Am', '8B' → 'C'.

    Rekordbox's XML import expects a Tonality string in key-name form; sharps
    follow PITCH_NAMES (so 2A → 'D#m', not 'Ebm')."""
    target = parse(code)
    for (pc, mode), c in _CAMELOT.items():
        if parse(c) == target:
            return PITCH_NAMES[pc] + ("m" if mode == "minor" else "")
    raise ValueError(f"bad Camelot code: {code!r}")  # unreachable after parse()


def parse(code: str) -> tuple[int, str]:
    """Split '8B' -> (8, 'B'). Raises on malformed codes."""
    code = code.strip().upper()
    letter = code[-1]
    number = int(code[:-1])
    if letter not in ("A", "B") or not (1 <= number <= 12):
        raise ValueError(f"bad Camelot code: {code!r}")
    return number, letter


def compatible(a: str, b: str) -> bool:
    """True if two Camelot codes mix harmonically (the classic rules)."""
    (na, la), (nb, lb) = parse(a), parse(b)
    if na == nb and la == lb:
        return True                      # identical
    if na == nb and la != lb:
        return True                      # relative major/minor
    if la == lb and _wheel_adjacent(na, nb):
        return True                      # ±1 on the wheel, same letter
    return False


def distance(a: str, b: str) -> int:
    """Rough harmonic distance (0 = perfect, higher = rougher transition).

    0: identical · 1: relative, ±1 same-letter (fifth) · 2: ±1-semitone energy
    boost (±5 on the wheel, same letter) — a smooth lift DJs use constantly · 2+:
    wider number gaps. Lets the Selector *prefer* smoother transitions within the
    compatible set, not just allow/deny.
    """
    (na, la), (nb, lb) = parse(a), parse(b)
    if na == nb and la == lb:
        return 0
    if na == nb and la != lb:
        return 1
    step = min((na - nb) % 12, (nb - na) % 12)
    if la == lb and step == 5:
        return 2                         # +1-semitone energy boost — read as smooth
    return step + (0 if la == lb else 1)


def energy_boost(a: str, b: str) -> bool:
    """True if B is a ±1-semitone 'energy boost' from A (same mode, ±5 on the wheel).

    The standard trick to lift a set's energy without a key clash — a richer move
    than the textbook three, available to a future extended-harmonic Selector.
    """
    (na, la), (nb, lb) = parse(a), parse(b)
    return la == lb and min((na - nb) % 12, (nb - na) % 12) == 5


def grade(a: str, b: str) -> int:
    """Harmonic move grade: 0 perfect · 1 energy-boost/diagonal · 2 two-step · 3 clash.

    A tier above `compatible()` (which is exactly grade 0). The Critic can surface
    the grade and a future Selector can *allow grade ≤ 1 with a penalty* — but that
    loosens the hard key gate, a taste call (see the backlog), so the default gate
    stays strict.
    """
    (na, la), (nb, lb) = parse(a), parse(b)
    if na == nb:
        return 0                         # identical or relative major/minor
    step = min((na - nb) % 12, (nb - na) % 12)
    if la == lb:
        if step == 1:
            return 0                     # adjacent fifth — textbook compatible
        if step == 5:
            return 1                     # ±1-semitone energy boost
        if step == 2:
            return 2                     # two-step lift
        return 3
    return 1 if step == 1 else 3         # diagonal (relative of a neighbour) else clash


def _wheel_adjacent(na: int, nb: int) -> bool:
    return min((na - nb) % 12, (nb - na) % 12) == 1


def _print_wheel() -> None:  # pragma: no cover - a human sanity-check
    print("Camelot wheel (pitch / mode → code):")
    for (pc, mode), code in sorted(_CAMELOT.items(), key=lambda kv: (kv[1][:-1].zfill(2), kv[1][-1])):
        print(f"  {PITCH_NAMES[pc]:<3} {mode:<5} → {code}")


if __name__ == "__main__":  # pragma: no cover
    _print_wheel()
    print("\n8A↔9A compatible:", compatible("8A", "9A"))
    print("8A↔8B compatible:", compatible("8A", "8B"))
    print("8A↔11B compatible:", compatible("8A", "11B"))
