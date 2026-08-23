# Red baseline: Customer Agent runtime integration

Date: 2026-08-23

The verified Customer Agent Starter can be listed, copied, and compiled, but it
cannot be registered as an immutable runtime input or invoked by one of its six
entrypoints. Its seven Skill files are contracts only: no package-bound
`SKILL.md` implementation exists for the real Host to load. The existing
invocation path only activates Workflow IDs already stored in the repository's
static registries, so an installed Starter cannot reach the existing Dispatcher,
LangGraph runtime, durable checkpointer, or TaskRun bridge.
