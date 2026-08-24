# GitHub Repository Onboarding Transport red baseline

Base candidate: `93cf4a31087347236040a12180de6e4969354232`
(merge of PR #2101).

Inspection of the exact base proves:

- `scripts/repository_onboarding_preflight.py` can evaluate a supplied metadata
  document but does not collect GitHub repository metadata;
- `governance/repository-onboarding-metadata.example.json` is illustrative and
  cannot prove the selected live repository, permissions, protected `main`,
  Environment, or Environment secret names;
- no controller queries the GitHub repository, branch-protection, Environment,
  or Environment-secret metadata APIs for WP-08 onboarding;
- no root command creates a sanitized, sealed metadata artifact and immediately
  submits it to the existing preflight;
- token values must not be placed in the metadata file, command output, or
  preflight evidence.

The negative baseline is therefore reproduced: the project is now present in
an accessible GitHub repository, but WP-08 still has no repository-owned runtime
transport that can produce the metadata required by its existing fail-closed
onboarding authority.
