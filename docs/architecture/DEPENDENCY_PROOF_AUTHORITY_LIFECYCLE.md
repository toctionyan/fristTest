# Dependency Proof Authority Lifecycle

## Scope

This document freezes the semantic dependency-proof model introduced on top of exact Release #54 main `21cbb7aff4d8b4e18dbade4844f2c5864be8b991`.

The purpose is not to add another Release-specific branch. It is to remove the failure mode in which a verifier observation is treated as authority too early, then another verifier call reopens or contradicts it later.

The model applies only to semantic Goal dependency proof. Runtime execution, capability selection, target resolution, business state, and transaction authorization remain outside this authority boundary.

## Authority boundary

The flow is:

`model observation -> deterministic reducer -> request-local dependency proof state -> structured repair/acceptance consumer`

The model never writes proof maturity directly. `complete` and `matching` are diagnostics, not authority.

Planner declarations are candidates. Runtime never inserts, removes, or rewrites `depends_on` as semantic inference.

## Frozen proof states

`CANDIDATE -> VERIFIED -> AUTHORITATIVE`

Additional states are `CHALLENGED`, `STALE`, and `REJECTED`.

- **CANDIDATE**: candidate-visible declaration/verifier output. No authority.
- **VERIFIED**: a complete candidate-blind pairwise graph under one frozen semantic premise. It is preservable but still requires dependency-specific adversarial closure.
- **AUTHORITATIVE**: an explicit dependency-closure observation has closed the graph under the same frozen premise.
- **CHALLENGED**: an authoritative graph received new admissible counterevidence explicitly bound to the authority it supersedes. A distinct reclosure is required before replacement authority can be sealed.
- **STALE**: frozen semantic premises changed. The old proof cannot be reused as current authority.
- **REJECTED**: the observation is structurally inadmissible or cannot satisfy the required proof transition.

Verifier call number is not semantic state. A third, fourth, or later bounded call has meaning only because orchestration assigned it an explicit role such as semantic-only adjudication or dependency closure.

## Core invariants

1. `complete && matching` never mints dependency authority by itself.
2. A complete multi-Goal blind graph first becomes `VERIFIED`; an explicit dependency-specific adversarial closure is required for `AUTHORITATIVE`.
3. Semantic-only adjudication cannot mint, replace, reopen, or downgrade dependency authority.
4. A graph mismatch is audited in both polarities: missing true edges and extra/false declared edges follow the same maturity protocol.
5. An empty graph is proof-carrying. Every unordered Goal pair must be represented by a complete pairwise decision before the graph can mature.
6. Positive dependency basis must be relation-only evidence (`result_reference`, `result_condition`, or `result_value_input`) and must not self-certify from requested-output/action wording.
7. Once `AUTHORITATIVE`, ordinary same-premise revotes cannot replace the graph.
8. Authority can be challenged only by new admissible counterevidence bound to the current authority evidence, or invalidated by changed frozen premises.
9. Planner repair feedback may consume a dependency graph only when `dependency_authority_state == authoritative`.
10. Outer alignment normalization may preserve a no-`missing_spans` dependency mismatch only when the mismatch is backed by mature authority. Raw `complete/matching` mismatch fails closed.
11. The semantic premise digest excludes Planner `depends_on`; Planner cannot bootstrap its own dependency authority.
12. Generic malformed/ungrounded verifier retries do not consume the expanded bounded call budget. Extra calls are reserved for explicit state transitions only.

## Historical failure-state matrix

| Historical failure | Illegal old transition | Required lifecycle behavior |
| --- | --- | --- |
| Release #43 | machine-proven true edge was not transportable as usable repair authority without redundant outcome grounding | candidate mismatch -> blind pairwise proof -> VERIFIED -> dependency closure -> AUTHORITATIVE mismatch -> structured Planner redeclaration |
| Release #44 | broad action/requested-output phrase self-certified a false `result_reference`/`result_value_input` | reject non-relation-only basis before maturity; blind pairwise proof may establish authoritative independence instead |
| Release #47 | correct dependency mismatch lost polarity or machine-usable repair detail across projection | only AUTHORITATIVE graph diff feeds repair; projection preserves violation polarity without letting Runtime rewrite the graph |
| Release #52 / #1110 | `complete/matching` dependency absence was trusted as if absence were already proven | complete blind absence -> VERIFIED only -> explicit independence/edge counterfactual closure -> AUTHORITATIVE |
| Release #53 / #1124 | semantic-only third call needed to ground/withdraw `incomplete`, but dependency proof also had to survive | preserve VERIFIED dependency proof unchanged during semantic-only adjudication; if semantic claim withdraws, run a separate dependency-closure transition |
| Release #54 | #52 and #53 branch rules competed for the same third verifier slot and changed behavior by branch precedence | call-count-independent roles + deterministic maturity reducer; no call simultaneously owns semantic adjudication and dependency authority |

## Scenario simulation

### True result dependency

User semantics contain a relation-only reference such as `that result`/`它` in Goal B.

1. Candidate-visible graph may include or omit the edge; this is not authority.
2. Candidate-blind pairwise proof finds `B -> A` with a literal relation-only basis and becomes `VERIFIED`.
3. Dependency closure applies the result-removal counterfactual and confirms the edge.
4. Proof becomes `AUTHORITATIVE`.
5. If Planner omitted the edge, structured repair feedback requests redeclaration. Runtime does not mutate the declaration.

### Truly independent siblings

1. Blind pairwise proof covers every unordered pair with `independent` and becomes `VERIFIED`.
2. Dependency closure repeats the dependency-specific counterfactual and confirms independence.
3. Empty graph becomes `AUTHORITATIVE` only now.
4. A Planner-declared false edge is repaired by redeclaration, not Runtime deletion.

### Ungrounded semantic `incomplete`

1. Blind dependency graph is complete and `VERIFIED`.
2. A semantic-only adjudication is allowed to ground or withdraw only the semantic claim; dependency decisions are not returned or rejudged.
3. If the semantic claim is withdrawn, orchestration performs a separate dependency-closure transition.
4. If a literal semantic missing span is grounded, the turn remains incomplete while the dependency proof remains preservable and non-authoritative until needed later.

### Repeated contradictory verifier opinions

After `AUTHORITATIVE`, a same-premise contradictory observation without authority-bound new counterevidence is a revote, not a state transition. The existing graph is preserved.

### User correction / changed semantic premise

If user text, target semantics, effect semantics, reference semantics, or another frozen premise changes, the premise digest changes. Old authority becomes stale and a fresh proof sequence starts. This is not an arbitrary downgrade of the old proof; it is a new proposition under new premises.

## Release closure requirements

This lifecycle is not considered closed merely because one Release case is green. Before merge, validation must include:

- Release #43, #44, #47, #52, #53, and #54-derived regressions;
- positive dependency and authoritative independence;
- malformed/ungrounded observations;
- mismatch polarity in both directions;
- semantic-only adjudication followed by dependency closure;
- repeated same-premise revotes;
- authority-bound counterevidence/reclosure reducer tests;
- premise-change invalidation;
- repair projection and outer-normalization boundaries;
- repository quality, architecture, protected-source, and Skill control-plane gates.

A failure in any of these dimensions is evidence that the model is not yet closed. It must not be converted into a Release-specific special branch.
