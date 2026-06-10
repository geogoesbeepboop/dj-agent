# ADR 0008 — Capture taste before you own the file (parked reviews)

**Status:** Accepted
**Date:** 2026-06-04
**Extends:** `0003-personal-taste-layer.md`

## Context

The taste layer (`ADR 0003`) is what makes this *my* DJ. But the only way to feed
it is `dj.taste.tag`, which **requires the track to already be in the vibe DB** —
and ingestion requires the **raw audio file** (streaming is excluded; beatmatching
needs the file). So capturing taste is gated behind owning + ingesting the file.

The problem: the strongest taste signal happens *while listening* — and I mostly
listen on Spotify, with no file in hand. "Hands-in-the-air, perfect 3am closer" is
a thought I have in the moment and lose by the time I've bought the track, dropped
it in a folder, and run the Curator. The friction kills the loop ADR 0003 depends
on (tag ~150 favorites). Tagging-against-a-path is a data-entry chore divorced
from the listening moment.

We want to leave a review the instant we feel it, with no file, and have it apply
itself later *if/when* the track enters the library — no second pass.

## Decision

Add a **pending-review store** that decouples taste **capture** from **ingest**:

1. **Capture anywhere, no file needed.** A review is `(artist, title, note,
   rating?, role?)` plus whatever identity we can grab (`isrc`, `spotify_id`,
   `duration_s`). Two front-ends, both routed through the agent so they share one
   sink (`agents/tools.save_review` → `taste/pending.add`):
   - **"review what's playing"** — the agent reads the Spotify MCP
     `get_currently_playing` for identity, then saves (`source='spotify_now'`).
   - **conversational chat** — the agent parses "save a review for X: …" and saves
     (`source='chat'`). This path has **no Spotify dependency**, so it survives any
     change to the Spotify MCP.
   A plain CLI (`dj.taste.review`) covers manual entry + the parked-review inbox.
2. **One store, same Postgres.** A `pending_taste` table beside `tracks` — not a
   second database (reaffirms `ADR 0001/0003`: separation lives in tables/columns).
   The note is stored as **raw text** and embedded at apply-time, so the store
   stays human-readable/editable and re-embeds free if the taste model changes.
3. **The Curator drains it at ingest.** After upserting a track, the Curator looks
   for a parked review matching the track's identity and, on a **confident** match,
   writes the note into `tracks.taste_*` exactly as `dj.taste.tag` would
   (`set_taste`, `taste_source='manual'`) — **no extra input** — then marks the
   review applied (so re-ingest won't re-fire; idempotent like the rest).
4. **Confident-only auto-apply; ISRC-first matching.** Match tiers:
   `ISRC` → unique normalized `(artist, title)` + duration within ±5 s → confident.
   Several name hits, or a duration mismatch → **ambiguous**: left parked and
   surfaced for a one-line manual resolve (`--apply <id> <path>`). Never silently
   tag the wrong recording (live vs studio, remix vs original).

## Rationale

- **Capture at the moment of feeling.** The whole taste loop hinges on actually
  leaving notes; removing the file-first gate is the difference between a loop that
  runs and one that doesn't.
- **ISRC is the right key.** It identifies the *recording*, robust to title
  punctuation/qualifiers. Name+duration is the always-available fallback; together
  they cover the common cases without a fingerprinting dependency (we have no
  Spotify audio to fingerprint anyway).
- **Conservative by default.** A wrong auto-apply silently poisons the taste layer;
  parking the ambiguous cases costs one confirmation and protects the signal.
- **Reuses the existing sink.** A parked review lands through `set_taste`, identical
  to a hand-typed tag — propagation, blending, and the Selector need no changes.

## Trade-offs accepted

- **Fuzzy name matching is imperfect.** `&`-joined collabs can miss; aggressive
  qualifier-stripping could, in rare cases, collide two different tracks. ISRC +
  duration + the manual resolve are the guards; thresholds start strict and tune
  on real data.
- **`get_currently_playing` may not surface ISRC.** If the MCP omits
  `external_ids.isrc`, capture falls back to name+duration (and can recover ISRC
  via a `search()`); matching still works, just one tier weaker.
- **Spotify becomes a taste *input*, not only Phase-6 export.** A small scope
  expansion, justified: it's where the listening (and the feeling) happens.

## Consequences

- `vibe/schema.sql`: new additive `pending_taste` table + status/isrc indexes.
- New `taste/pending.py` (pure normalization + `find_match` tiers; DB-guarded
  `add`/`list_pending`/`match`/`apply_review`/`apply_manual`) and `taste/review.py`
  (CLI). `metadata.py` gains best-effort `isrc` extraction. `curator.py` drains
  pending reviews after upsert (with a never-clobber-my-manual-label guard).
  `agents/tools.py` exposes `save_review` / `list_pending_reviews`.
- `.claude/skills/vibe-review/` — the chat front-end recipe.
- Pure matching is unit-tested (`tests/test_pending.py`); the DB/model/Spotify
  edges stay lazy + guarded, so the fast suite still runs with no DB/model/audio.
