# B33 Runtime Candidate Validation Carrier

This branch is a CI-only carrier for validating the B28-to-B33 Agent runtime and test delta.

- Base branch: `agent/b28-repository-onboarding`
- Runtime patch SHA-256: `af58c9e2262eaaf945b9c893624bc726ac619f832cb962e8209e61da1ca7bd89`
- Patched paths: 48 files below `services/agent-service/src` and `services/agent-service/tests`
- Production closure claim: **false**

The workflow reconstructs the compressed patch, verifies its digest, runs `git apply --check`, applies it only inside the ephemeral GitHub runner, installs the locked Agent environment, runs Stage 1–5 focused contracts, runs the non-integration Agent regression, and uploads evidence.

Do not merge this patch-carrier PR as the source publication mechanism. If validation succeeds, publish the actual B33 source tree through a separately governed source synchronization PR.
