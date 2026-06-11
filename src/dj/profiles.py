"""Genre profiles: how mixing *changes* per genre — one explicit, tunable table.

A house blend and a hip-hop cut are different crafts: long 16-bar EQ blends at
±8% tempo bend vs. 2-bar cuts where harmonic mixing barely matters and stretching
a rap vocal 6% sounds wrong. Encoding that as data (instead of constants buried
in the Selector/Critic/Mixer) gives every layer the same genre answer:

  - the **Architect** seeds the arc's BPM/LUFS ranges + default shape,
  - the **Selector/Critic** get genre thresholds (BPM-jump and harmonic bars),
  - the **Selector** plans play spans inside genre airtime bounds,
  - the **Mixer** caps the crossfade phrase length and the tempo stretch.

`detect()` resolves a profile from the vibe brief (first keyword hit, ordered
most-specific-first); `--genre` on generate overrides it. Everything here is
pure data + string matching — no audio, no DB, no models.
"""

from __future__ import annotations

from dataclasses import dataclass

from dj.critic import Thresholds


@dataclass(frozen=True)
class GenreProfile:
    """The mixing personality of a genre. Values are starting points, not law."""

    name: str
    keywords: tuple[str, ...]            # brief substrings that select this profile
    bpm_range: tuple[float, float]       # the arc's default tempo band
    lufs_range: tuple[float, float]      # the arc's default energy band
    default_shape: str = "build"         # arc shape when the brief doesn't say
    # Critic bars: how rough a transition this genre tolerates.
    max_bpm_jump: float = 6.0            # adjacent-slot BPM delta before it's rough
    min_harmonic_compat: float = 0.7     # fraction of transitions that must be in key
    # Mixer physics: how transitions are *played* in this genre.
    max_stretch: float = 0.06            # tempo bend cap (±) before it sounds wrong
    max_xfade_bars: int = 8              # longest overlap (quantized to 1/2/4/8/16/32)
    # Airtime: how long one track typically holds the floor.
    min_play_s: float = 120.0
    max_play_s: float = 330.0
    avg_slot_minutes: float = 3.5        # minutes → track-count estimate

    def thresholds(self) -> Thresholds:
        """The Critic thresholds this genre grades sets against."""
        return Thresholds(
            max_bpm_jump=self.max_bpm_jump,
            min_harmonic_compat=self.min_harmonic_compat,
        )


# Ordered most-specific-first: detect() returns the FIRST keyword hit, so
# "afro house" lands on afro (above house) and "melodic techno" on techno.
PROFILES: tuple[GenreProfile, ...] = (
    GenreProfile(
        name="afro",
        keywords=("afro", "amapiano", "afrobeats"),
        bpm_range=(110.0, 122.0), lufs_range=(-16.0, -8.0),
        max_bpm_jump=5.0, min_harmonic_compat=0.7,
        max_stretch=0.05, max_xfade_bars=8,
        min_play_s=150.0, max_play_s=330.0, avg_slot_minutes=3.75,
    ),
    GenreProfile(
        name="techno",
        keywords=("techno", "warehouse", "industrial", "rave", "acid"),
        bpm_range=(126.0, 135.0), lufs_range=(-15.0, -7.0),
        default_shape="peak",
        max_bpm_jump=4.0, min_harmonic_compat=0.75,
        max_stretch=0.08, max_xfade_bars=16,
        min_play_s=180.0, max_play_s=360.0, avg_slot_minutes=4.0,
    ),
    GenreProfile(
        name="trance",
        keywords=("trance", "uplifting", "psytrance"),
        bpm_range=(134.0, 140.0), lufs_range=(-15.0, -7.0),
        max_bpm_jump=4.0, min_harmonic_compat=0.8,
        max_stretch=0.06, max_xfade_bars=16,
        min_play_s=180.0, max_play_s=420.0, avg_slot_minutes=4.5,
    ),
    GenreProfile(
        name="house",
        keywords=("house", "disco", "deep", "melodic", "garage", "funky"),
        bpm_range=(118.0, 126.0), lufs_range=(-18.0, -8.0),
        max_bpm_jump=4.0, min_harmonic_compat=0.8,
        max_stretch=0.08, max_xfade_bars=16,
        min_play_s=180.0, max_play_s=360.0, avg_slot_minutes=4.0,
    ),
    GenreProfile(
        name="dnb",
        keywords=("dnb", "drum and bass", "drum & bass", "jungle", "liquid"),
        bpm_range=(170.0, 178.0), lufs_range=(-13.0, -6.0),
        default_shape="peak",
        max_bpm_jump=5.0, min_harmonic_compat=0.7,
        max_stretch=0.04, max_xfade_bars=8,
        min_play_s=120.0, max_play_s=270.0, avg_slot_minutes=3.0,
    ),
    GenreProfile(
        # Hip-hop/R&B sets live on cuts and doubles, not long blends: harmonic
        # mixing is a tiebreak (vocals dominate), stretching a vocal >3% warbles,
        # and a track rarely airs past ~3 min before the next one drops.
        name="hiphop",
        keywords=("hip hop", "hip-hop", "hiphop", "rap", "trap", "r&b", "rnb", "drill"),
        bpm_range=(84.0, 102.0), lufs_range=(-16.0, -8.0),
        max_bpm_jump=12.0, min_harmonic_compat=0.4,
        max_stretch=0.03, max_xfade_bars=2,
        min_play_s=90.0, max_play_s=210.0, avg_slot_minutes=2.5,
    ),
    GenreProfile(
        # Latin nights span reggaeton/dembow/bachata tempos — DJs bridge the
        # gaps with cuts, so the jump bar is loose and the blends short.
        name="latin",
        keywords=("latin", "reggaeton", "perreo", "dembow", "bachata", "salsa",
                  "cumbia", "merengue", "urbano"),
        bpm_range=(90.0, 112.0), lufs_range=(-14.0, -7.0),
        max_bpm_jump=10.0, min_harmonic_compat=0.5,
        max_stretch=0.04, max_xfade_bars=4,
        min_play_s=120.0, max_play_s=240.0, avg_slot_minutes=3.0,
    ),
    GenreProfile(
        name="pop",
        keywords=("pop", "top 40", "top40", "wedding", "party", "open format",
                  "throwback", "karaoke"),
        bpm_range=(100.0, 128.0), lufs_range=(-14.0, -7.0),
        max_bpm_jump=14.0, min_harmonic_compat=0.4,
        max_stretch=0.04, max_xfade_bars=4,
        min_play_s=90.0, max_play_s=210.0, avg_slot_minutes=2.5,
    ),
    GenreProfile(
        name="downtempo",
        keywords=("downtempo", "chill", "lounge", "ambient", "sunset", "dinner",
                  "background"),
        bpm_range=(100.0, 116.0), lufs_range=(-22.0, -12.0),
        default_shape="flat",
        max_bpm_jump=6.0, min_harmonic_compat=0.7,
        max_stretch=0.06, max_xfade_bars=8,
        min_play_s=150.0, max_play_s=330.0, avg_slot_minutes=4.0,
    ),
)

# The open-format fallback: today's Selector/Critic/Mixer defaults, unchanged.
DEFAULT = GenreProfile(
    name="open",
    keywords=(),
    bpm_range=(118.0, 126.0), lufs_range=(-18.0, -8.0),
)


def detect(brief: str) -> GenreProfile:
    """Resolve a profile from a vibe brief — first keyword hit wins, else open."""
    b = (brief or "").lower()
    for profile in PROFILES:
        if any(kw in b for kw in profile.keywords):
            return profile
    return DEFAULT


def get(name: str | None) -> GenreProfile:
    """Look a profile up by name (e.g. the persisted SetPlan.genre); open if unknown."""
    if name:
        wanted = name.strip().lower()
        for profile in PROFILES:
            if profile.name == wanted:
                return profile
    return DEFAULT
