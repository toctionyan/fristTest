---
name: customer-agent-standards-gate
description: Check customer-Agent findings against project-specific engineering and business standards without replacing general discovery. Use as an additive gate before validation.
---

# Apply project standards

Consume the existing finding set and the project's declared standards.

1. Check each applicable rule against concrete source, trace, test, or policy evidence.
2. Add missing standards findings; do not delete, downgrade, or hide unrelated findings.
3. Distinguish mandatory invariants from preferences and examples.
4. Report unknown or conflicting standards as blockers instead of inventing policy.

Return `continue` with `standards-verdict@1` evidence when evaluation is complete, or `blocked` when mandatory standards or evidence cannot be resolved.
