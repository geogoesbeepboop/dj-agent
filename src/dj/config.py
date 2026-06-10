"""Central config for the DJ agent. Pure Python, no heavy imports."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


# Dimensionality of the CLAP vibe vector (see vibe/clap.py). Must match the
# vector(VIBE_DIM) column in vibe/schema.sql — change both together.
VIBE_DIM = 512

# CLAP model (HuggingFace transformers). The music-specific variant clusters
# musical semantics better than the general CLAP for a DJ library.
CLAP_MODEL = _env("CLAP_MODEL", "laion/larger_clap_music")

# CLAP's required input sample rate (the model is trained at 48 kHz). Distinct
# from Settings.sample_rate, which is the 22.05 kHz librosa uses for DSP.
CLAP_SAMPLE_RATE = int(_env("DJ_CLAP_SAMPLE_RATE", "48000"))

# --- Personal taste layer (docs/taste.md, ADR 0003) -------------------------
# My free-text notes are embedded into this space by a small local
# sentence-transformer — DISTINCT from CLAP's 512-d audio space. Must match the
# tracks.taste_vec vector(TASTE_DIM) column in vibe/schema.sql.
TASTE_DIM = 384
TASTE_MODEL = _env("TASTE_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# Default blend for ranking: score = α·acoustic + β·taste + γ·(rating/5).
# Leans acoustic while taste labels are sparse; raise β as labels accumulate.
TASTE_WEIGHTS = (
    float(_env("DJ_W_ACOUSTIC", "0.5")),
    float(_env("DJ_W_TASTE", "0.4")),
    float(_env("DJ_W_RATING", "0.1")),
)


@dataclass(frozen=True)
class Settings:
    # Supabase / Postgres connection (pgvector lives here). Empty = DB ops skipped.
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", ""))
    # Default sample rate for analysis (22050 is plenty for BPM/key/energy).
    sample_rate: int = field(default_factory=lambda: int(_env("DJ_SAMPLE_RATE", "22050")))
    # Where rendered mixes get written.
    output_dir: str = field(default_factory=lambda: _env("DJ_OUTPUT_DIR", "./renders"))
    # HITL: 'full' (approve set before render) | 'none'.
    hitl_level: str = field(default_factory=lambda: _env("HITL_LEVEL", "full"))

    @property
    def db_enabled(self) -> bool:
        return bool(self.database_url)


settings = Settings()
