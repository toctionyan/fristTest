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

## Round-trip invariants

1. A merely `VERIFIED`, candidate-only, incomplete, or non-independent mismatch cannot seal a dependency repair delta.
2. Applying an authoritative `ADD_DEPENDENCY` delta must make the candidate graph match the same dependency authority.
3. Applying an authoritative `REMOVE_DEPENDENCY` delta must make the candidate graph match the same authority, including an authoritative empty graph.
4. Changing only Planner `depends_on` must not change the frozen semantic premise digest.
5. Provider diagnostic feedback remains read-only; exact relation ids appear only in the sealed reducer-owned repair contract.
6. Runtime never edits the candidate graph on Planner's behalf.

These invariants directly close the historical Attempt-8 class of failure: proof may no longer be correct internally while repair transport loses the proved relation and sends Planner back into semantic guesswork.
