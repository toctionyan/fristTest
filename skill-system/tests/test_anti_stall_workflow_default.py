from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
SKILL = ROOT / "skill-system" / "skills" / "product-code-governance" / "SKILL.md"


def _load(name: str):
    path = CONTROLLER / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"anti_stall_contract_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    if str(CONTROLLER) not in sys.path:
        sys.path.insert(0, str(CONTROLLER))
    spec.loader.exec_module(module)
    return module


class AntiStallWorkflowDefaultTests(unittest.TestCase):
    def test_portable_skill_contract_matches_executable_safety_defaults(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        anti_stall = _load("anti_stall")
        bounded_batch = _load("bounded_batch")

        self.assertIn("远程输入 Anti-Stall（WORKFLOW_DEFAULT）", text)
        self.assertIn("max_remote_calls = 2", text)
        self.assertIn("max_parallel <= 4", text)
        self.assertIn("source + immutable ref + path", text)
        self.assertIn("禁止同 Tool 原地重试", text)
        self.assertIn("不得继续临时寻找第三、第四条远程路径", text)
        self.assertIn("skill-system/controller/task_harness.py", text)
        self.assertIn("不能声称仓库代码已经拦截平台级 Connector", text)

        policy = anti_stall.AtomicToolPolicy()
        self.assertEqual(policy.budget.max_remote_calls, 2)
        self.assertEqual(bounded_batch.MAX_PARALLEL_REMOTE_READS, 4)

    def test_workflow_default_does_not_replace_quality_loop_authority(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn(
            "不得改变 Quality Loop Gate、Claim、repair-round、convergence",
            text,
        )
        self.assertIn("实现参考而非第二主链", text)


if __name__ == "__main__":
    unittest.main()
