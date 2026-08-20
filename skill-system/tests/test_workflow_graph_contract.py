from __future__ import annotations

import sys
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from workflow_graph_contract import (  # type: ignore
    WorkflowGraphContractError,
    parse_workflow_graph,
)


class WorkflowGraphContractTest(unittest.TestCase):
    def parse(self, graph):
        return parse_workflow_graph(
            graph,
            workflow_id="test-workflow",
            skills=("repair",),
            required_capabilities=("test.run", "ci.run.wait"),
        )

    def test_rejects_unreachable_step(self) -> None:
        graph = {
            "start": "repair",
            "steps": {
                "repair": {
                    "type": "skill",
                    "use": "repair",
                    "routes": {"success": "END"},
                },
                "orphan": {
                    "type": "executor",
                    "use": "test.run",
                    "routes": {"green": "END"},
                },
            },
        }
        with self.assertRaisesRegex(WorkflowGraphContractError, "unreachable steps"):
            self.parse(graph)

    def test_rejects_unknown_route_target(self) -> None:
        graph = {
            "start": "repair",
            "steps": {
                "repair": {
                    "type": "skill",
                    "use": "repair",
                    "routes": {"success": "missing-step"},
                }
            },
        }
        with self.assertRaisesRegex(WorkflowGraphContractError, "unknown target"):
            self.parse(graph)

    def test_external_wait_requires_explicit_wait_terminal(self) -> None:
        graph = {
            "start": "wait",
            "steps": {
                "wait": {
                    "type": "external_wait",
                    "use": "ci.run.wait",
                    "routes": {"ready": "END"},
                }
            },
        }
        with self.assertRaisesRegex(WorkflowGraphContractError, "route to WAITING_EXTERNAL"):
            self.parse(graph)

    def test_human_gate_cannot_bind_provider_or_skill(self) -> None:
        graph = {
            "start": "human",
            "steps": {
                "human": {
                    "type": "human_gate",
                    "use": "repair",
                    "routes": {"needs-human": "HUMAN_GATE"},
                }
            },
        }
        with self.assertRaisesRegex(WorkflowGraphContractError, "cannot bind"):
            self.parse(graph)

    def test_rejects_skill_not_declared_by_workflow(self) -> None:
        graph = {
            "start": "audit",
            "steps": {
                "audit": {
                    "type": "skill",
                    "use": "unknown-skill",
                    "routes": {"success": "END"},
                }
            },
        }
        with self.assertRaisesRegex(WorkflowGraphContractError, "not declared by workflow"):
            self.parse(graph)


if __name__ == "__main__":
    unittest.main()
