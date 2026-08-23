# Customer Agent Concrete Provider Bridge v1

## Outcome

The installed Customer Agent Starter now has one repository-owned assembly for
deterministic local and publication effects. The assembly implements effects
under the existing Capability Resolver and `WorkflowAdapterDispatcher`; it does
not create a new Workflow, write, Quality, merge, or completion authority.

| Capability | Activated Provider | Concrete implementation |
| --- | --- | --- |
| `workspace.write` | `local.workspace` | digest-bound structured mutation transaction |
| `test.run` / `quality.evaluate` | `local.process` | existing allow-listed profile runner |
| `vcs.commit.create` | `local.git` | exact-parent, exact-path local Git host |
| `code_review.pull_request.create` | `github.code_review` | GitHub HTTPS create plus exact-head verification read |
| `ci.run.wait` | `github.actions` | existing durable event-driven wait/resume adapter |

`build_concrete_starter_provider_registry()` assembles those implementations.
Provider availability still does not activate a capability. A mutating adapter
is called only after the existing `WriteAuthorityGuard` accepts the active
binding and TaskRun state.

## Structured workspace transaction

`workflow-workspace-mutation-request@1` accepts only `create`, `replace`, and
`delete` operations. Every path is canonical and project-relative. Create and
replace content includes its SHA-256; replace and delete include the exact
current file SHA-256.

The embedding supplies a trusted `allowed_path_patterns` policy, normally from
the verified Starter project declaration plus the active ChangePermit. Request
data cannot broaden that policy. `.git`, `.harness`, and `.quality` remain
protected control paths.

The adapter preflights every operation before the first write, rejects symlink
components and duplicate paths, writes files atomically, verifies all
postconditions, and rolls back already-applied operations when a later effect
fails. Its receipt binds each path to before/after digests and explicitly records
that it changed no authority.

This adapter supports Workflows that model patch application as an explicit
`workspace.write` executor step. A model-driven mutating Skill may instead edit
through the ChatGPT/Codex Host's structured tools; that path still passes the
same existing write Guard before the real Skill Host is invoked and must return
durable patch evidence before a Skill receipt is produced.

## Local Git commit

`SubprocessLocalGitPublicationHost` invokes a fixed Git argument sequence without
a shell. Workflow state cannot provide an executable, flags, environment, or
command text.

Before commit it requires:

- the configured workspace is the repository root;
- current `HEAD` equals `expected_parent_sha`;
- `HEAD` is attached to a named branch;
- the Git index is clean;
- tracked and untracked worktree changes equal the requested changed-path set;
- no requested path targets Harness control data.

After commit it re-reads the commit SHA, parent, and tree paths. Only an exact
match becomes `PublicationHostResult`; Git or hook failure becomes blocked
evidence and never a fabricated receipt.

## GitHub pull-request creation

`GitHubPullRequestPublicationHost` receives one fixed repository and a token
through runtime configuration. It sends the fixed GitHub REST create request,
then performs a separate GET for the created PR. The verification read must
match:

- repository full name;
- positive PR number and HTTPS URL;
- base branch;
- head branch;
- exact 40-character head SHA;
- requested draft state.

The token is used only in the HTTPS authorization header. It is excluded from
configuration representation, receipts, evidence references, and transport
error messages. Missing credentials, HTTP failure, invalid JSON, repository
mismatch, or head movement blocks the step.

This concrete Host intentionally does not implement merge. Customer Agent
Starter Workflows stop after exact PR creation and CI evidence. A different
governed publication Workflow may use the existing independent MergeAuthority
boundary, but it is not part of this assembly.

## ChatGPT/Codex embedding

The Host still supplies model-driven Skill execution and the existing write
authority object. Deterministic effects can be assembled once:

```python
github = GitHubPullRequestConfiguration.from_environment(
    repository_full_name="owner/customer-agent",
)
providers = build_concrete_starter_provider_registry(
    workspace=project_root,
    write_scope=registration.payload["project"]["write_scope"],
    allowed_profiles={
        "test.run": ["customer-agent-test"],
        "quality.evaluate": ["customer-agent-quality"],
    },
    github=github,
)

runtime = StarterWorkflowRuntime(
    resolved=resolved_entrypoint,
    skill_host=chatgpt_or_codex_skill_host,
    provider_adapters=providers.registry,
    write_authority_guard=existing_write_guard,
    checkpointer=durable_saver,
    taskrun_store=existing_taskrun,
    workspace_fingerprint=current_fingerprint,
    registry_workspace=harness_root,
)
```

Natural language remains a Host convenience. A write request is still reduced
to one exact `/harness` entrypoint and effect preview before runtime execution.

## End-to-end proof

The runtime regression installs the Customer Agent Starter in a temporary
project, uses a real mutating Skill Host fixture to create one bounded source
change, runs allow-listed test and Quality profiles, creates a real local Git
commit, creates and re-reads an exact-head GitHub PR through the transport
boundary, and reaches durable `WAITING_EXTERNAL` for CI.

The proof asserts that TaskRun is not `COMPLETED` and that automatic merge is
false. CI success will later resume the same TaskRun and Graph END will still
project only to `VALIDATING`.

## Standalone project boundary

The generated customer Agent imports none of these Harness modules. The
workspace mutation request, Skill receipts, Provider evidence, TaskRun, and
checkpointer are development artifacts. Once extracted, the project continues
to run, test, package, and deploy with its own source and declared dependencies;
removing `.harness` removes development automation, not application runtime.
