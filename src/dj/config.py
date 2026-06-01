"""Central config for the DJ agent. Pure Python, no heavy imports."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# Dimensionality of the v1 engineered "vibe vector" (see vibe/embed.py).
# Phase 5 swaps this for CLAP's 512-d learned embedding — change in one place.
VIBE_DIM = 28


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


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
