# Runtime trace evidence

The hardened focused suite produced these controlled traces.

External event:

1. exact read-only entrypoint -> `READY_TO_START`;
2. start -> one TaskRun and `WAITING_EXTERNAL`;
3. empty evidence -> rejected before a transition claim;
4. exact `ci.completed` event/correlation/evidence -> `RESUMING_EXTERNAL`;
5. same TaskRun -> graph END -> `VALIDATING` /
   `EVALUATE_COMPLETION_POLICY`.

Human Gate:

1. exact read-only entrypoint -> one TaskRun and `HUMAN_GATE`;
2. empty decision evidence -> rejected before a transition claim;
3. explicit `approve` plus evidence -> `RESUMING_HUMAN`;
4. same TaskRun -> graph END -> `VALIDATING`, never `COMPLETED`.

Crash recovery:

1. simulated process death after durable graph/TaskRun start leaves the Session
   at sealed `STARTING`;
2. `reconcile()` adopts the existing non-running SQLite checkpoint and returns
   `WAITING_EXTERNAL` without invoking the completed start again;
3. simulated death after the external resume graph ended leaves sealed
   `RESUMING_EXTERNAL`;
4. `reconcile()` adopts the newer END checkpoint and returns the same TaskRun at
   `VALIDATING`;
5. a separate death after only TaskRun resume, with the graph still at the old
   wait, is rejected as ambiguous and recorded `BLOCKED`.
