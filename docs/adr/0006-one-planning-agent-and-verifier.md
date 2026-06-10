# ADR 0006 — One planning agent + a deterministic verifier

**Status:** Accepted
**Date:** 2026-06-04
**Relates to:** `0004-structure-aware-sections.md`, `0005-calibrated-beats-and-energy.md`

## Context

Phase 3 turns a vibe brief into an ordered set. The open question (`docs/phases.md`)
was whether the **Architect** (brief → energy/BPM arc) and the **Selector**
(order tracks + sections to the arc) should be **two separate LLM agents**, and
whether to build the loop on the **Claude Agent SDK's in-process MCP server** or on
agent-core's `complete()`. Two more forces:

- The Selector needs a **verifier**. "Does this set follow the arc, stay
  harmonically compatible, and not jump tempo?" is fully decidable from the
  structured columns — no model judgment required.
- This is a personal tool that must **run offline** (no API key, on a plane) and be
  **unit-testable** without an agent runtime or network.

## Decision

1. **One planning flow, two roles, with the arc as an explicit artifact.** The
   Architect is a single LLM call that emits an `Arc` (a handful of control
   points, `dj/arc.py`). The Selector is the agentic part — a **generate → verify
   → revise** loop. They're separate modules but not two long-lived agent
   processes; the arc is the clean hand-off between them.
2. **The Critic is a deterministic verifier, not an LLM.** `dj/critic.py` scores a
   `SetPlan` (Camelot compatibility, BPM jumps, energy-arc RMSE in LUFS, artist
   spacing) and its notes feed the Selector's next revision. The same module is
   the Phase 5 eval scorecard core — one source of truth for "set quality."
3. **Build on agent-core `complete()`, behind an injectable model seam.** Every
   agent takes a `model: (messages) -> str` callable that defaults to
   `agent_core.complete` (tier-routed, cached, cost-tracked, traced). A future
   move to the SDK's in-process MCP server only needs to wrap the existing
   `dj/agents/tools.py` functions — the toolbelt is already factored out.
4. **Deterministic fallbacks everywhere.** No model → the Architect returns a
   named-shape arc and the Selector runs a greedy nearest-fit ordering. The system
   always produces a usable set, and that greedy set doubles as the eval A/B
   baseline.

## Rationale

- **The arc-as-artifact is the key simplification.** It shrinks the LLM's job to
  emitting numbers, makes the expensive part (ranking a large pool to a curve)
  pure and fast, and gives the HITL gate + evals something concrete to read.
- **Verifiers should be deterministic where the question is decidable.** A key
  clash is a fact, not an opinion; spending tokens to "judge" it would be slower,
  costlier, and less reliable than `camelot.compatible`. The LLM earns its cost on
  the genuinely fuzzy part — vibe-fit and ordering — and the Critic keeps it honest.
- **Injectable seams keep the loop testable and offline-capable.** `test_selector.py`
  drives the full loop with a fake model and fake tools; nothing imports the SDK or
  hits the network in the fast suite.

## Trade-offs accepted

- **Not using the SDK's MCP server yet.** We forgo the SDK's built-in tool
  orchestration for now. Mitigation: `dj/agents/tools.py` is the exact seam an MCP
  server would expose, so the migration is mechanical when it pays off.
- **Greedy fallback is myopic.** Nearest-fit can paint itself into a corner a
  beam search wouldn't. Acceptable — it's a fallback/baseline; the LLM path (and a
  future beam search) is the quality path.
- **Two heuristics to tune.** The greedy cost weights and the Critic thresholds are
  hand-set. They're centralized constants, tuned on real data in Phase 5.

## Consequences

- New: `dj/arc.py` (Arc + shapes + interpolation), `dj/plan.py` (Slot/SetPlan),
  `dj/critic.py` (verifier + eval core), `dj/agents/{tools,architect,selector,hitl,generate}.py`.
- The Selector's loop and all pure logic are unit-tested without a DB/model/audio
  (`test_arc`, `test_critic`, `test_selector`, `test_architect`, `test_hitl`).
- Phase 5 reuses `dj/critic.py` as the scorecard; only audio-level transition
  smoothness is added on top once the Mixer renders.
