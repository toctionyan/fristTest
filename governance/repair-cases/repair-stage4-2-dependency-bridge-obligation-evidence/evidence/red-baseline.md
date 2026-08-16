# Stage 4.2 RED baseline: dependency bridge obligation authority leak

Exact source: PR #1157 feature head `c050d213e9511a0af2894d303eee798fce3eeba6` before this governed repair.

Reproduction is source- and contract-grounded:

1. `services/agent-service/src/agent_core/goal_graph/dependency_alignment.py` calls `make_dependency_observation(...)` for every structurally validated pair row.
2. The bridge currently writes `target_compatibility=PASS` and `counterfactual=PASS` unconditionally.
3. Its `counterfactual_proof_digest` is computed from the pair decision and phase name rather than an independently validated counterfactual evidence record.
4. `goal_planning._model_alignment_pairwise_dependency_proof(...)` validates pair coverage and positive literal basis spans, but its normalized `dependency_pair_decisions` rows do not contain a dedicated target-compatibility proof or counterfactual proof object.
5. The deterministic reducer correctly seals authority once all required obligations are PASS, so the defect is the bridge manufacturing obligations upstream, not the reducer.
6. Existing `services/agent-service/tests/runtime/test_dependency_alignment_authority.py` demonstrates the current false-positive path: an adversarial closure phase plus a plain pair decision can make the graph authoritative.

Expected: pair decision/phase/complete/matching alone leave target compatibility and counterfactual UNKNOWN and cannot mint authority.
Actual: the bridge manufactures both PASS values, allowing closure phase to complete the proof without obligation-specific evidence.
