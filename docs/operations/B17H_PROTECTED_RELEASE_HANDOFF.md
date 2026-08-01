# B17h Protected Release Handoff

## Repository requirements

1. Put the complete workspace at the root of a GitHub repository.
2. Use `main` as the protected release branch.
3. Enable branch protection or a ruleset so GitHub reports `github.ref_protected == true`.
4. Keep `.github/workflows/release.yml` unchanged unless the release toolchain lock and tests are updated together.

## GitHub Environment

Create an Environment named `production-certification`. Configure required reviewers where appropriate and add:

- Secret `PRODUCTION_MODEL_API_KEY`
- Secret `PRODUCTION_EMBEDDING_API_KEY`
- Secret `QUALITY_EVIDENCE_SIGNING_KEY` with at least 32 bytes
- Optional variable `PRODUCTION_EMBEDDING_API_BASE`; when absent the workflow uses Alibaba Model Studio Beijing OpenAI-compatible base, while Singapore or workspace-specific keys must override it

Do not use placeholder, test or local credentials. The preflight does not print secret values.

## Manual dispatch

Run the workflow **production-certification-release** from protected `main` and provide:

- `provider`: `openai` or `deepseek`
- `model`: a current model ID for that provider
- `embedding_model`: the model served by the configured Embedding endpoint
- `embedding_dimension`: the exact vector dimension emitted by that model

## Expected sequence

1. `release-admission` rejects invalid trigger/ref/input without Secrets.
2. `protected-release` enters the protected Environment.
3. `protected-environment-preflight@1` validates exact base tools and protected configuration before dependency installation.
4. The locked environments and Chromium are installed.
5. Toolchain and CI Run provenance are captured.
6. The release Quality Loop executes real model, PostgreSQL/pgvector and browser journeys.
7. Only a fully validated same-run evidence Bundle can produce `production_closed` artifacts.

A failed run should provide `production-certification-evidence-<run_id>-<attempt>` containing the sanitized preflight result and any later control ledger that was reached.
