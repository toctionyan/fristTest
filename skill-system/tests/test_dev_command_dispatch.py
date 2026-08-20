from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from dev_command import (  # type: ignore
    COMMANDS,
    DEV_COMMAND_ROUTE_SCHEMA,
    DevCommandError,
    build_route,
    parse_command_text,
)
from scope_guard import bootstrap_command_allowed  # type: ignore
from skill_invocation import require_invocation  # type: ignore


class DevCommandDispatchTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="dev-command-"))
        for spec in COMMANDS.values():
            for name in spec.skills:
                skill = root / f"skill-system/skills/{name}/SKILL.md"
                skill.parent.mkdir(parents=True, exist_ok=True)
                if not skill.exists():
                    skill.write_text(
                        f"---\nname: {name}\ndescription: test\n---\n\n# {name}\n",
                        encoding="utf-8",
                    )
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_arch_command_preserves_free_form_payload_and_routes_exact_skill(self) -> None:
        workspace = self.workspace()
        payload = "ContextStore 职责太重。\n不要增加第二个 Owner。\n请比较三种方案。"
        route = build_route(
            workspace,
            command="/arch",
            payload=payload,
            invocation_prefix="arch-1",
        )
        self.assertEqual(route["schema"], DEV_COMMAND_ROUTE_SCHEMA)
        self.assertEqual(route["command"], "/arch")
        self.assertEqual(route["request_class"], "DESIGN")
        self.assertEqual(route["required_skills"], ["architecture-options"])
        self.assertEqual(route["user_payload"], payload)
        self.assertFalse(route["policy"]["natural_language_keyword_rerouting_allowed"])
        self.assertFalse(route["policy"]["fallback_without_required_skill_allowed"])
        require_invocation(
            workspace,
            request_class="DESIGN",
            skill="architecture-options",
        )

    def test_agent_arch_requires_both_architecture_skills_without_eviction(self) -> None:
        workspace = self.workspace()
        route = build_route(
            workspace,
            command="/agent-arch",
            payload="检查多意图规划和上下文 Owner。",
            invocation_prefix="agent-arch-1",
        )
        self.assertEqual(
            route["required_skills"],
            ["architecture-options", "customer-agent-architecture"],
        )
        for skill in route["required_skills"]:
            receipt = require_invocation(
                workspace,
                request_class="DESIGN",
                skill=skill,
            )
            self.assertEqual(receipt["selected_skill"], skill)

    def test_repair_routes_governance_and_red_baseline_but_keeps_change_scope_as_write_gate(self) -> None:
        workspace = self.workspace()
        route = build_route(
            workspace,
            command="/repair",
            payload="按刚才确定的方案修复，不保留旧主链。",
            invocation_prefix="repair-1",
            change_id="change-123",
        )
        self.assertEqual(
            route["required_skills"],
            ["product-code-governance", "red-baseline-repair"],
        )
        self.assertTrue(route["policy"]["write_requires_change_scope"])
        self.assertNotIn("change-scope", route["required_skills"])
        for skill in route["required_skills"]:
            require_invocation(
                workspace,
                request_class="REPAIR",
                skill=skill,
                change_id="change-123",
            )

    def test_status_and_continue_both_force_authoritative_status_first(self) -> None:
        workspace = self.workspace()
        for index, command in enumerate(("/status", "/continue"), start=1):
            with self.subTest(command=command):
                route = build_route(
                    workspace,
                    command=command,
                    payload="辅助说明可以保留。",
                    invocation_prefix=f"status-{index}",
                    task_id="task-123",
                )
                self.assertEqual(route["required_skills"], ["task-execution-status"])
                self.assertTrue(route["policy"]["status_first"])
                self.assertTrue(route["policy"]["deterministic_response_required"])
                self.assertIn("task-status-project", route["next"])
                require_invocation(
                    workspace,
                    request_class="STATUS_QUERY",
                    skill="task-execution-status",
                    task_id="task-123",
                )

    def test_parse_command_text_treats_only_first_token_as_route(self) -> None:
        command, payload = parse_command_text(
            "  /arch 先看 ContextStore\n再比较保守、演进、重构方案\n暂时不要改代码"
        )
        self.assertEqual(command, "/arch")
        self.assertEqual(
            payload,
            "先看 ContextStore\n再比较保守、演进、重构方案\n暂时不要改代码",
        )

    def test_unknown_command_fails_closed_instead_of_falling_back(self) -> None:
        with self.assertRaisesRegex(DevCommandError, "unsupported development command"):
            parse_command_text("/something-else do work")

    def test_dev_command_is_bootstrap_safe_for_supported_repository_hosts(self) -> None:
        self.assertTrue(
            bootstrap_command_allowed(
                "python3 -B skillctl.py dev-command --command /arch --payload x --invocation-prefix x"
            )
        )


if __name__ == "__main__":
    unittest.main()
