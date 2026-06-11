# dj-agent — Claude Code project guide

## What this is

A **personal** DJ agent (for me + friends, not a product) that analyzes *my*
library into a vibe vector DB, **learns my taste** from how I tag tracks,
**segments every song** so it can mix the right *part*, and renders real
beatmatched sets with a planned energy arc. See `BUILD_PLAN.md` for the full
pitch and `docs/` for deep reference.

## Quick orientation

```
src/dj/
├── config.py           # Settings (DATABASE_URL, CLAP/TASTE models+dims, LIBRARY_DIR, Spotify creds, HITL_LEVEL)
├── audio/
│   ├── segment.py      # [P1 ✅] track → Structure(bpm, downbeats, sections); allin1→librosa
│   ├── analyze.py      # librosa + pyloudnorm → BPM(downbeat) · Camelot · LUFS · per-section LUFS
│   └── camelot.py      # Camelot wheel (pure logic, no audio deps) + key_name ('8A'→'Am')
├── vibe/
│   ├── schema.sql      # [v2] pgvector DDL: tracks (acoustic+taste cols+LUFS) + sections
│   ├── clap.py         # CLAP: embed_audio + embed_track_and_sections + embed_text (512-d)
│   └── store.py        # upsert(+sections) + KNN (nearest, _to_text, _section, taste, ranked, cards)
├── taste/              # [P2 ✅] score · propagate · embed · tag (vibe-tagging CLI)
│                       #         pending (parked reviews + matcher) · review (capture CLI, ADR 0008)
│                       #         judge (bulk-judge a pasted playlist link, no download)
├── ingest/             # [✅] paste a Spotify/YouTube link → tracklist → yt-dlp FLAC → Curator
│                       #      links (classify) · resolve (spotipy/yt-dlp metadata) · download ·
│                       #      provider (LinkProvider) · __main__ (CLI)  (ADR 0009)
├── export/             # [✅] approved SetPlan → rekordbox.xml (cues/key/BPM) + .m3u8 (ADR 0010)
├── arc.py              # [P3 ✅] the energy/BPM Arc artifact (control points + interp + shapes + to/from_dict)
├── plan.py             # [P3 ✅] Slot / SetPlan (HITL artifact; to/from_dict for persistence)
├── critic.py           # [P3 ✅] verifier + eval core (Camelot·BPM·arc-RMSE·artist spacing·key monotony)
├── mixer.py            # [P4 ✅] phrase-aligned crossfade render (pure plan + lazy audio); robust normalize + set_duration
├── persist.py          # [P5 ✅] log every generated set (JSON) → set-acceptance record + recently-played dedup
├── evals/runner.py     # [P5 ✅] scorecard: taste-match + discovery over the Critic; CLI A/B vs CLAP-only
├── metadata.py         # mutagen → ID3 tags (genre, mood, ISRC) as keyword filter + review match key
├── sources/            # SourceProvider seam: LocalFolderProvider (links live in dj/ingest)
├── curator.py          # pipeline: source → segment + analyze + CLAP + sections + tags → DB
│                       #   main() takes a folder OR a URL (dispatches to LinkProvider)
└── agents/             # [P3/5 ✅] tools · architect · selector · hitl · generate · explain
                        #   generate exports manual mode (rekordbox.xml+m3u8) on every approval
tests/                  # 197 fast (no DB/model/network/audio): all pure logic + seam fakes;
                        #   clap·taste_embed (slow)
docs/                   # architecture · database · embeddings · taste · set-generation · phases ·
                        #   backlog · your-todo · why-vibe-vectors · ADRs (0001–0010)
```

## Representations per track (see docs/architecture.md)

- **CLAP acoustic vector (512-d, track)** — generic "what it sounds like";
  discovery + **text→audio search** (`store.nearest_to_text`) + spreading taste.
- **Taste vector (384-d, track)** — *mine*: my note embedded; ranking by my taste
  (`docs/taste.md`, ADR 0003). Blend: `α·acoustic + β·taste + γ·rating`.
- **Section vectors (512-d, per section)** — the vibe of *each part*, so a set can
  use just the chorus + outro and mix on musical boundaries (ADR 0004).
- **Structured columns** — BPM (downbeat-derived), Camelot, LUFS, section
  bounds/beats; the hard mixing constraints + cue points.

Semantics go in vectors, mixing math goes in columns — never mixed.

## Running the tests

```bash
uv run pytest -q          # fast suite (197 tests, no DB/model/network/audio): all pure logic
uv run pytest -m slow     # model tests: CLAP (~1.5 GB) + taste embedder (~90 MB)
```

## Current state: end-to-end loop closed (2026-06-10)

**Paste a link → library → judged → set → rekordbox/render, all code-complete
and unit-tested offline** (197 fast tests green). The heavy/external edges —
`allin1`, the LLM, `pyrubberband`/`scipy`, pgvector, **yt-dlp/spotipy** — are
behind lazy, injectable seams with deterministic fallbacks, so the whole
pipeline runs and tests offline.

- **Link ingestion (✅ 2026-06-10, ADR 0009):** `dj/ingest/` — paste a Spotify or
  YouTube link (track/album/playlist/artist); Spotify resolves via spotipy
  metadata (incl. ISRC) + duration-matched YouTube search, downloads FLAC via
  yt-dlp into `DJ_LIBRARY_DIR`, stamps tags, feeds the Curator.
  `python -m dj.ingest <url>` or `python -m dj.curator <url>`.
- **Bulk judging (✅ 2026-06-10):** `python -m dj.taste.judge <url>` — judge a
  playlist track-by-track without downloading; in-library tracks tag directly,
  unknown ones park as pending reviews that auto-apply at ingest (ADR 0008).
- **Two-form output (✅ 2026-06-10, ADR 0010):** every approved set writes
  `rekordbox.xml` (collection + playlist + MIX IN/OUT cues — **manual mode**)
  and `.m3u8`; `--render` is **automatic mode** (continuous beatmatched file).

Earlier phases (all ✅ code, landed 2026-06-04): segmentation + LUFS + section
embeddings (P1 revision), the taste loop with real propagation confidence (P2),
arc/plan/critic + the agent stack (P3, ADR 0006), the Mixer (P4, ADR 0007 —
honest caveat: no sample-accurate beat phase-lock yet, `docs/backlog.md` A1),
and the eval scorecard / `--explain` / persistence (P5 core).

**Pending — needs George (the live edges):** see `docs/your-todo.md`. In short:
`uv sync` → paste a playlist link (`python -m dj.ingest <url>`) → bulk-judge a
playlist you love (`python -m dj.taste.judge <url>`) → tag/propagate →
`generate "<brief>" --offline --explain` → import the XML into rekordbox / or
`--render` and **listen** → `evals.runner` A/B. Decisions I need from you +
everything not auto-applied: `docs/backlog.md`. ADRs so far: 0001–0010.

## Starting a session

Tell Claude: *"Building the DJ agent, plan in BUILD_PLAN.md, on Phase [X].
Check docs/phases.md for open questions."*

## Project Claude tooling (`.claude/`)

- **Skills** (the product workflows, in-chat): `/ingest-link` (paste a link →
  library), `/bulk-judge` (paste a playlist → judge each track, no download),
  `/make-set` (brief → plan → chat approval → rekordbox.xml/m3u8/render),
  `/library-status` (DB + taste-loop snapshot), `/vibe-review` (park one review).
- **Agents**: `planner` · `reviewer` · `adr-writer` · `eval-runner` · `tutor` ·
  `ingest-smoker` (live yt-dlp smoke test — fakes can't catch flag rot) ·
  `rekordbox-validator` (export-artifact invariants, read-only).
- **Hook**: a `Stop` gate (`.claude/hooks/test-gate.sh`) runs the fast suite
  when `.py` files changed and blocks ending the turn on red. Formatting/guard
  hooks live in the user-global settings — don't duplicate them here.
- **Gotchas these encode**: `hitl.confirm()` auto-rejects on piped stdin → in-
  session generation must use `HITL_LEVEL=none` + chat approval; reruns need
  `--allow-repeats` or the recently-played dedup mutates the set; Spotify
  editorial playlists (`37i9dQZF1DX…`) 404 for new API apps.

## Key invariants

- `config.VIBE_DIM = 512` matches `tracks.embedding`/`sections.embedding` (CLAP).
  `config.TASTE_DIM = 384` matches `tracks.taste_vec` (sentence-transformer).
  Change a dim → ALTER + index rebuild + re-ingest.
- DB ops are skipped when `DATABASE_URL` is empty — unit tests always run clean.
- `clap.embed_audio` and `clap.embed_text` share one 512-d space — that's what
  makes text→audio work. The taste vector is a *separate* space; the two are
  bridged by acoustic-neighbor label propagation, not by sharing a space.
- Acoustic (general) and taste (me) are separate vectors on the same row, blended
  at query time — never fused into one vector (ADR 0003).
- Sections are first-class rows; a track keeps a discovery vector AND per-section
  vectors, so the Selector may use *part* of a track (ADR 0004).
- BPM is downbeat-derived; energy is cross-track LUFS — not raw `beat_track` or
  per-track-normalized RMS (ADR 0005).
- The Curator is idempotent: re-ingest upserts the track and replaces its sections.
- Camelot compatibility is a hard constraint; vibe + taste similarity is the soft
  ranking within compatible candidates.
- Beatmatching needs a real local file — streaming APIs are never an audio
  source. A pasted Spotify/YouTube link is *resolved to a downloaded file*
  (yt-dlp → FLAC, ADR 0009) before anything touches it; Spotify's API supplies
  only metadata (and, later, playlist export to share — backlog B1).
- Ingested-from-link files carry stamped tags incl. ISRC — that's what lets
  parked taste reviews (ADR 0008) auto-apply confidently at ingest. Don't strip
  or rewrite those tags.

## Stack

| Concern | Library |
|---|---|
| Beats · downbeats · structure | `allin1` target; **`librosa`-only** fallback (msaf not used) (ADR 0005) |
| Audio analysis (key/energy) | `librosa` + `pyloudnorm` (LUFS) + `soundfile` |
| Acoustic vibe (CLAP, 512-d) | `torch` + `transformers` (`laion/larger_clap_music`) |
| Taste embedding (384-d) | `sentence-transformers` (`all-MiniLM-L6-v2`) on my notes |
| Metadata tags | `mutagen` (read at ingest; written by link downloads incl. ISRC) |
| Vector store | `pgvector` via `psycopg2` + Supabase (tracks + sections) |
| Link ingestion | `yt-dlp` (list + FLAC download; needs `ffmpeg`) + `spotipy` (Spotify metadata) (ADR 0009) |
| Set export | `dj/export` → rekordbox XML + m3u8, pure stdlib (ADR 0010) |
| LLM | `anthropic` SDK via `dj/llm.py` `complete()` (tiers; inlined from the retired agent-core) |
| Mixer | `pyrubberband` + `scipy` + `soundfile` (Phase 4+; needs the `rubberband` CLI) |
| Sharing | Spotify MCP (export approved tracklist as a playlist — backlog B1) |
| Tracing | `langfuse` via `dj/tracing.py` (no-ops without keys) |
| Linter / Tests | `ruff` / `pytest` |
