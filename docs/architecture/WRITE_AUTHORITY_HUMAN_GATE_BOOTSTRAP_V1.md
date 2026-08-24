# Write Authority / Human Gate Bootstrap v1

## Outcome

The concrete ChatGPT/Codex Host can now run governed mutating Workflows without an allow-all switch. It composes two existing boundaries:

```text
active Change Contract + ChangePermit
                  ↓ exact change_id / permit_digest / paths
ChangePermitWriteAuthorityGuard
                  ↓ allow before effect
WorkflowAdapterDispatcher → existing Provider

verified human_gate step
                  ↓ persisted gate contract
TaskRun BLOCKED / WORKFLOW_HUMAN_GATE
                  ↓ explicit sealed decision file
same LangGraph step resumes
```

The write guard does not issue permits. The Human Gate adapter does not authorize writes. They adapt existing authority and durable decisions to the already established dispatcher/runtime protocols.

## Automatic write path

The target project must already contain an implementing `governance/active-change.json` whose `repair_governance` points to one valid active ChangePermit chain. `START.payload.target_ref` carries the exact identity:

```json
{
  "change_id": "repair-customer-routing-v1",
  "permit_digest": "<exact active permit digest>",
  "workspace_requests": {
    "workspace.write": {
      "schema": "workflow-workspace-mutation-request@1",
      "capability_id": "workspace.write",
      "operations": [
        {
          "operation": "replace",
          "path": "src/routing.py",
          "expected_sha256": "<current digest>",
          "content": "<new UTF-8 source>",
          "content_sha256": "<new digest>"
        }
      ]
    }
  }
}
```

For every mutating Skill or Provider dispatch, the guard:

1. reloads the project-local active contract;
2. requires `status=implementing`;
3. validates the complete existing ChangePermit chain through `repair_governance.load_chain`;
4. matches `target_ref.change_id` and `target_ref.permit_digest` exactly;
5. extracts all requested paths before the effect;
6. delegates each path decision to `repair_governance.permit_path_decision`;
7. writes a non-authorizing audit record under `.harness/runtime/authority-checks/`.

The same pre-effect rule applies to `vcs.commit.create` and `code_review.pull_request.create`; both requests must include `changed_paths`. The generic guard rejects `code_review.pull_request.merge` regardless of the permit. Merge still needs its independent stronger MergeAuthorityGuard and is not enabled by this bootstrap.

This is why normal bounded repairs can proceed automatically after governance is ready: no human has to approve each file write, test, commit, or PR creation. Missing/stale governance or scope drift blocks instead of asking the model to reinterpret policy.

## Human Gate path

A verified Workflow may declare a `human_gate` step. The concrete adapter derives the waiting outcome and decision outcomes only from that verified step's route table, then persists:

```text
.harness/runtime/human-gates/<task>/<workflow>/<step>.json
```

The Host returns its `gate_ref`, digest, question, and allowed outcomes. After the user chooses one outcome, ChatGPT/Codex runs:

```bash
python3 -B skillctl.py authoring human-decision \
  --project-workspace /path/to/project \
  --gate-ref file:.harness/runtime/human-gates/<task>/<workflow>/<step>.json \
  --outcome approve \
  --actor operator-id
```

The command creates a new sealed file under `.harness/runtime/human-decisions/<gate-id>/` and refuses overwrite. The subsequent `RESUME_HUMAN` command sends the returned decision object and file reference. The adapter resumes only when all of these still match:

- gate ID and gate digest;
- TaskRun, Workflow, and step identity;
- verified route choices;
- selected outcome;
- actor and timezone-qualified decision time;
- decision fingerprint and exact persisted file content.

An inline answer without the file, a decision for another task, changed routes, altered content, or an undeclared outcome fails closed.

## Extension rules

- New mutating capabilities need a dedicated exact-scope extractor before they can be added to the generic guard's supported set. Unknown mutations stay blocked.
- New Human Gate behavior is expressed in verified Workflow routes. The adapter does not require handwritten YAML or Python for each gate.
- Richer question text can later be added through a versioned Workflow contract; it must not become a new decision or authority source.
- External approval systems may implement the same sealed decision contract, but the durable TaskRun/Workflow/gate identity and evidence rules remain unchanged.

## Preserved boundaries

- Capability binding does not grant write authority.
- Host selection and `CONFIRM` do not grant write authority.
- Human Gate decision does not grant write or merge authority.
- CI green and Quality green do not complete the TaskRun.
- LangGraph `END` transitions the TaskRun to `VALIDATING`, never `COMPLETED`.
- No automatic merge, release, deployment, or production closure is added.
- Application source and runtime dependencies remain independent of `.harness/**` and the development Harness.
