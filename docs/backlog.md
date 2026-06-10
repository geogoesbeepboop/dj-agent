# Backlog — needs your input, approval, or your ears

Items the 2026-06-04 multi-agent review surfaced that I deliberately did **not**
auto-apply, because each needs a decision from you, access to a tool/service, or
validation on real audio I can't hear. Everything I *could* safely do offline is
already done (see the change log at the bottom of `docs/phases.md`). IDs in
parentheses reference the review findings.

Grouped by what's blocking it. Tell me a letter+number (e.g. "do C1") and I'll
pick it up.

---

## A. Mixer DSP — needs your ears on a real render (highest impact on "does it sound good")

These are the most *audible* quality items, but I can't validate DSP without
hearing a rendered mix, and shipping unverified beat-alignment risks making
transitions worse. I made the docs honest about what the renderer does **today**
(tempo-glide, equal-power, phrase-length crossfades) so nothing overclaims; here's
what to build/validate together once you have audio:

- **A1 — True beat phase-lock + seam tempo match** (#1 / #2 / #3 / #19, *high*).
  Today `render_set` time-stretches each track to its **own** arc-position tempo
  and equal-power-crossfades the raw section tail/head. It does **not**
  sample-accurately align downbeats at the splice, and at a seam the two tracks
  can sit at slightly different tempos (≤ ~1.5 BPM on the default arc → tens of ms
  of kick drift). Fixing it right means: thread each section's downbeat times into
  the `Slot`/Mixer, align B's first downbeat to A's grid at the overlap, and hold a
  single shared tempo (or an intra-track tempo ramp) through the crossfade. This is
  real DSP that must be tuned by ear. **→ Let's do this together on your first renders.**
- **A2 — Real bass-swap / EQ mixing** (#9 / #20, *med*). The EQ swap is a one-sided,
  fixed-cutoff high-pass on the incoming track with a hard in/out at the splice;
  A's bass never ducks. A proper bass-swap ramps A's low end down as B's comes up.
  Also ear-tuned.

## B. Needs a tool/service or your authorization

- **B1 — Spotify playlist export** (#23, *med*). Export an approved tracklist to a
  Spotify playlist so friends can hear the selection (Phase 6 *sharing*). The
  capture side already uses the Spotify MCP; export would resolve each slot to a
  Spotify track (ISRC → artist+title search fallback) and call `create_playlist`.
  **→ This publishes to your account — I need you to OK it and confirm the connected
  Spotify MCP has `create_playlist` + write scope.** I can build the pure resolver
  behind a seam now and leave the live publish gated, if you want it staged.

## C. Product / design decisions (your taste)

- **C1 — Extended harmonic mixing** (#17, *med*). I added the richer harmonic
  primitives (`camelot.energy_boost`, `camelot.grade`, a smoother `distance`) but
  kept the **hard** key gate strict — that's the documented invariant. Letting
  energy-boost (+1 semitone) and diagonal moves through the gate *with a penalty*
  would unlock more transitions and deliberate energy lifts. That's a taste call on
  how adventurous you want the mixing. **→ Say the word and I'll wire `grade ≤ 1` as
  compatible-with-penalty across the Selector + Critic.**
- **C2 — Multi-user "we" taste** (#43, *med*). The endgoal is sets for you **and**
  friends, but the model has exactly one taste owner. Friends means a real schema +
  scoring change (per-owner taste keyed by `(track, owner)`, blend a chosen owner or
  a union at query time). **→ Want this now — and how should multiple tastes combine
  (intersection / union / weighted-by-who's-there)?**

## D. Buildable offline now — deferred only for sequencing (confirm priority and I'll do them)

- **D1 — Phase 6 memory loop** (#22). Persistence + the set-history log now exist
  (`dj/persist.py`); the missing piece is *learning* from accept/skip/replay to
  nudge taste vectors / blend weights. Substrate is in; the learning **policy** is
  the work.
- **D2 — Section-pair transition scoring + dual cue points** (#16). Score A's
  *mix-out section* vibe → B's *mix-in section* vibe (via the section vectors), and
  let a slot carry distinct mix-in and mix-out sections, so "outro of A into intro
  of B" is literal. Bigger Selector/Mixer change; the **energy** half is already
  done (the Critic now scores the played section's LUFS).
- **D3 — Section-aware greedy ordering** (#8). Order on each track's best-fitting
  section energy, not the whole-track LUFS. The final plan already *uses* section
  energy after assignment; this would align the *ordering* too (costs N section
  lookups at pool time).
- **D4 — Per-section key detection**. `sections.camelot` is always NULL today
  (only the track key is computed). Detecting per-section keys enables harmonic
  mixing on partial tracks and pairs with D2 and C1.
- **D5 — HITL iterate / nudge** (#42). Make the approval gate interactive: re-roll
  the order, swap a slot, "lighter/harder", pin/exclude — instead of binary y/N.
- **D6 — Energy pacing within a level** (#41). "No two bangers back to back" beyond
  raw distance-to-arc — model local energy contour, not just the target.
- **D7 — DB connection hygiene** (review *rejected* the "exhausts Supabase"
  severity, so it's not urgent). `with _connect() as conn` commits/rolls back but
  doesn't *close* the psycopg2 connection; a shared `closing(...)` helper across
  `store`/`pending` would be tidier for the all-day Curator.

---

## Already shipped from this review (for reference)

Auto-applied because they were safe, offline-verifiable correctness/quality wins —
full list in `docs/phases.md` → "2026-06-04 review pass". Highlights: the
section-energy arc-scoring fix, the blend-weights passthrough, the Phase 5 eval
scorecard + `--explain` + plan persistence + recently-played dedup, the
DnB-tempo / first-section-merge / `bridge`-label / arc-clamp / short-term-LUFS /
empty-audio-guard bug fixes, windowed artist spacing + key-monotony signals, the
richer Camelot primitives, robust mix normalization, parked-review dedup, and
real propagation confidence.
