# B27 Stage-6 Host and Production Closure Handoff

This handoff never creates production closure from local or synthetic evidence.

## 1. Preconditions

- WP-08 and STAGE-5 are `CLOSED_VERIFIED`.
- The workspace is the clean `main` checkout of the correct protected GitHub repository.
- Real `codex`, `claude`, and `gh` CLIs are installed and authenticated.

Run:

```bash
python3 -B scripts/host_execution_preflight.py --workspace-root . --mode host --output host-preflight.json
```

A nonzero result is a blocker, not a reason to bypass Hooks or edit the ledger.

## 2. Real host canaries

Run one governed, disposable repair through Codex and one through Claude Code. Both must use the same `skillctl.py`, active ChangePermit, read-only reviewers and Stop Hook. Preserve the host transcripts and resulting governance records.

## 3. Protected production release

Run `.github/workflows/release.yml` on protected `main` in the `production-certification` Environment. Download:

- `production-release-control/production-release-result.json`;
- `release-toolchain-provenance.json`;
- the exact three files from the `production-closed-*` Artifact.

Independently consume them:

```bash
python3 -B scripts/verify_production_closure_artifact.py \
  --result production-release-result.json \
  --toolchain-evidence release-toolchain-provenance.json \
  --artifact-dir production-closed-artifacts \
  --repository OWNER/REPO --commit COMMIT_SHA \
  --run-id RUN_ID --run-attempt RUN_ATTEMPT \
  --output production-closure-consumption.json
```

Only a PASS output from this consumer may support `production_closed=true`.
