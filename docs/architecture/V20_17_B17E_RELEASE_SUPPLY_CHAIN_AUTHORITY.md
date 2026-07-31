# V20.17 B17e Release Supply-Chain Authority

## Problem

B17d bound the browser, PostgreSQL and real-model certifications to one protected runtime, but the release runner itself was not immutable. The workflow referenced GitHub Actions by mutable major tags, installed `uv` without an exact version or artifact hash, and selected a mutable Node major line. A later upstream action or bootstrap tool change could therefore execute different code against the same source commit while the release evidence still appeared to describe one authoritative run.

## Authority boundary

B17e introduces `release-toolchain-lock@1` and `release-toolchain-provenance@1`.

The static lock owns:

- the exact protected runner image declaration;
- full commit SHAs for every remote GitHub Action used by the release workflow;
- exact Python, Node, npm and uv versions;
- a SHA-256 locked uv bootstrap wheel;
- hashes of both Python lockfiles and the frontend package manifests;
- one immutable `pgvector/pgvector@sha256:...` manifest digest;
- explicit hidden-file upload for `.quality` targets and claim manifests.

The runtime provenance owns:

- the resolved tool versions and executable hashes, including Docker client/server identity;
- the installed Python distribution-set digests for Agent and Business;
- the installed frontend dependency-tree digest;
- the protected PostgreSQL image reference and the actual container image ID;
- the static workflow and source-lock contract;
- one canonical `toolchain_fingerprint_sha256`.

## End-to-end binding

The workflow captures provenance only after locked installation. Its fingerprint and evidence path are injected into the production release controller. The controller validates the evidence against the current workspace before any production certification starts. The same fingerprint is then required in:

1. every production component session;
2. the combined production certification bundle;
3. the Quality Loop production dimension;
4. the Quality Loop real-model dimension;
5. the final release summary and production-closed ledger.

The PostgreSQL and browser components must additionally attest the same immutable pgvector reference and the same actual container image ID. A mutable tag, image mismatch or missing hidden claim artifact is a hard failure, not an environmental PASS.

A missing, malformed, replayed, tampered or cross-toolchain fingerprint fails closed. External installation or command availability failures remain `BLOCKED_BY_ENVIRONMENT`; they cannot become a product PASS.

## Non-goals

B17e does not change customer-service semantics, prompts, capabilities, business rules, transaction state, storage behavior or model selection policy. It does not claim that the protected workflow has run in the current local environment.
