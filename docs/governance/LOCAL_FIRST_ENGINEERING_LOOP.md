# Local-first Engineering Repair Loop

## Authority model

One task has one `Patch Owner`. Test, review, security and CI agents publish findings and evidence; they do not concurrently edit the same branch.

The authoritative sequence is:

```text
TaskRun
  -> local reproduction
  -> local repair
  -> targeted gate
  -> module gate
  -> static gate
  -> Quick gate
  -> read-only review
  -> exact-scope upload admission
  -> GitHub clean-room certification
  -> CI feedback returned to the same TaskRun
```

GitHub is the independent certification layer. It is not the default repair workspace.

## Local budgets

Default budgets are independent:

- local repair rounds: 4;
- local verification failures: 2;
- CI feedback rounds: 2;
- no-progress events: 2;
- flaky retries: 2.

Environment, Secret, permission, timeout and flaky failures do not consume product-code repair authority.

## Upload admission

A branch may be uploaded only when all local conditions have evidence:

1. targeted tests green;
2. owning module tests green;
3. static checks green;
4. Quick regression green;
5. read-only review green;
6. changed paths remain inside the immutable allowlist.

The admission record binds the task, base SHA, candidate head SHA, workspace fingerprint and exact changed paths.

## CI feedback ownership

| CI failure | Owner | Product-code write |
|---|---|---:|
| code or contract | original Patch Owner | yes |
| test authority defect | Test Maintainer | no, until independently approved |
| flaky | CI Reliability | no |
| runner/network/environment | CI Reliability | no |
| Secret/auth/permission | Platform Operator | no |
| unknown | human triage | no |

A code/contract failure is returned to the original local TaskRun. A new unrelated remote fixer must not take ownership automatically.

## Remote repair

Remote Stage-2 repair is opt-in fallback only. It requires explicit approval evidence. It remains unable to merge `main`, weaken tests, publish production or set `production_closed=true`.

## CLI

```bash
python3 -B scripts/local_first_loop.py init \
  --spec governance/local-first/my-task.json \
  --state .quality/task-runs/my-task.json

python3 -B scripts/local_first_loop.py run-local \
  --workspace . \
  --spec governance/local-first/my-task.json \
  --state .quality/task-runs/my-task.json

python3 -B scripts/local_first_loop.py admit-upload \
  --workspace . \
  --state .quality/task-runs/my-task.json \
  --head-sha <local-commit-sha> \
  --changed-path services/agent-service/app/example.py
```

After GitHub starts, bind the exact run and ingest its result with `ci-start` and `ci-result`.

## Git checkout identity

`local-first init` must run from the root of a real Git worktree. Before it records a
baseline, it verifies all of the following:

1. the checked-out named branch exactly matches the task's immutable `branch`;
2. `HEAD` resolves to the task's declared `base_sha`;
3. the worktree is clean, including untracked files;
4. detached HEAD and archive-only directories are rejected.

During local repair the declared base must remain an ancestor of the current `HEAD`.
Upload admission is stricter: the candidate must be committed, the worktree must be
clean, and the supplied candidate SHA must equal the actual local `HEAD`. This prevents
an Agent from testing one tree and uploading another, or from declaring an arbitrary
base SHA that was never checked out locally.
