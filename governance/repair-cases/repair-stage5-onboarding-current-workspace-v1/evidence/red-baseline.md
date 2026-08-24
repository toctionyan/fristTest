# Red baseline

Status: REPRODUCED.

On the exact merged `main` source (`d64934e9ba52e306e65ee7de830937b0bee6035e`),
the canonical repository-onboarding evaluator returns `FAIL` before it can make
the repository-admission decision:

- `required_file_missing:PHASE_CANDIDATE_MANIFEST.json`
- `workspace_identity_invalid:json_invalid`
- `runtime_directory_forbidden:.git`

B28 deliberately deleted `PHASE_CANDIDATE_MANIFEST.json` and made
`release/MANIFEST.json` the current phase identity. Every GitHub Actions checkout
also contains `.git`. The newly installed live transport requests the deleted
file from GitHub, so a correctly selected repository cannot produce an
onboarding artifact against current `main`.

Remote environment observations are not the failing condition:

- GitHub reports `main` as protected.
- GitHub lists the `production-certification` Environment.
- WP-08 run `31716787445` completed the protected configuration step that
  verifies all three required Environment secret inputs are non-empty, then
  failed later in the resumable certification batch.

The red baseline is therefore an identity/traversal contract mismatch, not
evidence that production credentials are absent and not authorization to bypass
the remaining WP-08 runtime gates.
