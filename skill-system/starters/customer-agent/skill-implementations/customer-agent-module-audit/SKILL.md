---
name: customer-agent-module-audit
description: Audit one exact customer-Agent module or feature with targeted traces and tests. Use for scoped checks such as context, Tool routing, authorization, RAG, or response rendering.
---

# Audit one module

Remain inside the supplied module or feature scope and do not modify files.

1. Identify the module's inputs, outputs, callers, dependencies, state ownership, and failure contracts.
2. Trace normal and negative paths across its public boundary.
3. Check that it neither duplicates another authority nor silently falls back to a similar capability.
4. Verify targeted tests cover stale state, invalid references, partial results, interruption, retry, and unsupported requests where applicable.
5. Distinguish defects inside the module from upstream or downstream findings, while retaining cross-boundary evidence.

Return `findings` with `finding-set@1`, `clean` with targeted coverage evidence, or `blocked` when the exact scope cannot be resolved.
