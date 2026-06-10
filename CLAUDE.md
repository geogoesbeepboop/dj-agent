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
├── config.py           # Settings (DATABASE_URL, CLAP/TASTE models+dims, HITL_LEVEL)
├── audio/
│   ├── segment.py      # [P1 ✅] track → Structure(bpm, downbeats, sections); allin1→librosa
│   ├── analyze.py      # librosa + pyloudnorm → BPM(downbeat) · Camelot · LUFS · per-section LUFS
│   └── camelot.py      # Camelot wheel (pure logic, no audio deps)
├── vibe/
│   ├── schema.sql      # [v2] pgvector DDL: tracks (acoustic+taste cols+LUFS) + sections
│   ├── clap.py         # CLAP: embed_audio + embed_track_and_sections + embed_text (512-d)
│   └── store.py        # upsert(+sections) + KNN (nearest, _to_text, _section, taste, ranked, cards)
├── taste/              # [P2 ✅] score · propagate · embed · tag (vibe-tagging CLI)
│                       #         pending (parked reviews + matcher) · review (capture CLI, ADR 0008)
├── arc.py              # [P3 ✅] the energy/BPM Arc artifact (control points + interp + shapes + to/from_dict)
├── plan.py             # [P3 ✅] Slot / SetPlan (HITL artifact; to/from_dict for persistence)
├── critic.py           # [P3 ✅] verifier + eval core (Camelot·BPM·arc-RMSE·artist spacing·key monotony)
├── mixer.py            # [P4 ✅] phrase-aligned crossfade render (pure plan + lazy audio); robust normalize + set_duration
├── persist.py          # [P5 ✅] log every generated set (JSON) → set-acceptance record + recently-played dedup
├── evals/runner.py     # [P5 ✅] scorecard: taste-match + discovery over the Critic; CLI A/B vs CLAP-only
├── metadata.py         # mutagen → ID3 tags (genre, mood, ISRC) as keyword filter + review match key
├── sources/            # SourceProvider seam: LocalFolderProvider
├── curator.py          # pipeline: source → segment + analyze + CLAP + sections + tags → DB
└── agents/             # [P3/5 ✅] tools · architect · selector · hitl · generate · explain (agent-core complete)
tests/                  # 103 fast: camelot·analyze·metadata·segment·score·propagate·arc·critic·selector·
                        #   architect·hitl·mixer·pending·plan·explain·persist·evals;  clap·taste_embed (slow)
docs/                   # architecture · database · embeddings · taste · set-generation · phases ·
                        #   backlog · your-todo · why-vibe-vectors · ADRs
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
uv run pytest -q          # fast suite (103 tests, no DB/model/audio): all pure logic
uv run pytest -m slow     # model tests: CLAP (~1.5 GB) + taste embedder (~90 MB)
```

## Current state: Phases 1-revision, 3, 4 + Phase 5 core landed (2026-06-04)

**Everything through Phase 4, plus the Phase 5 eval scorecard / `--explain` /
plan persistence, is built and unit-tested without a DB, model, or audio** (103
fast tests green). The heavy/external edges — `allin1`, the LLM,
`pyrubberband`/`scipy`, pgvector — are behind lazy, injectable seams with
deterministic fallbacks, so the whole pipeline runs and tests offline.

- **Phase 1 revision (✅ code):** `audio/segment.py` (sections + downbeats, allin1→
  librosa fallback), LUFS in `analyze.py`, `clap.embed_track_and_sections`, schema
  v2 (`sections` table), `store` section writes/queries, Curator wiring.
- **Phase 2 (✅ code):** taste loop (`dj/taste/`); propagation now stores a real
  per-track confidence (`taste_confidence`) the blend uses.
- **Phase 3 (✅ code):** `arc.py`, `plan.py`, `critic.py`, `agents/` (tools,
  architect, selector with generate→verify→revise, hitl, generate CLI). `ADR 0006`.
- **Phase 4 (✅ code):** `mixer.py` — phrase-length crossfades + lazy render. `ADR 0007`.
  Honest caveat: it does **not** yet sample-accurately beat-phase-lock or match
  seam tempo — see `docs/backlog.md` A1 (needs validation on real audio).
- **Phase 5 (core ✅ code):** `evals/runner.py` scorecard (taste-match, discovery,
  A/B vs CLAP-only), `agents/explain.py` (`--explain` narration), `persist.py`
  (logs every set → set-acceptance record + recently-played dedup).

**Pending — needs George (the live edges):** see `docs/your-todo.md`. In short:
`uv sync` → ingest a folder → tag ~50 favorites → `generate "<brief>" --offline
--explain` → `evals.runner` A/B → `--render` and **listen**. Decisions I need from
you + everything not auto-applied: `docs/backlog.md`. ADRs so far: 0003–0008.

## Starting a session

Tell Claude: *"Building the DJ agent, plan in BUILD_PLAN.md, on Phase [X].
Check docs/phases.md for open questions."*

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
- Streaming is excluded from ingestion (beatmatching needs the raw file); Spotify
  is used only to *export* a finished tracklist to share.

## Stack

| Concern | Library |
|---|---|
| Beats · downbeats · structure | `allin1` target; **`librosa`-only** fallback (msaf not used) (ADR 0005) |
| Audio analysis (key/energy) | `librosa` + `pyloudnorm` (LUFS) + `soundfile` |
| Acoustic vibe (CLAP, 512-d) | `torch` + `transformers` (`laion/larger_clap_music`) |
| Taste embedding (384-d) | `sentence-transformers` (`all-MiniLM-L6-v2`) on my notes |
| Metadata tags | `mutagen` |
| Vector store | `pgvector` via `psycopg2` + Supabase (tracks + sections) |
| Agent harness | `agent-core` `complete()` today; `claude-agent-sdk` optional (wrap toolbelt as an MCP server later) |
| Substrate | `agent-core` (editable dep at `../agent-core`) |
| Mixer | `pyrubberband` + `scipy` + `soundfile` (Phase 4+; needs the `rubberband` CLI) |
| Sharing | Spotify MCP (export approved tracklist as a playlist — backlog B1) |
| Tracing | `langfuse` via agent-core |
| Linter / Tests | `ruff` / `pytest` |

## agent-core

Sibling repo at `../agent-core`, installed as an editable dep. Do not fork or
copy it. Use `agent_core.tracing.trace` for span instrumentation in the
Curator and future agents.
