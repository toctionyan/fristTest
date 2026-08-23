---
name: customer-agent-repair
description: Apply a bounded, evidence-driven repair to a customer Agent. Use only after an exact finding, write scope, rollback, and external write authority have been established.
---

# Repair the customer Agent

Treat the supplied finding and write boundary as immutable inputs.

1. Reproduce the finding and confirm its owning authority before editing.
2. Reject unrelated cleanup, broad rewrites, gate weakening, compatibility dual paths, and guessed requirements.
3. Change the smallest owner that fixes the causal defect and remove any superseded live decision path.
4. Add or strengthen a counterexample that failed before the repair.
5. Record changed paths, invariant impact, rollback, and the focused tests required next.
6. Never commit, create a PR, merge, or claim completion from this Skill; those are separate Workflow capabilities and authorities.

Return `patched` with `patch-set@1` and durable diff evidence, or `blocked` without modifying source when write authority, scope, reproduction, or required evidence is absent.
