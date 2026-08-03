---
name: governed-repair
description: Use for Codex multi-agent repairs that require independent diagnosis, plan approval, isolated implementation, frozen-candidate review, and evidence-backed closure.
---

# Governed Repair

Use this Skill for every writable `repair`, `migration`, or `revert`.

## Required Codex task topology

Each stage runs in a different Codex task, thread, and worktree:

1. `failure-explorer` — read-only baseline reproduction and root-cause proof.
2. `repair-plan-reviewer` — read-only review of the proposed repair plan.
3. `product-implementer` — the only product-code writer.
4. `diff-integrity-reviewer` — read-only semantic review of the frozen candidate.
5. `closure-arbiter` — read-only decision over immutable closure evidence.

The `review-importer` is a deterministic controller role. It may import exact reviewer output into governance records but may not approve, reinterpret, or edit product code.

## Mandatory handoff records

All records live under `governance/repair-cases/<change-id>/`.

- `failure-case.json`
- `root-cause-proof.json`
- `repair-plan.json`
- `plan-review.json`
- `baseline-manifest.json`
- `change-permit.json`
- `agent-task-manifest.json`
- `attestations/<role>.json`
- `candidate-freeze.json`
- `diff-review.json`
- `semantic-diff-review.json`
- `closure-matrix.json`
- `closure-decision.json`

A role name written inside JSON is not identity evidence. Every reviewer artifact must be imported with an `agent-attestation` bound to repository, baseline commit, task ID, thread ID, worktree ID, input digest, output digest, and decision.

## Lifecycle

```bash
python3 -B skillctl.py agent-review-import \
  --role failure-explorer --artifact /tmp/root-cause-proof.json \
  --attestation /tmp/failure-explorer-attestation.json

python3 -B skillctl.py agent-review-import \
  --role repair-plan-reviewer --artifact /tmp/plan-review.json \
  --attestation /tmp/repair-plan-reviewer-attestation.json

CODEX_TASK_ID=... CODEX_THREAD_ID=... CODEX_WORKTREE_ID=... \
python3 -B skillctl.py agent-implementer-register

python3 -B skillctl.py repair-permit
python3 -B skillctl.py contract-begin
```

After implementation and required tests, commit the product change and freeze that exact candidate:

```bash
python3 -B skillctl.py candidate-freeze --candidate-commit <full-sha>
```

Then generate deterministic Diff evidence and import the independent semantic review:

```bash
python3 -B skillctl.py repair-diff-review --decision PASS
python3 -B skillctl.py agent-review-import \
  --role diff-integrity-reviewer --artifact /tmp/semantic-diff-review.json \
  --attestation /tmp/diff-integrity-reviewer-attestation.json
```

After the eight closure evidence dimensions are recorded, import the independent closure decision and verify:

```bash
python3 -B skillctl.py agent-review-import \
  --role closure-arbiter --artifact /tmp/closure-decision.json \
  --attestation /tmp/closure-arbiter-attestation.json
python3 -B skillctl.py multi-agent-validate --stage verification --result CONVERGED
```

## Fail-closed rules

Reject the transition when:

- any required role is missing;
- two roles reuse the same task or worktree;
- a reviewer shares the implementer task or worktree;
- an attestation input/output digest is stale;
- a plan, permit, candidate, deterministic Diff scan, or closure matrix changes after review;
- the candidate source changes after freeze;
- a reviewer attempts product writes;
- the implementer attempts to write governance review or evidence records;
- a reviewer says PASS while deterministic evidence says REJECT;
- max-cycle exhaustion or environment blocking is presented as convergence.
