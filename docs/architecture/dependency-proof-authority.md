# Dependency Proof Authority

## Status

Candidate architecture for PR #1157. The implementation is intentionally fail-closed until the proof lifecycle and historical regressions are green.

## Decision

Dependency verifier output is **observation**, not authority. A deterministic reducer owns proof maturity and the authority ledger.

A pairwise dependency claim can become authoritative only after all required proof obligations pass, including grounding, semantic compatibility, target compatibility, counterfactual necessity/independence, structural validity, contradiction closure, and an explicit adversarial-closure phase. `complete` and `matching` remain diagnostics and cannot mint authority.

An authoritative claim is stable relative to its frozen premise digest. Repeated verifier opinions cannot downgrade it. Downgrade requires either a changed frozen premise or new admissible counterevidence explicitly bound to the current authority evidence; contradictory evidence then enters a challenged state and must be independently reclosed before a replacement authority is sealed.

## Empty graph rule

`dependencies=[]` is not evidence of independence. For a multi-goal declaration, every unordered Goal pair must have an authoritative pair decision. An empty authoritative graph therefore requires explicit authoritative independence for every pair.

## Repair rule

Planner repair consumes only a deterministic authoritative graph diff. Runtime does not rewrite `depends_on` and raw verifier `complete`/`matching` output is insufficient to create a repair contract.

## Call-count independence

Verifier call number is not semantic state. Broad re-audit, semantic/scope adjudication, and graph closure may consume separate calls, but only proof observations plus the deterministic reducer can move maturity. A semantic-only adjudication cannot silently certify or erase a provisional dependency graph.

## Required invariants

1. `complete && matching` without adversarial closure is not authority.
2. Grounding success cannot short-circuit a failed dependency counterfactual.
3. Target incompatibility blocks dependency/result reuse even when effects look compatible.
4. An authoritative proof survives repeated ungrounded, unbound, or merely contradictory opinions.
5. New counterevidence must bind to the current authority evidence and be reclosed before replacement.
6. A premise change marks old authority stale rather than silently preserving or rewriting it.
7. Empty graphs are proof-carrying and fail closed while any pair remains unresolved.
8. Repair feedback is emitted only from mature deterministic authority.

## Historical failure mapping

- Release #52: empty/matching dependency absence was accepted too early -> invariant 1 + empty graph rule.
- Release #53: ungrounded semantic `incomplete` could consume the final verifier slot -> call-count independence and evidence admissibility.
- Release #54 / #1110 / #1124 conflict: semantic adjudication, dependency closure, and preserve behavior competed as branches -> deterministic maturity reducer and authority stability.
- Cross-target reuse regressions -> target compatibility is an independent mandatory proof obligation.

## Release closure

This design is not considered complete merely because focused behavior tests pass. The PR must still pass repository quality, skill control-plane, architecture, historical regression, and protected release gates before merge or release certification.
