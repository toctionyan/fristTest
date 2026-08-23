# Runtime trace

The installed Customer Agent `overall_audit` end-to-end test used a durable
SQLite LangGraph saver and one TaskRun:

1. runtime start reached `customer-agent-audit`;
2. the bridge persisted the exact request and returned `WAITING_HOST`;
3. TaskRun became `WAITING_EXTERNAL_RESULT / WORKFLOW_WAITING_HOST`;
4. no canonical Skill receipt existed;
5. a matching Codex result with a structured `workspace.read` receipt was
   submitted and the same Skill step resumed;
6. `CanonicalSkillInvocationAdapter` created the first
   `skill-invocation-receipt@1`;
7. the composed `customer-agent-standards-gate` created a different Host request
   and yielded the same TaskRun again;
8. its matching result resumed the same Workflow/checkpointer thread and created
   the second canonical receipt;
9. the existing Quality Provider ran green;
10. Graph END projected TaskRun to
    `VALIDATING / WORKFLOW_GRAPH_ENDED_AWAITING_COMPLETION_POLICY`.

The test asserted two distinct Host execution IDs, exactly two canonical Skill
receipts after the corresponding results, and no TaskRun `COMPLETED` transition.

