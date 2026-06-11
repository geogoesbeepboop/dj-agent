# ADR 0009 — Link ingestion via yt-dlp (metadata first, FLAC into the library)

**Status:** Accepted
**Date:** 2026-06-10
**Complements:** `0008-spotify-taste-capture.md`

## Context

Streaming is excluded from ingestion (beatmatching needs the raw file), so until
now the only way into the vibe DB was a local folder — every track gated on me
buying or hunting down a file, dropping it somewhere, and running the Curator.
That throttles the library to the speed of my shopping, not my listening. It
also strands `ADR 0008`: parked reviews only auto-apply *when the file arrives*,
and files weren't arriving.

But my listening already produces links. A Spotify playlist, a YouTube rip a
friend sends, an album page — each one names exactly the tracks I want, with
real catalog metadata behind it. What's missing is the bridge from "pasted
link" to "tagged local file the Curator can ingest." This is a personal,
non-commercial library for me + friends — the acquisition path is judged on
that basis, not as a product feature.

Forces on the design: the wrong *cut* (live, sped-up, extended) poisons BPM,
sections, and the vibe vector alike, so matching must be conservative; the
taste loop needs the ISRC on the file (`ADR 0008`'s confident-match key); and
the whole pipeline must stay testable offline like everything else in the repo.

## Decision

**Ingest from pasted Spotify/YouTube links: resolve metadata first (spotipy
for Spotify, `yt-dlp -J` for YouTube), then download audio via yt-dlp to FLAC
into `DJ_LIBRARY_DIR`, stamping Spotify's own metadata — including the ISRC —
onto the file.** New package `src/dj/ingest/`, four stages:

1. **Classify** (`links.classify`, pure URL parsing): a pasted link →
   `Link(service, kind, id)`. Spotify track/album/playlist/artist
   (open.spotify.com incl. `/intl-xx/` and `?si=`, plus `spotify:` URIs);
   YouTube watch/youtu.be/shorts/playlist incl. music.youtube.com; scheme-less
   pastes get `https://` prepended. One deliberate rule: `watch?v=X&list=Y` is
   the *single video* — the playlist param is just share-link decoration.
   Anything else fails fast naming what is supported.
2. **Resolve** (`resolve.resolve`): a Link → `TrackRequest`s — **metadata only,
   no audio**, so a playlist expands to a reviewable list of titles before any
   download. Spotify uses spotipy with client-credentials (free app keys in
   `.env`, no browser auth — public catalog metadata only: artist/title/album/
   duration/**ISRC**; albums re-fetch full track objects in batches of 50
   because the simplified objects omit ISRC; playlists paginate; deleted/local
   items are skipped with a summary line). YouTube shells out to
   `yt-dlp -J` / `--flat-playlist`; artist/title come from splitting
   "Artist - Title" with decoration stripping ("(Official Video)", "[4K]" go;
   "(feat. X)" and remix names stay), uploader as fallback artist with
   " - Topic" stripped.
3. **Fetch** (`download.fetch`): YouTube-sourced requests download their
   `video_url` directly (`yt-dlp -x --audio-format flac --audio-quality 0`,
   final path read via `--print after_move:filepath`). Spotify-sourced requests
   have no audio, so they run `ytsearch5:"artist title"` and `pick_best`
   scores the results: **duration proximity to the catalog duration dominates**
   (>25 s off is penalized beyond what any bonus recovers), with a " - Topic"/
   official-audio bonus and live/sped-up/cover penalties — suppressed when my
   own request's title carries the token (asking for "X (Live)" means live IS
   the target). After download, mutagen stamps artist/title/album/**ISRC** onto
   the file exactly where `metadata.read_tags()` reads them. Downloads are
   **idempotent**: filenames end in `[<video id>]` and an existing id
   short-circuits the fetch, so re-pasting a playlist only pulls what's new.
4. **Curate** (`provider.LinkProvider`, `source_id="link"`): yields a
   `TrackRef` per downloaded file into the unchanged segment/analyze/embed
   pipeline; per-track failures print and continue, so a 40-track playlist
   never dies on entry 3. Files land in `DJ_LIBRARY_DIR/<source>/`.

CLIs: `python -m dj.ingest <url> [--favorites]`, and `python -m dj.curator` now
accepts a URL anywhere it took a folder. yt-dlp runs as
`[sys.executable, -m, yt_dlp]` — a declared dep of this interpreter, no PATH
lookup; ffmpeg is required for the FLAC extraction. Both externals sit behind
injectable seams (`runner=` for yt-dlp, `sp=` for spotipy) with lazy imports,
so the fast suite tests the whole pipeline offline.

**spotdl was rejected.** The installed v3 CLI is the legacy broken one; v4
relies on bundled shared credentials that churn; and most decisively, I want to
*own the match* — the duration-dominant scoring above — and keep official
Spotify metadata with ISRC flowing onto the file, because the taste loop
depends on it.

## Rationale

- **Metadata before audio.** Expanding a link to named tracks is cheap and
  reviewable; downloading is slow and irreversible-ish. Splitting resolve from
  fetch means consumers can show or reuse *titles* without audio (the judge CLI
  is exactly that), and the catalog duration exists before the
  search needs it for scoring.
- **Duration dominates the match** because it's the one signal that separates
  the studio recording from live/extended/sped-up cuts — the failure mode that
  silently corrupts every downstream representation (`ADR 0004/0005`).
  Title-token heuristics are bonuses and penalties around it, not the core.
- **Stamping ISRC closes the ADR 0008 loop.** A rip's embedded metadata names
  the *video*; overwriting it with Spotify's catalog identity means a parked
  review matches at the confident ISRC tier the moment the Curator ingests the
  file — capture-while-listening to tagged-in-library with no manual step.
- **Idempotence matches the Curator.** Skip-by-video-id on disk plus upsert in
  the DB means re-pasting a growing playlist is the natural sync gesture.
- **Owning the seams keeps the suite fast.** Fake runners and a fake spotipy
  client exercise classify/resolve/score/idempotence logic with zero network,
  consistent with every other heavy edge in the repo.

## Trade-offs accepted

- **Audio quality ceiling is YouTube's** — roughly 128–160 kbps opus,
  re-encoded into a FLAC *container* (lossless wrapper, lossy source). Fine for
  listening, AI analysis, and practice sets; anything precious still gets
  bought as a real file.
- **Artist/title from YouTube video titles is heuristic.** Separator splitting
  + decoration stripping covers the common shapes; oddly-titled uploads will
  mis-split and need a tag fix.
- **YouTube-only tracks have no ISRC**, so their parked reviews fall back to
  name+duration matching — ambiguous cases stay parked for a one-line manual
  `--apply` (`ADR 0008`'s conservative tier, one level weaker).
- **New external surface.** spotipy + yt-dlp become core deps and ffmpeg a
  system requirement; yt-dlp chases YouTube's churn, which is exactly why it's
  a maintained dep rather than logic I own. Acceptable for a personal,
  non-commercial library.
- **`pick_best` can still be wrong** on sparse result pages (it prefers the
  least-bad option over failing). Listening is the backstop; a bad rip gets
  deleted and re-fetched.

## Consequences

- New `src/dj/ingest/` (`links` / `resolve` / `download` / `provider` /
  `__main__`); `curator` accepts URLs. `config.settings` gains `library_dir` +
  `spotify_client_id/secret`; `.env.example` documents the free
  developer.spotify.com app; `.gitignore` adds `library/` and `*.m4a`.
- **The library grows from a pasted link.** Paste a playlist → resolve → FLAC
  files in `DJ_LIBRARY_DIR/<source>/` → full segment/analyze/embed ingest, with
  `--favorites` flowing through like any folder ingest.
- **ADR 0008 parked reviews now auto-apply confidently** on the Spotify path,
  because the stamped ISRC is on the file at ingest. `dj.taste.judge` builds on
  this: judge a whole playlist by link with no download, and the reviews drain
  themselves when the audio later arrives through this pipeline.
- Fast suite grows 103 → 197 tests, all offline (fake runners/spotipy/store);
  pure logic (classify, title splitting, `pick_best`, idempotence keys) is
  unit-tested directly.
- Unchanged: streaming itself is still excluded from ingestion — this ADR
  acquires *files*; the no-raw-audio-no-ingest invariant stands.

## ELI5 / what I learned

My DJ agent could only learn from songs I physically had as files, so its
library grew at the speed of my record shopping, not my listening. Now I just
paste a Spotify or YouTube link. The key design insight: split "figure out what
the tracks are" from "go get the audio." First I ask Spotify (or YouTube) for
the official metadata — names, durations, and the ISRC, which is like a
barcode for a specific recording. Then yt-dlp fetches the audio from YouTube,
and when I started from Spotify, the system picks the right upload mostly by
*duration* — because a live or sped-up version is the silent killer that
corrupts the BPM, the sections, and the vibe vector all at once. Finally it
stamps the official metadata, barcode included, onto the file — so a review I
left weeks ago while listening on Spotify attaches itself automatically the
moment the file lands. I rejected spotdl because the version I had was broken,
the new one borrows shared credentials that keep breaking, and honestly I
wanted to own the matching logic myself — that's where the quality lives. The
honest cost: the audio is YouTube-quality in a lossless wrapper, and YouTube
titles parse imperfectly. For a personal library that feeds an AI, that's the
right trade.
