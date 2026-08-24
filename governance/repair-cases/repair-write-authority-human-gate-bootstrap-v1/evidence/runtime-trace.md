# Runtime trace

The concrete v2 bootstrap trace is:

```text
host-init
  -> verified Starter registration
  -> concrete-host-bootstrap@2
build_orchestrator
  -> existing Provider registry
  -> project-local SqliteSaver
  -> ChangePermitWriteAuthorityGuard
  -> DurableHumanGateAdapter
mutating dispatcher call
  -> reload implementing active-change.json
  -> repair_governance.load_chain
  -> exact change_id + permit_digest
  -> exact request paths
  -> repair_governance.permit_path_decision per path
  -> existing Provider effect
human_gate call
  -> persist exact task/workflow/step/routes gate
  -> TaskRun BLOCKED / WORKFLOW_HUMAN_GATE
  -> authoring human-decision creates sealed file
  -> RESUME_HUMAN re-enters same step
  -> exact persisted decision validates
  -> declared outcome continues
Graph END
  -> TaskRun VALIDATING
```

Focused tests executed each new local edge. Existing runtime/orchestrator tests prove the durable wait/resume and TaskRun projections remain unchanged.
