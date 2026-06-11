---
name: make-set
description: Generate a DJ set from a vibe brief ("dreamy nostalgic bedroom vibes, 60 min") → plan → George approves in chat → rekordbox.xml + m3u8, optionally a rendered mix (ADR 0010). Use when George asks for a set, mix, or "make me a playlist that feels like …".
---

You drive `dj.agents.generate` and run the approval gate **in chat** — never via
the CLI's own stdin prompt.

## The one mechanic that matters

`hitl.confirm()` prompts on stdin; when you run it from Bash there is no TTY, so
EOF **auto-rejects** and nothing exports. Therefore ALWAYS run with the gate off
and do the approval yourself in conversation:

```bash
HITL_LEVEL=none uv run python -m dj.agents.generate "<brief>" --minutes <N> --offline --explain
```

- `--offline` = deterministic arc + greedy selector, no API key, instant. Use it
  by default; use the live model (drop `--offline`) when George asks for a
  smarter selection and `ANTHROPIC_API_KEY` is set.
- The command always writes manual mode on approval: `renders/<arc>.rekordbox.xml`
  + `.m3u8` (with `HITL_LEVEL=none` approval is automatic, so they're written
  every run — reruns overwrite, which is what you want while iterating).

## Flow

1. Run it. Show George the **SET PLAN** block + the `--explain` narration, plus
   the verdict line (harmonic %, max BPM jump, arc RMSE).
2. Ask: keep, or adjust? (brief wording, `--minutes`, `--tracks`.) Iterate —
   **add `--allow-repeats` to reruns**, otherwise the recently-played dedup
   excludes the tracks the previous run just logged and the set mutates under him.
3. On "keep": point at the artifacts —
   - **Manual mode**: import the XML in rekordbox (Preferences → Advanced →
     Database → rekordbox xml), drag the `dj-agent — <arc>` playlist in. Cues,
     keys, BPM, order are pre-set; no beat grid is exported (rekordbox analyzes).
   - **Automatic mode**: rerun with `--render --allow-repeats` for the continuous
     beatmatched file (needs the `mixer` extra + `brew install rubberband`;
     transitions aren't yet phase-locked — backlog A1).
4. If the selector returns nothing: the library is thin for that BPM band/vibe —
   suggest `/ingest-link` to feed it.

## Quality nudge

After a few sets, suggest the A/B: `uv run python -m dj.evals.runner "<brief>"`
(taste-blend vs CLAP-only — the number that says the taste loop is working).
