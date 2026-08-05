# GitHub CI Failure Ingestion — Stage 1

This stage closes the manual screenshot and Run-ID handoff gap between GitHub Actions and the repository governance control plane.

## Trigger

`.github/workflows/governed-ci-failure-ingest.yml` listens for completed runs of:

- `quality`
- `wp08-full-stack-certification`

Only non-successful runs enter ingestion.

## Behavior

The workflow checks out trusted control-plane code from protected `main`, checks out the failed source SHA as untrusted data, downloads the source run artifacts and logs, redacts common secret forms, classifies the failure, and writes:

- `failure-case.json`
- `task-run.json`
- `changed-files.json`

It uploads those files as `governed-ci-failure-<run-id>` and creates or updates a GitHub Issue for the run.

## Fail-closed boundaries

Stage 1 does not:

- read `production-certification` Environment secrets;
- execute downloaded logs or artifacts;
- modify source code;
- push a repair branch;
- create or merge a pull request;
- write to protected `main`;
- set `production_closed=true`.

A code or contract failure is marked `READY_FOR_REPAIR_STAGE_2` only when the source run is from the same repository, a failed quality gate exists, and concrete non-governance candidate paths are supported by failure evidence. Environment, timeout, cancellation, approval, runner, fork, and unknown failures remain blocked for diagnosis.
