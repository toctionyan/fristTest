# Dependency Authority Repair Round-trip

## Purpose

This note freezes the Planner repair boundary added after the dependency-proof maturity lifecycle. It closes the historical failure where verification proved a dependency relation, but provider-facing repair discarded the proved relation and forced Planner to infer it again.

## Ownership

The authority chain is:

`Verifier observation -> deterministic dependency proof reducer -> AUTHORITATIVE graph mismatch -> sealed RepairContract delta -> Planner redeclaration -> ordinary validation`

The verifier does not write Planner semantics. The reducer decides maturity. Runtime does not insert or remove `depends_on`. Planner remains the semantic writer, but for an already-authoritative dependency mismatch it applies the sealed relation delta rather than reopening inference.

## Repair contract

Only `dependency_authority_state == authoritative` may create `repair_contract.authoritative_dependency_delta`.

The delta is bound to both:

- the frozen semantic `premise_digest`;
- the reducer's current `authority_evidence_digest`.

Operations are explicit and minimal:

- `ADD_DEPENDENCY` carries dependent Goal id, prerequisite Goal id, relation-only basis kind and literal basis span;
- `REMOVE_DEPENDENCY` carries only the exact unproved candidate relation to remove.

The contract must not carry Tool identity, Capability availability, target replacement, requested-effect replacement, or business facts.

## Provider projection

`independent_verifier_feedback` remains violation-only diagnostic evidence. Raw verifier replacement graphs still do not become writer authority.

The sealed `repair_contract.authoritative_dependency_delta` is different: it is reducer-owned machine truth and is intentionally preserved across the provider boundary. Planner is instructed to apply only the listed dependency operations, preserve Goal ids and all non-dependency semantics, and redeclare.

### Compatibility boundary

The established generic provider-facing repair constraints remain stable, including `rederive_semantics_from_current_user_input` and `do_not_copy_verifier_dependency_edges_or_replacement_semantic_values`. The new dependency repair path does not redefine those generic contracts.

The sole machine-authoritative exception is the reducer-sealed `repair_contract.authoritative_dependency_delta`. It is not a raw verifier graph: it is emitted only after deterministic maturity reaches `AUTHORITATIVE`, is bound to the current premise and authority evidence digests, and may be applied only as the exact listed dependency operations. Non-dependency and non-authoritative repairs continue to rederive semantics from current user input under the existing provider contract.

## Round-trip invariants

1. A merely `VERIFIED`, candidate-only, incomplete, or non-independent mismatch cannot seal a dependency repair delta.
2. Applying an authoritative `ADD_DEPENDENCY` delta must make the candidate graph match the same dependency authority.
3. Applying an authoritative `REMOVE_DEPENDENCY` delta must make the candidate graph match the same authority, including an authoritative empty graph.
4. Changing only Planner `depends_on` must not change the frozen semantic premise digest.
5. Provider diagnostic feedback remains read-only; exact relation ids appear only in the sealed reducer-owned repair contract.
6. Runtime never edits the candidate graph on Planner's behalf.
7. Stable generic provider repair constraints remain backward-compatible; the sealed dependency delta is additive rather than a rename or weakening of existing repair semantics.

These invariants directly close the historical Attempt-8 class of failure: proof may no longer be correct internally while repair transport loses the proved relation and sends Planner back into semantic guesswork.

## Control-plane liveness reliability

Stage-3 validation exposed an independent CI observability race in the reusable execution runtime. The stall loop evaluated the fail-closed timeout before publishing the warning heartbeat. Under scheduler jitter a process could therefore cross both thresholds in one scheduling interval and emit `STALL_TIMEOUT` without an observable `SUSPECTED_STALL` transition.

The control-plane runtime now binds warning publication to the current no-progress epoch rather than to heartbeat sampling luck. If a scheduling jump reaches the stall timeout before that epoch has published a warning, the runtime emits one `SUSPECTED_STALL` event immediately before the fail-closed `STALL_TIMEOUT` event. New child progress starts a new epoch naturally because the bound progress timestamp changes.

This does not extend timeout budgets, suppress failure, or weaken the liveness assertion. The regression test retains the warning requirement and additionally verifies that `SUSPECTED_STALL` is observable before the `[WP08 STALL]` timeout event.

## Stage-3 validation closure

The validated product/control-plane tree passed the standard push quality pipeline after the liveness reliability fix:

- Skill control plane: 310 tests, OK; protected product source remains 617/617.
- Static quality: PASS.
- Quick quality: PASS, including the stable Release-50 provider repair contract.
- Integration quality: PASS after actually starting the deterministic model and both services and running the integration gates.

The pull-request-triggered quality path independently passed control-plane, static and quick; its integration job was change-gated and remains recorded as SKIPPED rather than being relabeled as PASS. The actual current-tree integration PASS comes from the same-head push quality run.
