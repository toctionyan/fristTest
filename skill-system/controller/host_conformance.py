from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = (
    "change-scope",
    "architecture-options",
    "red-baseline-repair",
    "oracle-review",
    "adversarial-review",
    "release-certification",
    "customer-agent-architecture",
    "product-code-governance",
    "task-execution-status",
)
DEV_COMMAND_HOST_BINDINGS = {
    "architecture-options": ("/arch", "/agent-arch"),
    "customer-agent-architecture": ("/agent-arch",),
    "oracle-review": ("/oracle",),
    "product-code-governance": ("/diagnose", "/repair"),
    "red-baseline-repair": ("/repair",),
    "adversarial-review": ("/review",),
    "release-certification": ("/cert",),
    "task-execution-status": ("/status", "/continue"),
}
ROLES = (
    "scope-planner",
    "skill-implementer",
    "oracle-reviewer",
    "adversarial-reviewer",
    "release-judge",
    "product-implementer",
    "failure-explorer",
    "repair-plan-reviewer",
    "diff-integrity-reviewer",
    "closure-arbiter",
)


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def verify(strict: bool = False) -> list[str]:
    errors: list[str] = []
    for root in (ROOT / ".agents" / "skills", ROOT / ".claude" / "skills"):
        for name in SKILLS:
            path = root / name / "SKILL.md"
            if not path.is_file():
                errors.append(f"missing_host_skill:{path.relative_to(ROOT)}")
                continue
            text = path.read_text(encoding="utf-8")
            fm = _frontmatter(path)
            if fm.get("name") != name or not fm.get("description"):
                errors.append(f"invalid_host_skill_frontmatter:{path.relative_to(ROOT)}")
            if f"skill-system/skills/{name}/SKILL.md" not in text:
                errors.append(f"host_skill_not_thin_adapter:{path.relative_to(ROOT)}")
            commands = DEV_COMMAND_HOST_BINDINGS.get(name, ())
            if commands:
                if "skillctl.py dev-command" not in text:
                    errors.append(f"host_skill_missing_dev_command_dispatch:{path.relative_to(ROOT)}")
                for command in commands:
                    if command not in text:
                        errors.append(
                            f"host_skill_missing_explicit_command:{path.relative_to(ROOT)}:{command}"
                        )

    status_skill = ROOT / "skill-system" / "skills" / "task-execution-status" / "SKILL.md"
    if not status_skill.is_file():
        errors.append("missing_canonical_status_skill")
    else:
        status_text = status_skill.read_text(encoding="utf-8")
        if "task-status-project" not in status_text or "execution-progress@1" not in status_text:
            errors.append("status_skill_missing_canonical_projection_contract")

    for relative in (
        "skill-system/controller/skill_invocation.py",
        "skill-system/controller/skill_invocation_cli.py",
        "skill-system/controller/dev_command.py",
    ):
        if not (ROOT / relative).is_file():
            errors.append(f"missing_skill_invocation_control:{relative}")

    command_doc = ROOT / "docs" / "architecture" / "MINIMAL_DEV_HARNESS_COMMANDS.md"
    if not command_doc.is_file():
        errors.append("missing_minimal_dev_harness_command_contract")

    skillctl = ROOT / "skillctl.py"
    if not skillctl.is_file():
        errors.append("missing_skillctl")
    else:
        skillctl_text = skillctl.read_text(encoding="utf-8")
        for command in ("skill-load", "skill-invocation-verify", "task-status-project", "dev-command"):
            if command not in skillctl_text:
                errors.append(f"missing_skillctl_invocation_command:{command}")

    if not (ROOT / "AGENTS.md").is_file():
        errors.append("missing_AGENTS.md")
    if not (ROOT / "CLAUDE.md").is_file():
        errors.append("missing_CLAUDE.md")

    config = ROOT / ".codex" / "config.toml"
    if not config.is_file() or "PreToolUse" not in config.read_text(encoding="utf-8") or "Stop" not in config.read_text(encoding="utf-8"):
        errors.append("invalid_codex_hook_config")
    settings = ROOT / ".claude" / "settings.json"
    if not settings.is_file():
        errors.append("missing_claude_settings")
    else:
        try:
            payload = json.loads(settings.read_text(encoding="utf-8"))
            hooks = payload.get("hooks") or {}
            for event in ("PreToolUse", "PostToolUse", "Stop"):
                if event not in hooks:
                    errors.append(f"missing_claude_hook:{event}")
        except json.JSONDecodeError:
            errors.append("invalid_claude_settings_json")

    # Static adapter presence is not runtime invocation proof. Supported repository
    # hosts must at least have fail-closed guards that consume durable invocation evidence.
    for event, relative in (
        ("PreToolUse", "skill-system/hooks/pre_tool_use.py"),
        ("PostToolUse", "skill-system/hooks/post_tool_use.py"),
        ("Stop", "skill-system/hooks/stop_guard.py"),
    ):
        path = ROOT / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if "require_change_scope_invocation" not in text:
            errors.append(f"runtime_invocation_guard_missing:{event}")

    for role in ROLES:
        if not (ROOT / ".claude" / "agents" / f"{role}.md").is_file():
            errors.append(f"missing_claude_agent:{role}")
        if not (ROOT / ".codex" / "agents" / f"{role}.toml").is_file():
            errors.append(f"missing_codex_agent:{role}")
    if strict:
        for role in (
            "scope-planner",
            "oracle-reviewer",
            "adversarial-reviewer",
            "release-judge",
            "failure-explorer",
            "repair-plan-reviewer",
            "diff-integrity-reviewer",
            "closure-arbiter",
        ):
            if 'sandbox_mode = "read-only"' not in (ROOT / ".codex" / "agents" / f"{role}.toml").read_text(encoding="utf-8"):
                errors.append(f"codex_agent_not_read_only:{role}")
            text = (ROOT / ".claude" / "agents" / f"{role}.md").read_text(encoding="utf-8")
            if "disallowedTools: Write, Edit" not in text:
                errors.append(f"claude_agent_not_read_only:{role}")
    product_codex = ROOT / ".codex" / "agents" / "product-implementer.toml"
    product_claude = ROOT / ".claude" / "agents" / "product-implementer.md"
    if product_codex.is_file() and 'sandbox_mode = "workspace-write"' not in product_codex.read_text(encoding="utf-8"):
        errors.append("codex_product_implementer_not_writable")
    if product_claude.is_file():
        product_text = product_claude.read_text(encoding="utf-8")
        if "tools: Read, Grep, Glob, Bash, Write, Edit" not in product_text:
            errors.append("claude_product_implementer_not_writable")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    errors = verify(args.strict)
    print(json.dumps({"status": "PASS" if not errors else "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
