# Your TODO — the live edges only you can run

Everything through Phase 4 plus the Phase 5 scorecard/`--explain`/persistence —
and now **link ingestion** (`ADR 0009`) and the **rekordbox/m3u8 manual-mode
export** (`ADR 0010`) — is **built and unit-tested offline** (197 fast tests).
What's left needs your machine, your library, your accounts, or your ears.
Work top-to-bottom.

## One-time setup

- [ ] **`uv sync`** — base deps (now includes `yt-dlp` + `spotipy` for link
      ingestion). Add extras as you need them:
      `uv sync --extra segment` (allin1 detector; the librosa fallback runs without it),
      `uv sync --extra mixer` (time-stretch + EQ render),
      `uv sync --extra agents` (only if you wrap the toolbelt in the Claude Agent SDK).
- [ ] **`brew install ffmpeg rubberband`** — `ffmpeg` is what yt-dlp uses to
      extract downloads to FLAC (link ingestion fails without it); `rubberband`
      is the CLI `pyrubberband` shells out to for beat-matching time-stretch
      (without it the Mixer plays at native tempo, no crash).
- [ ] **Supabase / Postgres**: create the project, **enable the `vector` extension**
      (Dashboard → Database → Extensions, or it's created by `schema.sql` if your role
      may create extensions), put the connection string in `.env` as `DATABASE_URL`.
- [ ] **`cp .env.example .env`** and fill it in. `ANTHROPIC_API_KEY` is only needed
      for **live** (non-`--offline`) set generation; ingest/tag/eval never call the LLM.
      **`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`** — only needed to paste
      *Spotify* links: create a free app at developer.spotify.com/dashboard
      (client-credentials flow, never opens a browser, reads catalog metadata
      only — the audio still comes from YouTube). **YouTube links need zero setup.**
- [ ] Apply the schema: `psql "$DATABASE_URL" -f src/dj/vibe/schema.sql`
      (or just run the Curator once — it self-applies).

## First real run — link-first (Phases 1–2, `ADR 0009`)

- [ ] **Paste a playlist**: `uv run python -m dj.ingest "<spotify-or-youtube-playlist-url>"`
      — downloads every track to `./library/<source>/` as FLAC and curates it
      (segment + analyze + embed) exactly like a local file. A folder still
      works the old way, and `python -m dj.curator` now accepts a URL anywhere
      it took a folder. Re-pasting is idempotent (downloads skip by video id;
      the Curator upserts). Honest caveats: audio quality tops out at YouTube's
      (~128–160 kbps opus in a FLAC container) — fine for listening and AI
      analysis, buy files for anything precious; Spotify-sourced tracks are
      matched on YouTube by duration-dominant scoring then stamped with the
      catalog's artist/title/album/**ISRC**, while YouTube-only tracks get a
      *heuristic* artist/title split from the video title — spot-check a few.
- [ ] **Bulk-judge a playlist you know well**: `uv run python -m dj.taste.judge "<url>"`
      — a note + rating + role per track, **no downloading**. Tracks already in
      the library are tagged in place; unknown tracks park as pending reviews
      (`ADR 0008`) that auto-apply the moment the file is later ingested — the
      ISRC stamped by the Spotify ingest path makes that match confident.
      YouTube-only tracks have no ISRC, so they match by name+duration;
      ambiguous ones need a manual `python -m dj.taste.review --apply`.
- [ ] **Sanity-check retrieval**: in a REPL, `from dj.vibe import store;
      store.nearest_to_text("dreamy nocturnal")` → do the neighbors feel right?
      `store.nearest_section(...)` → sensible *parts*?
- [ ] **Tag ~50 favorites**: `uv run python -m dj.taste.tag` (walks the
      active-learning queue), then `uv run python -m dj.taste.tag --propagate`.
      Smoke-test `--queue` surfaces sensible next labels.

## Generate, perform, render (Phases 3–5, `ADR 0010`)

- [ ] **Generate a set** (offline first):
      `uv run python -m dj.agents.generate "dreamy and nostalgic, bedroom set vibes" --offline --explain`
      — track count is derived from `--minutes` (~3.5 min/track) unless you pass
      `--tracks`; `--explain` narrates the arc + section picks; every run is logged to
      `renders/set_history.jsonl` and recently-played tracks are skipped (`--allow-repeats`
      to override).
- [ ] **Import the XML into rekordbox and play the set yourself** (manual mode).
      Every approved set now writes `<arc>.rekordbox.xml` + `.m3u8` to
      `DJ_OUTPUT_DIR` automatically (`--rekordbox <path>` moves the XML). In
      rekordbox: Preferences → Advanced → Database → enable **rekordbox xml** →
      point it at the file → drag the "dj-agent — <arc>" playlist in. Order,
      key, BPM, and the planned **MIX IN/OUT cues** (hot cues + memory cues at
      the chosen section's bounds) are pre-set — you perform the transitions.
      Honest caveat: we don't export a beat grid (the XML omits the TEMPO
      element on purpose — an anchor we can't place correctly is worse than
      none), so rekordbox analyzes the grid on import; the cue marks are
      wall-clock seconds, so they land correctly regardless (backlog **E2**
      would export a real anchor).
- [ ] **Render + LISTEN** (automatic mode): re-run generate with `--render`
      (needs the `mixer` extra + rubberband) — one continuous beatmatched file.
- [ ] **Eval A/B** (after ~50 tags): `uv run python -m dj.evals.runner "<same brief>"`
      — confirm the blended set's **taste-match beats the CLAP-only baseline**
      (the Phase 3 goal). Early on (few tags) the delta may be ~0; it should
      grow as you tag more.

## Validate — needs your ears (I couldn't hear any of this)

- [ ] **Beat phase-lock** on the first render: are transitions actually beat-aligned?
      The renderer does tempo-glide equal-power crossfades but does **not** yet
      sample-accurately phase-lock downbeats or match seam tempo. If transitions sound
      off-grid, that's expected → prioritize **backlog A1** and we'll tune it together.
- [ ] **Section labels**: do the detected `drop`/`break`/`intro`/`bridge` labels match
      reality on your tracks? If the librosa fallback mislabels, that tells us whether
      `allin1` is worth fighting to install.
- [ ] **Spotify→YouTube match quality**: play a few Spotify-sourced downloads —
      did the duration-dominant scorer pick the studio cut (not a live/sped-up/
      cover version)? Misses tell us how to reweight `download.pick_best`.

## Tune on real data

- [ ] **Blend weights** α/β/γ (`DJ_W_ACOUSTIC` / `DJ_W_TASTE` / `DJ_W_RATING`) — lean
      taste-heavier as your label count grows.
- [ ] **Greedy cost weights + Critic thresholds** (`src/dj/agents/selector.py`,
      `src/dj/critic.py`) — once you've seen a few sets.
- [ ] **Tempo band** if your library has unusual tempos (the octave fold now keeps
      DnB/footwork at full tempo, but check it on your edge cases).

## Decisions I'm waiting on (see `docs/backlog.md`)

- [ ] **B1** — authorize Spotify playlist **export** (publishes to your account;
      this is the *sharing* direction — not what `ADR 0009` built, which is ingestion).
- [ ] **C1** — allow extended (energy-boost) harmonic moves through the key gate?
- [ ] **C2** — when/how to add friends' taste ("we").
- [ ] **Security** — rotate any real secret that's been in `.env` if this repo or
      machine is shared (`.env` is gitignored and untracked — no leak found).
