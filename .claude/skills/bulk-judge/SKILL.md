---
name: bulk-judge
description: Judge a pasted playlist link track-by-track in chat — taste notes/ratings with no downloading; known tracks tag directly, unknown ones park and auto-apply at ingest (ADR 0008/0009). Use when George pastes a playlist to "judge / rate / review these" or wants to bulk-feed his taste.
---

A playlist George knows is dozens of taste judgments waiting to happen, and none
need the audio. You resolve the tracklist (metadata only) and capture how each
track makes him feel. Single-track flow lives in `/vibe-review`; this is the
bulk version.

## Resolve the tracklist (no download)

```bash
uv run python -c "
from dj.ingest.links import classify
from dj.ingest.resolve import resolve
for i, r in enumerate(resolve(classify('<url>')), 1):
    print(f'{i}. {r.display}')
"
```

Spotify links need `SPOTIFY_CLIENT_ID/SECRET`; editorial playlists (`37i9dQZF1DX…`)
404 — same caveats as `/ingest-link`. For big playlists show ~10 at a time, or
let George cherry-pick numbers.

## Capture judgments

Walk the tracks conversationally. For each one George judges (note required;
rating 1–5 and role `warmup/build/peak/closer/tool/wildcard` optional), route it
exactly like `dj.taste.judge` does:

```bash
uv run python -c "
from dj.ingest.links import classify
from dj.ingest.resolve import resolve
from dj.vibe import store
from dj.taste import pending, embed
req = resolve(classify('<url>'))[<i-1>]          # or build identity from the list above
path = store.find_track_by_meta(req.artist, req.title)
if path:
    store.set_taste(path, '<note>', embed.embed_note('<note>'), rating=<r|None>, role=<role|None>)
    print('tagged in library:', path)
else:
    rid = pending.add(req.artist, req.title, '<note>', isrc=req.isrc, spotify_id=req.spotify_id,
                      album=req.album, duration_s=req.duration_s, rating=<r|None>, role=<role|None>,
                      source='bulk')
    print('parked review #', rid)
"
```

Batch several judgments into one `python -c` when George answers in bulk — one
process per track is wasteful.

- Keep George's wording **verbatim** in the note — idiosyncratic phrasing is the
  signal the taste vector captures.
- Never invent identity fields; `pending.add` is idempotent, so re-judging a
  track just updates the note.

## Alternative + after

- If George prefers the terminal: `uv run python -m dj.taste.judge "<url>"` is
  the same loop as an interactive CLI.
- Finish with the counts (tagged directly / parked / skipped) and the reminder:
  parked reviews auto-apply when he ingests those files (`/ingest-link` the same
  playlist makes the match confident via stamped tags). Suggest
  `uv run python -m dj.taste.tag --propagate` once manual labels grew.
