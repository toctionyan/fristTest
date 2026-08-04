# B33 GitHub Actions validation candidate

This branch is based on `agent/b28-repository-onboarding` at commit `cf1ae3e406557070bc18c33782583a1c8f70de70`.

The B33 runtime and test delta is stored as an XZ-compressed, base64-split Git patch under `candidate-validation/b33-runtime-patch-xz/`.

The workflow reconstructs the patch, verifies decompressed SHA-256 `af58c9e2262eaaf945b9c893624bc726ac619f832cb962e8209e61da1ca7bd89`, verifies that exactly 48 source/test paths change, applies it to the checked-out B28 candidate, installs locked Agent dependencies, and runs focused Stage 1-5, strong-context/runtime, architecture, and Skill control-plane tests.

This is a test-only candidate. It must not be merged into `main`, and it does not assert `production_closed=true`.
