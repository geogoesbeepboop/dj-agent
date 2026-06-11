# dj-agent — Build Plan

> Open in a fresh Claude Code session inside `~/dev/dj-agent/`. Phases run
> top-to-bottom; each ends with something you can verify (often something you
> can *hear*). Deep "why" notes live in `docs/`.

The pitch: **"Spotify DJ is a recommender in a costume — it legally can't touch
the audio, so no beatmatch, no arc, and no idea what *I* actually like. Mine
analyzes my own library into a vibe vector DB, learns my taste from how I tag
and react to tracks, segments every song so it can mix in and out of the *right
part*, and renders real continuous sets with a planned energy arc."**

This is a **personal** agent — for me and my friends, not a product. That single
fact drives the whole design: **my taste is the center of gravity, not an
afterthought.** Copyright/licensing is a non-issue (personal use), so we ingest
my real library and stop hedging.

---

## The four decisions this plan is built on

These were decided deliberately (see the ADRs in `docs/adr/`):

1. **Taste = notes + light tags** (`ADR 0003`). I write a sentence or two in my
   own words for the tracks I care about, plus light structured fields (rating,
   role). Notes become a *taste vector*; tags become filters. I label my
   favorites; the agent propagates to the rest.
2. **Two vectors, one row, blended** (`ADR 0003`). Every track carries a **CLAP
   acoustic vector** (general — "what it sounds like") *and* a **taste vector**
   (me — "what I think of it"). Queries blend them (`α·acoustic + β·taste`); I
   can dial the mix or search either alone. No second database to sync.
3. **Full structural segmentation** (`ADR 0004`). Sections (intro / verse /
   build / chorus / drop / break / bridge / outro) are **first-class rows** with
   their own embeddings and energy. This is what lets a set use *part* of a
   track — just the chorus and the outro, or the bridge into the drop — instead
   of always playing it end to end.
4. **Calibrated beats + energy** (`ADR 0005`). BPM/downbeats come from a
   structure-aware detector (not a single librosa estimate that octave-errors),
   and energy is **cross-track-comparable loudness (LUFS)** — because "plan an
   energy arc across a set" is meaningless if every track's energy is normalized
   to its own 0–1 range.

---

## Architecture (three layers — same substrate as the migration agent)

```
dj/llm.py + dj/tracing.py  (inlined from the retired agent-core)
├── model providers · tracing · evals · queueing · budgets · sandbox

dj-agent  (the product)
├── Ingest      # pasted Spotify/YouTube link → local FLAC (the second front door)
├── Curator     # background: ingest audio → segment + analyze + embed → vibe DB
├── Vibe DB     # pgvector: tracks (acoustic vec + taste vec + cols) + sections
├── Taste loop  # tagging UX + taste vectors + label propagation + blended score
├── Architect   # LLM agent: vibe prompt → target energy/BPM arc
├── Selector    # generate→verify→revise loop: pick tracks AND sections to the arc
├── Mixer       # cue to section boundaries, beatmatch, EQ, render transitions
├── Export      # approved set → rekordbox.xml + m3u8 (play it manually)
├── Critic      # deterministic transition scorer; the Selector's verifier
└── Memory      # learns taste from accept/skip/replay (later)

Agent harness = Claude Agent SDK  (Architect + Selector are agent-centric)
```

**Why embeddings/pgvector genuinely belong HERE:** relevance is *fuzzy* ("does
this fit the vibe?") and the corpus is *large/open* (my whole library). That's
the both-halves-true case vector search is for. We use **CLAP** learned
audio+text embeddings, so **text→audio** search ("find me something dreamy and
nocturnal") works on day one — but CLAP is the *general* signal. The *personal*
signal (my taste vector) is what makes the sets mine. See
`docs/why-vibe-vectors.md` and `docs/embeddings.md`.

---

## Two-plus representations per track

| Representation | Holds | Used for |
|---|---|---|
| **CLAP acoustic vector** (512-d, track) | *Generic* "what it sounds like" | Discovery; text→audio search; spreading my sparse labels |
| **Taste vector** (384-d, track) | *Mine* — my note embedded | Ranking by my taste; "more like the ones I love" |
| **Section vectors** (512-d, per section) | The vibe of *each part* | Transition matching (outro of A ↔ intro of B); partial-track use |
| **Structured columns** | Hard math — BPM, Camelot, LUFS, section bounds/beats | Mixing constraints the Selector filters on; cue points |

Semantics go in vectors, mixing math goes in columns — never mixed. See
`docs/architecture.md` and `docs/database.md`.

---

## Audio scope

Works on **my actual library** — no licensing hedging, because this is personal
use. The differentiator vs Spotify is twofold: the **audio-manipulation layer**
(real beatmatched, section-aware transitions they can't legally do) and the
**personal taste model** (they optimize for everyone; this optimizes for me).
The library grows through **two front doors** (`ADR 0009`): local folders, and
**pasted Spotify/YouTube links** (`python -m dj.ingest <url>`, or hand the URL
to the curator) — Spotify resolves to catalog metadata (incl. the ISRC that
auto-applies parked taste reviews, `ADR 0008`), the audio comes from a
duration-matched YouTube search via `yt-dlp`, and everything flows through the
same Curator pipeline. **Sharing/output:** every approved set ships in **two
playable forms** (`ADR 0010`) — a `rekordbox.xml` + `.m3u8` to perform the set
manually (order, key, BPM, and MIX IN/OUT cue points pre-set) and the
beatmatched *mix* rendered locally as a file (`--render`). Exporting the
tracklist as a **Spotify playlist** for friends stays on the backlog (B1, via
the Spotify MCP).

---

## Stack

| Concern | Library |
|---|---|
| Agent harness (Architect/Selector) | `dj.llm` `complete()` (Anthropic SDK; `claude-agent-sdk` optional later) |
| Tracing | `langfuse` via `dj/tracing.py` (no-ops without keys) |
| Beats · downbeats · structure | **`allin1`** (All-In-One Music Structure Analyzer) target; `librosa`-only fallback (no msaf) — see `ADR 0005` |
| Key → Camelot | `librosa` chroma + Krumhansl (`dj/audio/camelot.py`) |
| Loudness / energy (cross-track) | **`pyloudnorm`** (integrated + short-term LUFS) |
| Acoustic vibe embedding (general) | **CLAP** `laion/larger_clap_music`, 512-d, via `torch`+`transformers` |
| Taste embedding (personal) | **`sentence-transformers`** (`all-MiniLM-L6-v2`, 384-d) on my notes |
| Metadata tags | `mutagen` (file ID3 → cheap keyword filter) |
| Vector store | `pgvector` via `psycopg2` + Supabase (tracks + sections) |
| Track sources | `SourceProvider` seam: local folders + Spotify/YouTube links (`spotipy` + `yt-dlp`, needs `ffmpeg`) |
| Set export (manual mode) | stdlib `xml.etree` → rekordbox.xml + m3u8 |
| Mixer / rendering | `pyrubberband` + `scipy` + `soundfile` (time-stretch + EQ + write); stem separation later |

---

## Eval scorecard (DJ-quality has semi-objective oracles)

Put set quality **in CI**, mirroring the migration agent's rigor:

| Metric | What it measures |
|---|---|
| **BPM continuity** | max/avg tempo jump between adjacent sections (smaller = smoother) |
| **Harmonic-compat %** | % transitions that are Camelot-compatible |
| **Energy-arc RMSE** | distance between the set's actual LUFS curve and the requested arc |
| **Transition smoothness** | spectral/loudness discontinuity at the section mix points |
| **Taste-match score** | mean taste-vector similarity of the set to my labeled favorites |
| **Discovery ratio** | % of set that's non-favorite tracks I kept |
| **Set-acceptance rate** | % of proposed sets I approve at the HITL gate (north star #1) |
| **Blind listen test** | human A/B vs a naive recommender playlist (north star #2) |

The goal isn't "different from Spotify" — it's sets *I and my friends actually
want to hear*. Set-acceptance and the blind A/B are how we know we got there.

---

## HITL gate (mimic production, removable later)

One high-value gate (toggle via `HITL_LEVEL`): **approve the set before
rendering.** The agent proposes the tracklist + which sections it'll use + the
arc + transition plan; I approve (or nudge) before it spends time rendering
audio. This *is* the set-acceptance metric.

---

## Phases

Re-sequenced from the original plan: **the taste loop moves to the front**
(Phase 2) because a personal DJ that doesn't know my taste is just a worse
Spotify. Full phase detail and open questions live in `docs/phases.md`.

### Phase 0 — Scaffold ✅  ·  Phase 1 — Curator + Vibe DB ✅ (revision code landed)
Camelot wheel, project layout, config, tests (done). CLAP encoder, basic
`analyze`, `store`, `sources`, `metadata` (done). **Revision built + unit-tested**
(needs only the live ingest run) to match the new model:
- **Sections become first-class**: segment each track (`allin1`, librosa fallback) →
  `sections` table rows with label, time bounds, downbeat, per-section LUFS, and
  a per-section CLAP vector.
- **Beats/energy calibrated**: downbeat-derived BPM; integrated + short-term
  LUFS instead of per-track-normalized RMS (`ADR 0005`).
- **Schema v2**: `tracks` (acoustic vec + taste cols + global features) +
  `sections` (per-section vec + bounds + mix flags). See `docs/database.md`.
**Verify:** ingest a folder → `nearest_to_text("dreamy nocturnal")` returns
sensible *track* neighbors, and `nearest_section(...)` returns sensible
*sections*; print title/section/BPM/Camelot/LUFS/distance.

### Phase 2 — Taste loop 🎯 (in progress — code landed 2026-06-04)
*Concept (ask `tutor`): sentence embeddings, label propagation / active learning, hybrid scoring.*
*Built + unit-tested (no DB): `dj/taste/` (score, propagate, embed, tag CLI), taste columns, `store.ranked`. Pending: live ingest + `uv sync`. Detail in `docs/phases.md`.*
- **Ingest my real library** (a few hundred tracks first) — this also gut-checks
  whether CLAP neighbors feel right and tells us how heavy the taste layer must be.
- **Tagging UX** — low-friction CLI/TUI: load a track, show what we know, accept
  a note + rating (1–5) + role (warmup/peak/closer). A **vibe-tagging skill**
  can run this as a short interview and write structured output.
- **Taste vector** — embed the note (`sentence-transformers`) → `tracks.taste_vec`.
- **Label propagation (active learning)** — untagged tracks get a provisional
  taste vector from their tagged CLAP-neighbors; the agent surfaces the tracks
  where it's *most uncertain* (or where my taste diverges from acoustics) as the
  next ones worth labeling. **Tag ~150, not 2,000.**
- **Blended scoring** — `score = α·acoustic + β·taste + γ·rating`, tunable.
**Verify:** after tagging ~50 tracks, "more like the ones I love" returns
neighbors that match my taste, not just the acoustics; uncertain-track queue
surfaces sensible next labels. See `docs/taste.md`.

### Phase 3 — Architect + Selector ✅ (code landed; `ADR 0006`)
*Concept (ask `tutor`): agent harness, tool calling, generate→verify→revise loops.*
*Built + unit-tested offline: `arc.py`, `plan.py`, `critic.py`, `agents/` (tools,
architect, selector, hitl, generate). One planning flow + arc artifact + a
deterministic Critic verifier; `dj.llm` `complete()` behind an injectable seam;
deterministic fallbacks run with no API key. Detail: `docs/set-generation.md`.*
- Tools as an in-process SDK MCP server: `query_vibe_db`, `get_track_features`,
  `get_sections`, `check_harmonic_compat`, `propose_arc`, `score_transition`.
- **Architect**: vibe prompt ("2-hr sunset rooftop, deep→melodic, slow build") →
  a target energy (LUFS)/BPM arc over set position.
- **Selector**: a **generate → verify → revise loop** — propose an ordered set
  *with chosen sections and cue points*, run the deterministic Critic/evals,
  revise until it passes thresholds or hits budget. Blends taste + discovery,
  enforces Camelot/BPM, spaces artists.
- HITL gate: present the plan → approve before Phase 4 renders.
**Verify:** prompt → a coherent tracklist whose BPM/energy follow the arc and
whose taste-match beats a CLAP-only baseline (eval metrics, no audio yet).

### Phase 4 — Mixer (the wow) ✅ (code landed; `ADR 0007`)
*Built + unit-tested (pure transition planning; audio render is lazy/guarded):
`mixer.py`. Phrase-derived crossfades, time-stretch to the arc tempo, equal-power
crossfade + optional EQ bass-swap. Needs real audio + the `mixer` extra to render.*
- Cue to **section boundaries** (phrase-aligned via downbeats), not arbitrary
  offsets — so "mix out of A's outro into B's chorus" is literal.
- Beatmatch: time-stretch B to A's BPM (`pyrubberband`); align the downbeat grid.
- Crossfade + EQ swap; render a continuous mix to a file.
**Verify:** play the rendered mix — transitions land on the phrase, beatmatched,
using the *parts of each track* the Selector chose.

### Phase 5 — Eval scorecard + Critic + demo  · core ✅ (2026-06-04)
- ✅ The scorecard `evals/runner.py` — a **standalone dj-agent module reusing the
  Critic** (not an `agent-core EvalRunner`; there isn't one): taste-match,
  discovery ratio, and the Critic's BPM/harmonic/arc-RMSE metrics.
- ✅ A/B vs a CLAP-only baseline (`compare_to_acoustic_baseline`, `python -m
  dj.evals.runner "<brief>"`) — does the blended Selector win on taste-match?
- ✅ `--explain` narrates the arc, key moves, and section picks ("rides the drop of
  track 5 at the apex") — deterministic, in `agents/explain.py`.
- ✅ `persist.py` logs every set → the **set-acceptance** record.
- Remaining: audio-level transition smoothness (post-render), the blind A/B listen
  test, and a CI gate on regressions.

### Phase 6 — Memory (learned taste) + sharing
- Learn taste from accept/skip/replay on top of the labeled taste vectors —
  closing the loop so the agent gets more "me" over time. *(Substrate landed:
  `persist.py` logs sets; the learning policy is `docs/backlog.md` D1.)*
- Spotify-playlist export of approved tracklists to share with friends
  (`docs/backlog.md` B1 — needs your authorization). *(The playable-set export
  already ships: every approved set writes a rekordbox.xml + m3u8, `ADR 0010` —
  B1 is only the share-a-playlist piece.)*

---

## Quickstart (first session)
```bash
cd ~/dev/dj-agent
uv sync                      # installs from pyproject (see new deps in ADR 0005)
cp .env.example .env         # ANTHROPIC_API_KEY, DATABASE_URL (Supabase), LANGFUSE_*,
                             # SPOTIFY_CLIENT_ID/SECRET (only for Spotify-link ingestion)
# Phase 1/2: point the curator at a folder of audio and ingest…
uv run python -m dj.curator ~/Music/some-folder
# …or paste a link (Spotify/YouTube track/album/playlist → download + ingest):
uv run python -m dj.ingest "https://open.spotify.com/playlist/..."
```
Tell Claude Code: *"Building the DJ agent, plan in BUILD_PLAN.md, on Phase [X].
Check docs/phases.md for open
questions; use the `planner` subagent first."*
