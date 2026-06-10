---
name: vibe-review
description: Park a taste review for a track I don't own yet (Spotify now-playing or in chat), so it auto-applies when I later ingest the file. Use when I say "review this", "save a review for …", or tell you how a track makes me feel. See ADR 0008.
---

You capture how George feels about a track **before** the file is in his library,
and park it so the Curator applies it automatically on the matching ingest — no
second pass. This is the low-friction front of the taste loop (`docs/taste.md`,
ADR 0003/0008). The note is the rich part; rating (1–5) and role
(`warmup`/`build`/`peak`/`closer`/`tool`/`wildcard`) are cheap optional filters.

## Two flows, one sink

Everything lands via `agents.tools.save_review(...)` → `taste/pending.py`. Run it
with: `uv run python -c "from dj.agents.tools import save_review; print(save_review(...))"`.

### A. "Review what's playing"
Triggered by "review this", "review what's playing", "tag this one".
1. Call the Spotify MCP `get_currently_playing`.
2. Pull `artist`, `title`, and — if present — `external_ids.isrc`, track `id`
   (`spotify_id`), album, and `duration_ms` (÷1000 → `duration_s`). ISRC is the
   gold match key; include it whenever the MCP exposes it. If the MCP omits ISRC,
   proceed with name + duration (optionally `search()` the track to recover ISRC).
3. Confirm the note/rating/role from what George said (ask only if a note is
   missing — a review needs a note).
4. `save_review(artist, title, note, isrc=…, spotify_id=…, duration_s=…,
   rating=…, role=…, source="spotify_now")`.

### B. Conversational (no Spotify needed — the resilient fallback)
Triggered by "save a review for <artist> – <title>: <note>, <rating>, <role>".
1. Parse artist, title, note, and any rating/role from the sentence.
2. `save_review(artist, title, note, rating=…, role=…, source="chat")`.

## After saving
- Confirm: "Parked review #N for <artist> – <title> — it'll apply itself when you
  ingest that track."
- "What's on my want-list?" / "show parked reviews" → `list_pending_reviews()`
  (these are tracks George already loves but doesn't own yet — a shopping list).

## Notes
- Don't invent identity fields. Pass only what you actually have; the matcher
  degrades gracefully (ISRC → name+duration).
- Keep George's wording in the note verbatim — idiosyncratic phrasing is the
  signal the taste vector captures.
- Matching/auto-apply happens later in the Curator; you only capture here.
