# Authority boundary

The hard-link primitive owns only first-writer file publication. It does not
interpret Host output or grant authority. All #2095 authorities remain unchanged:
the existing write guard owns mutation permission, CanonicalSkillInvocationAdapter
owns Skill receipts, LangGraph owns routing, TaskRun owns lifecycle/completion,
Quality/Judge owns acceptance, and Customer Agent Starter exposes no merge step.

