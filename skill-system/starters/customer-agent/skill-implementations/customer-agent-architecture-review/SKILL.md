---
name: customer-agent-architecture-review
description: Review a customer Agent's authority boundaries, state ownership, orchestration, extensibility, and quality attributes. Use when deciding whether an implementation is structurally sound or needs repair.
---

# Review architecture

Analyze the current implementation and evidence without modifying source.

1. Map semantic planning, context, business facts, transaction state, Tool execution, presentation, Quality, and completion owners.
2. Detect dual writers, duplicated state machines, fuzzy capability fallback, hidden side effects, and Host-specific control paths.
3. Evaluate whether a new domain, Tool, Skill, Provider, or Workflow can be added without rewriting the main chain.
4. Check durability, idempotency, interruption recovery, observability, replay, deployment independence, and bounded failure behavior.
5. Prefer the smallest architecture change that restores one authoritative path and deletes superseded writers.

Return `approved` with `architecture-assessment@1` evidence when no structural repair is required. When repair is needed, use the exact repair outcome declared by the active Workflow: `change-required` in the standalone architecture review or `repair-required` in full development. Return `blocked` when essential topology is unavailable.
