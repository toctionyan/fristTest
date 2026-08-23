# Red baseline

Command:

```text
python3 -B -c 'import sys; sys.path.insert(0,"skill-system/controller"); import host_skill_bridge'
```

Observed result on exact parent `a921cc5da61f84c683ed9e101e660e40f78b7a80`:

```text
ModuleNotFoundError: No module named 'host_skill_bridge'
```

The current runtime requires an injected synchronous `SkillHostAdapter`, but it
has no durable ChatGPT/Codex request/result handoff. A repository process cannot
pause a Skill step, let the real Host load the exact `SKILL.md` and execute
structured tools, then resume the same TaskRun and LangGraph checkpoint. The
strict slash-command router exists, but a natural-language Host selection cannot
be reduced to an exact Starter entrypoint with a digest-bound effect preview and
explicit confirmation for mutating routes.

