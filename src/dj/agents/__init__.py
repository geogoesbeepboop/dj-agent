"""Phase 2 — the LLM agents (Claude Agent SDK).

These are agent-CENTRIC (the model drives), unlike the migration agent's
LangGraph workflow. They QUERY the vibe DB the Curator builds.

Planned:
- architect.py  — vibe prompt → target energy/BPM arc (a curve over set position)
- selector.py   — constraint search over the vibe DB to order tracks to the arc,
                  blending favorites + discovery; enforces BPM ramp + Camelot
                  compatibility (dj.audio.camelot.compatible/distance).

Both expose their tools (query_vibe_db, get_track_features, check_harmonic_compat)
to Claude via an in-process Claude Agent SDK MCP server. Not built yet — Phase 2.
"""
