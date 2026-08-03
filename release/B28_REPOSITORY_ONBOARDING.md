# B28 Repository Onboarding

B28 does not choose or mutate a GitHub repository. It makes repository selection and protected-environment readiness machine-verifiable before import.

## Required sequence

1. Export target repository metadata using `governance/repository-onboarding-metadata.example.json`.
2. Run `python scripts/repository_onboarding_preflight.py --repository-metadata <file>`.
3. Import only when the result is `PASS`.
4. Run `.github/workflows/wp08-certification.yml` on protected `main`.
5. Continue incomplete batches with the exact prior Run ID and Attempt.

The preflight rejects real `.env` files, runtime caches, symlinks, public repositories without explicit approval, and nonempty unrelated repositories. It checks only secret names/presence; secret values are never read or emitted.
