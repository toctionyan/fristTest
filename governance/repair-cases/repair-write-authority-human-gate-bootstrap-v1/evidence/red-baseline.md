# Red baseline

At merge commit `90b4f3e10bc66dd110f005b07dd4a01dda39d47f`, the built-in concrete Host factory deliberately passes `write_authority_guard=None` and `human_gate_adapter=None` to `StarterHostOrchestrator`.

Consequences:

- every mutating Skill or Provider dispatch fails closed even when the project has a valid active ChangePermit;
- a verified Starter Workflow containing a `human_gate` step cannot produce a durable gate contract through the concrete factory;
- Host `RESUME_HUMAN` can carry evidence references, but no concrete adapter binds an exact decision artifact to the waiting task/workflow/step;
- the initializer exposes no generated, closed authority/gate bootstrap policy.

The lower dispatcher, TaskRun bridge, resumable LangGraph runtime, repair-governance ChangePermit validator, Host transport, and project-local durable storage already exist. The missing defect is one concrete, fail-closed composition of those existing authorities.
