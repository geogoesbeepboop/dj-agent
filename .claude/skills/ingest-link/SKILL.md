---
name: ingest-link
description: Ingest a pasted Spotify/YouTube link (track/album/playlist/artist) into the vibe DB — resolve, download via yt-dlp, analyze, embed (ADR 0009). Use when George pastes a music link to add to the library, or says "ingest this", "add these", "grab this playlist".
---

You turn a pasted link into ingested, embedded library tracks with one command.

## Preflight (fast, do silently)

- `DATABASE_URL` must be set in `.env` (it is, normally) — without it the Curator
  refuses.
- **Spotify links** also need `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` in
  `.env`. If they're missing, say so and offer the YouTube equivalent instead
  (YouTube needs zero setup).
- **Spotify-owned editorial/algorithmic playlists** (ids starting `37i9dQZF1DX`,
  Discover Weekly, Release Radar) 404 for new API apps. If the link looks
  editorial, warn first and suggest George save the tracks to his own playlist
  and paste that.

## Run

```bash
uv run python -m dj.ingest "<url>"              # add --favorites to mark all as favorites
```

- Playlists are long-running (download + segment + CLAP per track; the first ever
  run also downloads ~1.8 GB of CLAP weights). Run it in the background and
  relay progress — the output prints `ingested (i/N)` per track and
  `FAILED <name>: <reason>` for casualties.
- Re-pasting the same link is safe: downloads are idempotent (finished FLACs
  short-circuit), and the Curator upserts.
- Scheme-less pastes (`open.spotify.com/...`) work; `python -m dj.curator <url>`
  is the same thing.

## After

- Report: ingested X/N, name the failures and why (dead video, no YouTube match).
- If parked taste reviews auto-applied (the output says `applied parked review`),
  mention it — that's the judge→ingest loop closing.
- Suggest the natural next step: `/bulk-judge` the same playlist if untagged, or
  `/make-set` if they ingested for a specific set.
