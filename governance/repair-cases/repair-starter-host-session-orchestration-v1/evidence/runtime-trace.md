# Runtime trace evidence

The installed Customer Agent overall-audit test produced this controlled trace:

1. `open()` -> revision 0, `AWAITING_SELECTION`,
   `SELECT_EXACT_ENTRYPOINT`.
2. exact `overall_audit` selection -> revision 1, `READY_TO_START`,
   `START_TASKRUN`.
3. `start()` claims `STARTING`, creates one stable TaskRun, invokes the existing
   graph, and persists revision 3 as `WAITING_HOST` /
   `EXECUTE_HOST_SKILL` for `customer-agent-audit`.
4. validated Host `findings` result claims `RESUMING_HOST`, resumes the same
   Skill step, creates one canonical Skill receipt, and persists revision 5 as
   another `WAITING_HOST` for the composed standards gate.
5. validated Host `continue` result resumes the same TaskRun, creates the second
   canonical receipt, dispatches deterministic Quality, and persists revision 7
   as `VALIDATING` / `EVALUATE_COMPLETION_POLICY`.
6. The durable TaskRun binding still names the same Host session and its status is
   `VALIDATING`, not `COMPLETED`.

Two concurrent revision-0 selection attempts were synchronized; one persisted
revision 1 and the other failed without overwriting the winner.

