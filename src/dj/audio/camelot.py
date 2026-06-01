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

    0: identical · 1: relative or ±1 same-letter · 2+: number gap (mod 12).
    Lets the Selector *prefer* smoother transitions, not just allow/deny.
    """
    (na, la), (nb, lb) = parse(a), parse(b)
    if na == nb and la == lb:
        return 0
    if na == nb and la != lb:
        return 1
    step = min((na - nb) % 12, (nb - na) % 12)
    return step + (0 if la == lb else 1)


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
