---
name: customer-agent-adversarial-review
description: Challenge a customer-Agent patch using counterexamples, authority checks, and regression analysis. Use after a repair and before final Quality evaluation.
---

# Adversarially review a patch

Review the patch and its evidence without modifying it.

1. Re-run the original failure mentally and against available deterministic evidence.
2. Search for nearby counterexamples: stale context, ambiguous references, missing capabilities, duplicate actions, retries, partial failures, and unauthorized writes.
3. Check that the patch fixes the owning cause rather than adding a special-case keyword, fallback, or parallel authority.
4. Inspect scope drift, weakened assertions, skipped gates, fabricated receipts, and false completion claims.
5. Separate product defects from environment or Provider failures.

Return `findings` with `review-finding-set@1` when any issue remains, `clean` only with explicit counterexample coverage, or `blocked` when the patch/evidence identity is incomplete.
