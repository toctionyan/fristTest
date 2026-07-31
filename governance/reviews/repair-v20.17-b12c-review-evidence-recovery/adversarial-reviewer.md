# Adversarial Review — B12c Review Evidence Recovery

Decision: **PASS**

## Attacks considered

1. **Historical hashes silently assigned to newly written reviews** — rejected: the recovery manifest preserves the old hashes under `historical_missing_hashes`, records separate replacement hashes, and fixes `historical_hashes_reused_for_replacement` to `false`.
2. **Deleted review bytes falsely claimed to be reconstructed from SHA-256** — rejected: the architecture record explicitly states that the original bytes cannot be reconstructed and the new reviews are independently authored replacements.
3. **Original closed contract rewritten to conceal the incident** — rejected: an exact preserved copy of the B12b closed contract is hash-bound in `prior-closed-change.json`; the recovery manifest identifies the incident as `closed_review_files_deleted_after_attestation`.
4. **Product code changed under the label of evidence recovery** — rejected: checksum comparison found zero differences across Agent source, Agent app, Business Service and frontend source trees; the active contract also forbids all product implementation roots.
5. **B12 transaction/runtime result lost during recovery** — rejected: the B12 boundary counterexample remains VERIFIED and the architecture graph still reports only `lifecycle / runtime` in the main SCC.
6. **Recovery documentation becomes a second active change authority** — rejected: the recovery artifacts disclose provenance only; the active B12c contract remains the sole current governance authority.
7. **Quality evidence reused without rerun** — rejected: B12c ran a new 18-gate Quick against the recovery candidate; lifecycle and real Chromium gates passed.
8. **Missing evidence hidden by deleting historical hashes** — rejected: both historical hashes remain visible and are asserted by an executable regression test.
9. **Architecture debt incorrectly declared closed** — rejected: architecture remains `PASS_WITH_DEBT / REDUCED`, and the unresolved `lifecycle / runtime` cycle is explicitly retained.
10. **Deterministic certification misrepresented as real-model certification** — rejected: real model certification remains `NOT_DECLARED`.

## Residual risk

- The original two review Markdown byte streams remain unavailable; B12c provides honest replacement evidence, not byte restoration.
- The remaining `lifecycle / runtime` SCC is unresolved architecture debt.
- State/Loop simplification, real-model provider evaluation and prompt-injection certification remain future work.

No reason to reject the B12c evidence recovery was found.
