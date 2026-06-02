# Architecture

## The pitch

Spotify DJ is a recommender in a costume — it legally can't touch audio, so no
beatmatching, no arc. **dj-agent** analyzes your library into a vibe vector DB
and renders real continuous sets with a planned energy arc, surfacing discovery
picks you didn't know fit.

---

## Two representations per track

This is the central design idea. Every ingested track is stored as **two
complementary things**, because "fits the vibe" and "will actually mix" are
different questions:

| Representation | Holds | Used for |
|---|---|---|
| **CLAP vector** (512-d) | *Semantics* — what it sounds/feels like | Soft vibe ranking; **text→audio search** ("dreamy nocturnal") |
| **Structured columns** | *Hard math* — exact BPM, Camelot key, energy arc, duration | Mixing constraints the Selector enforces as SQL filters |

The old engineered vector crammed BPM and key *into* the embedding. We don't do
that anymore: semantics live in the CLAP vector, mixing math lives in plain
columns. Because CLAP maps **text and audio into the same space**, semantic
prompts work from day one — no separate "Phase 5" needed.

---

## Three-layer model

```
agent-core  (substrate — reused, unchanged)
├── tracing (Langfuse)  · LLM provider  · budget/cost tracking
├── task queue          · eval runner   · sandbox isolation

dj-agent  (the product)
├── Sources        # SourceProvider seam: local folder now; CC remote later
├── Curator        # background pipeline: track → analyze + CLAP + tags → DB
├── Vibe Vector DB # pgvector: CLAP vector + structured cols + metadata
├── Architect      # LLM agent: vibe prompt → target energy/BPM arc
├── Selector       # constraint search: order tracks to arc + discovery ratio
├── Mixer          # cue points, beatmatch (time-stretch), EQ, render mix
├── Critic         # scores transitions; rejects bad ones
└── Taste/Memory   # learns from skips/replays (Phase 5)
```

**agent-core** is a sibling repo at `../agent-core`, installed as an editable
dep. dj-agent never forks it.

**Architect + Selector** are Claude Agent SDK agents — they call tools
(`query_vibe_db`, `nearest_to_text`, `check_harmonic_compat`) in a loop until
they produce a tracklist. The rest of the system (Curator, Mixer, Critic) is
deterministic Python — no LLM involved.

---

## Data flow

```
Audio (SourceProvider: LocalFolderProvider now; Jamendo/FMA later)
       │
       ▼
  [Curator] per track, three things in parallel:
       ├── analyze.py   librosa: BPM · key→Camelot · energy arc · duration   (structured)
       ├── clap.py      CLAP audio encoder → 512-d vibe vector               (semantic)
       └── metadata.py  mutagen: genre · artist · mood tags                  (metadata)
       │
       ▼
  [pgvector / Supabase]  tracks table  (vector + columns + tags, one row)
       │
       ╔══════════════════════════════════════════╗
       ║  SET GENERATION (Phase 2+)               ║
       ╠══════════════════════════════════════════╣
       ║  [Architect]  vibe prompt → arc target   ║
       ║       ↓                                   ║
       ║  [Selector]   nearest_to_text + BPM/key  ║
       ║               filters → ordered tracklist║
       ║       ↓                                   ║
       ║  [HITL gate]  approve/nudge the plan     ║
       ╚══════════════════════════════════════════╝
       │
       ▼
  [Mixer] cue points · beatmatch · crossfade → rendered .wav/.mp3
       │
       ▼
  [Critic] scores transition smoothness → accept/retry
```

---

## Component responsibilities

| Component | Phase | Type | Key dep |
|---|---|---|---|
| `dj.sources.*` | 1 | Seam / I/O | pathlib |
| `dj.audio.analyze` | 1 | Deterministic DSP | librosa |
| `dj.audio.camelot` | 0 | Pure logic | — |
| `dj.vibe.clap` | 1 | ML inference (local) | torch + transformers |
| `dj.metadata` | 1 | I/O | mutagen |
| `dj.vibe.store` | 1 | I/O + search | psycopg2 + pgvector |
| `dj.curator` | 1 | Orchestrator | all of the above + agent-core tracing |
| `dj.agents.architect` | 2 | LLM agent | claude-agent-sdk |
| `dj.agents.selector` | 2 | LLM agent | claude-agent-sdk |
| `dj.mixer` | 3 | Deterministic | pydub + pyrubberband |
| `dj.critic` | 4 | Deterministic/heuristic | numpy + librosa |
| `dj.memory` | 5 | ML | — |

---

## HITL gate

One high-value human-in-the-loop checkpoint: **approve the set before
rendering** — *per set, not per track*. The Selector proposes a tracklist + arc
+ transition plan as **data** (no audio yet); the human reads it (ordered list
with BPM/Camelot/energy position, target-vs-actual arc, flagged rough
transitions) and approves or nudges the prompt before the Mixer spends compute
rendering audio. You *listen* to the final render afterward (that feeds the
blind A/B eval).

Controlled by `HITL_LEVEL`: `full` (always pause) | `none` (skip, for eval runs).

---

## Sources & licensing

The differentiator vs Spotify is **audio manipulation**, which needs the raw
decodable file on disk. Streaming APIs can't provide that, so they're excluded
by design. The `SourceProvider` seam (`dj/sources/`) lets the Curator ingest
from anywhere that yields a local file:

- **`LocalFolderProvider`** (Phase 1) — your own files. A `favorites/` path
  segment marks `is_favorite`.
- **Jamendo / FMA / ccMixter** (later) — CC-licensed, downloadable audio so the
  agent never "runs out" of tracks. These drop in behind the same protocol with
  zero Curator changes.

---

## Key design decisions

See `docs/adr/` for full records. Summary:

- **CLAP from the start** — learned audio+text embeddings give real semantic
  vibe search (and text→audio) on day one. The ingestion cost (~1–3s/track on
  M1 Pro MPS) is one-time; queries stay sub-millisecond. See
  `adr/0002-clap-from-the-start.md`.
- **pgvector over a dedicated vector DB** — 512-d × ~10k tracks ≈ 20 MB; HNSW
  is sub-ms at this scale, and it keeps vector + metadata + constraints in one
  row / one query. See `adr/0001-pgvector-over-pinecone.md`.
- **Two representations** — semantics in the CLAP vector, mixing math in
  structured columns. Cleaner than packing everything into one vector.
- **Deterministic Curator, LLM only in Architect/Selector** — analysis has no
  ambiguity; the agents earn their cost on the fuzzy "does this vibe fit?" and
  arc-planning problems.
- **agent-core as substrate, not forked** — tracing, budgets, evals, queue are
  cross-cutting concerns reused across projects.
