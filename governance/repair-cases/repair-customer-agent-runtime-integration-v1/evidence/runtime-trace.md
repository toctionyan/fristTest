# Runtime trace

The executable Starter runtime test produced this lifecycle:

1. existing TaskRun `CREATED`;
2. `WORKFLOW_RUNTIME_STARTED` and `RUNNING`;
3. real `customer-agent-repair` and adversarial Skill Host calls with
   `skill-invocation-receipt@1` evidence;
4. injected local test/quality, VCS commit, and PR adapters;
5. `ci.run.wait` returned a durable handle and TaskRun became
   `WAITING_EXTERNAL_RESULT / WORKFLOW_WAITING_EXTERNAL`;
6. a mismatched correlation was rejected without changing TaskRun state;
7. the matching `ci.completed` event recorded `WORKFLOW_RUNTIME_RESUMED` and
   re-entered the declared wait node on the same durable thread;
8. Graph END projected to `VALIDATING /
   WORKFLOW_GRAPH_ENDED_AWAITING_COMPLETION_POLICY`.

The TaskRun never became `COMPLETED`, and no merge capability ran.
