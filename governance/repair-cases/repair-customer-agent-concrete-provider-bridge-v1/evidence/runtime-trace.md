# Runtime trace

The installed-Starter regression executes this real effect sequence in a
temporary project:

1. install and register the Customer Agent Starter;
2. initialize a real Git repository and create a feature branch;
3. run `customer-agent-repair-with-ci` through `StarterWorkflowRuntime`;
4. invoke the guarded mutating Skill Host and materialize `src/fix.py`;
5. execute allow-listed focused test and Quality profiles;
6. create a real exact-parent commit containing only `src/fix.py`;
7. send GitHub PR create through the transport and GET the PR again;
8. prove the returned head equals the created commit;
9. persist `WAITING_EXTERNAL` for `github.actions` with exact correlation.

Observed terminal state for this phase: Workflow `WAITING_EXTERNAL`, TaskRun
`WAITING_EXTERNAL_RESULT`, automatic merge false, TaskRun not `COMPLETED`.
