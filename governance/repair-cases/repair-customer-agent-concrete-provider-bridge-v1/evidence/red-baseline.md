# Red baseline: concrete provider bridge

At merge commit `9e15dcf29a909990f24dc58ade04fbd8e382d71c`, the Customer Agent Starter can compile, activate, route, checkpoint, and resume a Workflow, but its publication boundary is not executable without test doubles:

- `LocalGitProviderAdapter` requires an injected `LocalGitPublicationHost`; the repository supplies only Protocol and fake test implementations.
- `CodeReviewProviderAdapter` requires an injected `CodeReviewPublicationHost`; the repository supplies only Protocol and fake test implementations.
- `local.workspace` is registered as the `workspace.write` Provider but has no Provider Adapter with an exact structured mutation contract.
- no bootstrap assembles real local workspace, Git commit, GitHub PR-create, local test/quality, and CI-wait adapters for an installed Starter.

Therefore `/harness repair ... --ci` and `/harness full-dev ... --ci` can reach activation but fail closed unless an embedding application reimplements these missing bridges. The existing fake hosts prove routing only; they do not prove a real temporary Git repository can be mutated, committed, and published through an exact-head PR request.
