# ADR 0012 — allin1 for real: CLI subprocess bridge and memory-linear neighborhood attention

**Status:** Accepted
**Date:** 2026-06-11
**Relates to:** `0004-structure-aware-sections.md`, `0005-calibrated-beats-and-energy.md`
(delivers its target detector), `0011-grid-truth-downbeat-phase-and-anchors.md` (the
hardened librosa path now actually becomes the *fallback* instead of the only path)

## Context

`allin1` has been the designated structure detector since `ADR 0005` — one pass
yields beats, downbeats, functional segment labels, and tempo, serving both the
section pipeline (`ADR 0004`) and the beat grid everything mixes on. It has
never run for real. Two independent blockers:

1. **It can't live in the project venv.** allin1's torch/NATTEN dependency pins
   fight the uv environment (Python-version pin conflicts; NATTEN wheels for
   the venv interpreter are painful on macOS), so it was never installed there
   and the librosa fallback carried every ingest to date.
2. **Installed elsewhere, it crashed the machine.** George installed allin1
   1.1.0 + NATTEN 0.21.6 under the python.org framework Python 3.14 — a
   separate interpreter entirely — and full-track analysis OOM-killed the
   16 GB M1 Pro: a hard system crash requiring a restart, not a dead process.

The crash had a precise root cause. allin1 1.1.0 targets NATTEN ≤ 0.17;
NATTEN 0.20+ replaced the functional API, and its only CPU backend is torch
Flex Attention, which **uncompiled materializes the full T×T attention scores
matrix**. Measured: **11.3 GB peak RSS for a 30-second clip**; a full 257 s
track exceeds 16 GB → system OOM. (demucs, the obvious heavyweight suspect,
was exonerated — it chunks internally.) The API migration also silently
dropped the checkpoint's trained relative positional bias (rpb), so even the
runs that survived were quietly degraded.

## Decision

**Run allin1 out-of-process as a cached CLI bridge from whatever Python it
actually installs in, map its Harmonix labels into our section vocabulary, and
replace its quadratic-memory NATTEN attention with a numerically-verified
memory-linear pure-torch implementation so a full track analyzes within
16 GB.**

Concretely, three decisions:

1. **CLI subprocess bridge** (`audio/segment.py`): `_segment_allin1` tries
   `import allin1` first; on `ImportError` it runs the `allin1` CLI — from any
   Python install on `PATH` — as a subprocess via `_segment_allin1_cli` and
   parses the JSON it writes. Config: `DJ_ALLIN1_BIN` (default `"allin1"`),
   `DJ_ALLIN1_CACHE` (default `~/.cache/dj-agent/allin1`), `DJ_ALLIN1_DEVICE`
   (default empty = allin1's own default). Results cache as one JSON per track
   stem; a cache hit never spawns the subprocess, so re-ingest is free. Any
   failure raises and `segment()` degrades to the librosa fallback exactly as
   before. `Structure.source` reports `"allin1-cli"` for provenance. The JSON
   parsing (`allin1_json_to_structure`) and the shared builder
   (`structure_from_allin1`) are pure and unit-tested. *Rejected alternative:*
   installing allin1 into the venv — the torch/NATTEN/py3.12 pin conflicts
   above, and no clean NATTEN wheel story for the venv interpreter on macOS.
2. **Harmonix label vocabulary mapping:** allin1 emits `start`/`end`/`inst`/
   `solo` beyond our `LABELS` vocabulary; previously they all flattened to
   `"verse"`. New `ALLIN1_LABEL_MAP`: `start→intro`, `end→outro`, `inst→break`
   (an instrumental bed is the ideal blend zone — mixable *and* loopable),
   `solo→bridge` (mixout-friendly). Known labels pass through; unknowns still
   degrade to `verse`.
3. **Memory-linear neighborhood attention** — the crash fix. Lives *outside*
   the repo (patched into allin1's site-packages `dinat.py`) but is vendored at
   `tools/allin1/dinat.py` with an install README. A pure-torch windowed
   implementation: each query gathers exactly its `kernel_size=5` neighbors,
   replicating NATTEN's edge semantics (the window *shifts* at boundaries, it
   never shrinks); dilation is handled by exact regrouping into at most two
   batched calls (groups differ by at most one in length — no padding, no
   masking). Memory is **O(T·k) instead of O(T²)**. Verified numerically
   against `natten.functional.na1d`/`na2d` to float32 epsilon at dilations
   1/2/4/8 (1D) and 5×5 (2D). Bonus: it restores the checkpoint's trained
   relative positional bias that the NATTEN 0.20+ functional API had dropped —
   an observable quality gain (a 30 s clip's BPM went from `None` to a real
   estimate).

Measured results after the fix: 30 s clip **2.07 GB** peak (was 11.3 GB); full
257 s track **5.2 GB** peak (was system OOM); BPM 133 + 12 sensible sections
on the test bachata track (Aventura — *Obsesión*).

## Rationale

- **A process boundary beats a shared interpreter.** allin1's dependency world
  and the venv's were never going to coexist; making the contract "JSON over a
  subprocess" instead of "importable module" sidesteps pip entirely. This is
  the same seam pattern every other heavy edge in the repo uses (yt-dlp,
  pyrubberband) — the venv never needs natten or a second torch.
- **The cache turns minutes into once-ever.** CPU analysis is minutes per
  track, but the JSON is deterministic per file; caching by track stem keeps
  the Curator's idempotence (`re-ingest upserts`) genuinely free on the
  heaviest stage.
- **Labels are mixing decisions, so flattening them threw away the point.**
  `inst` and `solo` are precisely the sections a DJ blends over; mapping them
  to `break`/`bridge` feeds `ADR 0011`'s mix-flag logic real information
  instead of defaulting everything to `verse`.
- **The attention math was always linear — only the backend was quadratic.**
  Windowed attention needs k = 5 neighbors per query; materializing T×T scores
  was an artifact of uncompiled Flex Attention, not of the model. Gathering
  exactly the window, with exact regrouping instead of padding/masking, keeps
  the computation identical — which is why it could be verified to float32
  epsilon against the NATTEN reference rather than merely "close enough."
- **Restoring rpb is correctness, not tuning.** The checkpoint was *trained*
  with that bias; an API migration that drops trained parameters is a silent
  quality regression. The `None`-BPM-to-real-BPM flip on the 30 s clip was the
  observable evidence.

## Trade-offs accepted

- **The site-packages patch is fragile** — a `pip install --force-reinstall`
  or upgrade of allin1 reverts it. Mitigation: the vendored copy + README at
  `tools/allin1/`; the cost is a manual reapply step after any reinstall, and
  the symptom of forgetting is the old OOM behavior.
- **The bridge depends on an interpreter the repo doesn't manage.** The only
  handle is `DJ_ALLIN1_BIN`; the CLI's JSON shape is an informal contract that
  can drift across allin1 versions. Provenance (`Structure.source =
  "allin1-cli"`) and the raise-then-fallback behavior bound the blast radius.
- **Per-track analysis is minutes on CPU.** Accepted because it's
  ingestion-time only and cached forever — the same call as `ADR 0002/0005`.
- **Bulk ingest is now the memory-heavy mode:** ~5 GB subprocess peak plus
  ~2 GB CLAP resident. Fine on 16 GB, but it's the number to remember before
  running ingest alongside anything else hungry.

## Consequences

- `audio/segment.py` gains `_segment_allin1_cli`, `allin1_json_to_structure`,
  `structure_from_allin1`, and `ALLIN1_LABEL_MAP`; `config` gains
  `DJ_ALLIN1_BIN` / `DJ_ALLIN1_CACHE` / `DJ_ALLIN1_DEVICE`. All new parsing
  and mapping logic is pure and covered by the fast suite — still offline.
- `tools/allin1/dinat.py` + README enter the repo as the canonical copy of the
  out-of-repo patch.
- **The librosa fallback finally is a fallback.** Everything `ADR 0011`
  hardened stays as the degradation path (and the analyzer for clips allin1
  fails on), not the everyday detector.
- Library rows segmented by the fallback upgrade to real allin1 structure on
  **re-ingest** (the Curator upserts and replaces sections), and the cache
  means doing so only pays the model cost once per track, ever.
- demucs is off the suspect list for future memory hunts; the attention layer
  was the whole story.

---

## ELI5 / what I learned

allin1 was always the plan, and it failed in two unrelated ways that taught me
two unrelated lessons. First, the packaging one: when two Python dependency
worlds refuse to share an interpreter, stop fighting pip and put a process
boundary between them — my code now just runs the `allin1` command from
*whatever* Python it managed to install into and reads the JSON it writes.
Subprocess-plus-JSON is the universal adapter. Second, the crash: 30 seconds
of audio ate 11 gigabytes and a full song hard-crashed my Mac. The math of
windowed attention is linear — each position only ever looks at its 5
neighbors — but the new backend's uncompiled path was *implemented*
quadratically, writing out the full every-position-versus-every-position score
matrix before throwing most of it away. I rewrote it to gather exactly the 5
neighbors each query needs, so memory scales with track length instead of
track length squared: same numbers to float32 precision, 5 GB instead of a
dead machine. The sneaky third lesson: the library upgrade hadn't just changed
the API, it silently dropped trained weights (the positional bias), so the
model still "worked" — just worse. The tell was a BPM coming back `None`.
Version drift doesn't always crash; sometimes it quietly deletes part of the
model. And since the patch lives in site-packages where any reinstall erases
it, I vendored a copy in the repo — never let the only copy of a fix live on
a whiteboard someone else can wipe.
