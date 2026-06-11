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
    # Where link ingestion (dj.ingest) downloads audio before curating it.
    library_dir: str = field(default_factory=lambda: _env("DJ_LIBRARY_DIR", "./library"))
    # Spotify Web API (link resolution only — metadata, no audio). Create a free
    # app at developer.spotify.com/dashboard; empty = Spotify links can't resolve.
    # Reading playlists now needs *user* auth (Authorization Code flow): the
    # app-only client-credentials flow stopped returning playlist contents after
    # Spotify's Nov-2024 change. Register `spotify_redirect_uri` as a Redirect URI
    # in the app dashboard, then run `python -m dj.ingest --login` once.
    spotify_client_id: str = field(default_factory=lambda: _env("SPOTIFY_CLIENT_ID", ""))
    spotify_client_secret: str = field(default_factory=lambda: _env("SPOTIFY_CLIENT_SECRET", ""))
    # Spotify requires a loopback IP (127.0.0.1, not "localhost") for new apps.
    spotify_redirect_uri: str = field(
        default_factory=lambda: _env("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    )
    # Where the refreshable user token is cached after the one-time login.
    spotify_cache_path: str = field(
        default_factory=lambda: _env("SPOTIFY_CACHE_PATH", ".spotify-cache")
    )
    # HITL: 'full' (approve set before render) | 'none'.
    hitl_level: str = field(default_factory=lambda: _env("HITL_LEVEL", "full"))
    # allin1 structure detector (ADR 0005/0012). The Python package fights the
    # venv's torch pins, so segment.py can also drive an allin1 CLI living in a
    # DIFFERENT Python (e.g. the python.org framework install) as a subprocess.
    # Empty bin = CLI bridge disabled (librosa fallback only, if the module is
    # also absent). Results are cached as JSON per track, so re-ingest is free.
    allin1_bin: str = field(default_factory=lambda: _env("DJ_ALLIN1_BIN", "allin1"))
    allin1_cache_dir: str = field(
        default_factory=lambda: _env("DJ_ALLIN1_CACHE", "~/.cache/dj-agent/allin1")
    )
    # Passed to `allin1 -d` (e.g. 'cpu', 'mps'); empty = allin1's own default.
    allin1_device: str = field(default_factory=lambda: _env("DJ_ALLIN1_DEVICE", ""))

    @property
    def db_enabled(self) -> bool:
        return bool(self.database_url)


settings = Settings()
