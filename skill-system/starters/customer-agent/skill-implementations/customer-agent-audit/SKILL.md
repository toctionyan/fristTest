---
name: customer-agent-audit
description: Audit an entire customer-service Agent for architecture, context, tool, authorization, quality, and operability defects. Use for whole-project problem discovery before repair.
---

# Audit the customer Agent

Inspect the declared project scope without modifying it.

1. Establish the requested revision, modules, supported capabilities, and acceptance criteria.
2. Trace representative requests through context construction, semantic planning, Tool selection, business authority, write confirmation, response projection, and evidence persistence.
3. Check context reference resolution, multi-intent decomposition, unsupported-capability rejection, Draft/Confirm/Execute writes, RAG grounding, error recovery, observability, and replayability.
4. Inspect tests for normal, boundary, counterexample, interruption, and stale-state paths. Do not treat test green as proof of missing requirements.
5. Record every finding with severity, affected boundary, reproduction, expected behavior, evidence, and proposed verification.
6. Preserve unrelated findings when a standards or domain extension is attached.

Return `findings` with `finding-set@1` evidence when defects exist, `clean` only with coverage evidence, or `blocked` when required project evidence is unavailable.
