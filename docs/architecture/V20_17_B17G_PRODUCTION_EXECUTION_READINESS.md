# V20.17 B17g Production Execution Readiness

## Problem

B17f bound every accepted artifact to one protected GitHub Actions run, but the workflow placed the admission expression on the only secret-bearing release job. When a manual dispatch came from the wrong or unprotected ref, GitHub could skip that job instead of executing a failing control step. A workflow containing only a skipped job can be misread as a successful certification even though no Quality Loop, production Bundle, or artifact validation ran.

The phase candidate metadata also lagged behind the implementation: `PHASE_CANDIDATE_NOTICE.md` still described B17e and the root README still claimed the current change modified only Skill files. Those statements would be copied into a release source artifact and undermine operator trust.

## Admission authority

B17g introduces `release-workflow-admission@1`. It is a small standard-library-only contract executed by a dedicated `release-admission` job before any production Environment or secret is available. It validates:

- GitHub Actions and CI context;
- the expected `workflow_dispatch` event;
- the exact protected branch ref and branch ref type;
- repository protection reported by GitHub;
- the expected workflow identity;
- an allowed provider;
- non-empty printable model and embedding model inputs;
- a positive bounded embedding dimension.

Invalid dispatches return an explicit failing exit code. Missing CI context is reported as an environment block. The output contains no credentials.

## Defense in depth

The `protected-release` job requires `release-admission` and retains its original platform expression. Therefore:

1. an invalid dispatch makes the admission job fail visibly;
2. the protected job is still skipped before accessing its protected Environment;
3. a valid dispatch must satisfy both the admission contract and GitHub's platform condition;
4. the B17f run-identity contract independently revalidates the protected ref, commit, workflow, clean checkout and run attempt after the locked toolchain is installed.

The release toolchain lock includes the admission script, and the static supply-chain contract requires the admission job, dependency edge and invocation.

## Metadata truth

The phase notice, README, Changelog, active change and development release manifest identify B17g and distinguish three independent facts:

- product application version: `20.6.1`;
- governance/release phase: `V20.17 B17g`;
- Architecture Skill version: `6.3.0`.

B17g changes release admission and metadata only. It does not change customer-service semantics or business behavior.

## Remaining closure boundary

B17g is still a phase candidate. Production closure requires one real protected GitHub Environment run with the exact locked Python/Node/uv environments, Docker and immutable pgvector image, official model and embedding credentials, evidence signing key, complete release Quality Loop, PostgreSQL/browser journeys and final content-addressed artifact validation. Missing prerequisites cannot be converted into PASS.
