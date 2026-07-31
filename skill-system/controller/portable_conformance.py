from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST_FILES = (
    "skillctl.py",
    "AGENTS.md",
    "CLAUDE.md",
    ".agents/skills/product-code-governance/SKILL.md",
    ".claude/skills/product-code-governance/SKILL.md",
    ".codex/agents/product-implementer.toml",
    ".claude/agents/product-implementer.md",
)
CANONICAL_COMMANDS = (
    "skillctl.py product-init",
    "skillctl.py product-baseline",
    "skillctl.py product-verify",
    "skillctl.py contract-verify",
    "skillctl.py contract-close",
)


def verify() -> list[str]:
    errors: list[str] = []
    for relative in HOST_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"missing_portable_host_file:{relative}")
    for relative in ("AGENTS.md", "CLAUDE.md", ".agents/skills/product-code-governance/SKILL.md", ".claude/skills/product-code-governance/SKILL.md"):
        path = ROOT / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for command in CANONICAL_COMMANDS:
            if command not in text:
                errors.append(f"portable_command_missing:{relative}:{command}")
    return errors


def main() -> int:
    errors = verify()
    print(json.dumps({"status": "PASS" if not errors else "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
