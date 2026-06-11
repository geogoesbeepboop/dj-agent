"""dj-agent: analyze a music library into a vibe vector DB and build real
beatmatched DJ sets with a planned energy arc.

Model access is inlined (dj.llm — tiered Anthropic; see agent-core ADR-0004 for
why the shared substrate was retired). The Architect/Selector run a hand-rolled
generate→verify→revise loop against a deterministic Critic (dj ADR-0006).
"""
