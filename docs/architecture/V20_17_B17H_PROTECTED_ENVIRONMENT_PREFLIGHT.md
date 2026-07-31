# V20.17 B17h Protected Environment Preflight

## Problem

B17g makes invalid workflow dispatches fail visibly, but a valid protected dispatch still installs uv, two Python environments, the frontend dependency tree and Chromium before the release controller checks production credentials and endpoints. A missing secret or placeholder value is therefore discovered late, after the most expensive setup work, and has no dedicated early evidence record.

## Preflight authority

B17h introduces `protected-environment-preflight@1`, a standard-library-only script executed inside the secret-bearing protected Job after pinned `setup-python` and `setup-node`, but before any dependency installation.

It validates:

- GitHub Actions CI context, protected `main`, commit and Run/Attempt identity;
- exact Python, Node and npm versions from `release-toolchain-lock@1`;
- availability of Node, npm, Docker and Git;
- official OpenAI or DeepSeek chat endpoint and provider/model coherence;
- non-placeholder chat credential;
- OpenAI-compatible HTTPS Embedding endpoint, model, dimension and credential;
- an Evidence signing key of at least 32 bytes.

The script reports only normalized identities and short SHA-256 fingerprints. It never emits credential values. Missing environment inputs return `BLOCKED_BY_ENVIRONMENT`; malformed, placeholder, unofficial or unsafe inputs return `FAIL`.

## Workflow ordering

The supply-chain static contract requires the preflight step to occur before `Install locked Python and frontend environments`. The preflight script is itself included in `locked_source_files`, and its sanitized JSON result is uploaded by the existing `if: always()` evidence step even when the Job stops before Quality Loop execution.

The later B17c/B17e/B17f controls remain authoritative. B17h is an early rejection layer, not a replacement for runtime toolchain provenance, real provider calls, PostgreSQL/browser certification, signed Quality evidence or final artifact validation.

## Remaining closure boundary

This candidate cannot become `production_closed` without one real protected GitHub Environment run. The current execution environment has no accessible repository, protected branch identity, exact locked Python/Node runtime, Docker or production secrets. Those absences remain environment blocks and are recorded without relabeling the Claim as closed.
