# Governance Record Rules

- Governance records are append-oriented evidence, not implementation scratch files.
- Reviewers remain read-only. Only the deterministic `review-importer` may import exact reviewer artifacts and attestations.
- The product implementer may register its task identity but may not approve plans, import reviews, alter attestations, freeze records, Diff reviews, closure matrices, Claims, Targets, or Judge evidence.
- Do not overwrite an existing stage with another task unless the prior record is explicitly invalidated and the full downstream chain is rebuilt.
- A changed plan, baseline, candidate, Diff scan, or closure matrix invalidates every downstream record bound to it.
