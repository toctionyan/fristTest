from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from host_skill_bridge import (  # type: ignore
    DurableHostSkillBridge,
    HOST_REQUEST_SCHEMA,
    HOST_RESULT_SCHEMA,
    HOST_TOOL_RECEIPT_SCHEMA,
    HostSkillBridgeError,
)
from langgraph_workflow_runtime import HostExecutionPending  # type: ignore
from workflow_graph_contract import WorkflowStepSpec  # type: ignore


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DurableHostSkillBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="host-skill-bridge-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.skill_path = Path(".harness/starters/customer-agent/skills/audit/SKILL.md")
        target = self.root / self.skill_path
        target.parent.mkdir(parents=True)
        target.write_text("# Audit\nInspect the project.\n", encoding="utf-8")
        self.bridge = DurableHostSkillBridge(
            workspace=self.root,
            host_id="codex",
            canonical_skill_paths={"customer-agent-audit": self.skill_path},
        )
        self.step = WorkflowStepSpec(
            step_id="audit",
            step_type="skill",
            use="customer-agent-audit",
            routes={"findings": "END", "blocked": "BLOCKED_UNRECOVERABLE"},
            max_attempts=2,
        )
        self.state = {
            "task_id": "task-host-1",
            "workflow_id": "customer-agent-audit",
            "step_attempts": {},
            "target_ref": {"kind": "project", "ref": "customer-agent", "user_payload": "检查总体问题"},
        }

    def pending(self):
        with self.assertRaises(HostExecutionPending) as caught:
            self.bridge.execute(
                skill_name="customer-agent-audit",
                request_class="AUDIT",
                step=self.step,
                state=self.state,
            )
        return caught.exception

    def request(self, pending: HostExecutionPending) -> dict:
        ref = pending.host_wait["request_ref"].removeprefix("file:")
        return json.loads((self.root / ref).read_text(encoding="utf-8"))

    def result(self, request: dict, *, mutates: bool = False) -> dict:
        output = '{"findings":["context-loss"]}'
        return {
            "schema": HOST_RESULT_SCHEMA,
            "execution_id": request["execution_id"],
            "request_fingerprint_sha256": request["request_fingerprint_sha256"],
            "host_id": "codex",
            "status": "PASS",
            "loaded_skill": dict(request["skill"]),
            "outcome": "findings",
            "output": {
                "schema": "customer-agent-audit-report@1",
                "content": output,
                "sha256": digest(output),
                "evidence_ref": "file:.harness/host-evidence/audit-output.json",
            },
            "tool_receipts": [
                {
                    "schema": HOST_TOOL_RECEIPT_SCHEMA,
                    "tool_call_id": "tool-1",
                    "tool_name": "workspace.read",
                    "arguments_sha256": digest("args"),
                    "result_sha256": digest("result"),
                    "evidence_ref": "file:.harness/host-evidence/tool-1.json",
                    "mutates": mutates,
                    "write_authority_checked": mutates,
                }
            ],
            "evidence_refs": ["file:.harness/host-evidence/audit-output.json"],
            "payload": {"finding_count": 1},
            "problem_ledger_ref": "file:.harness/problem-ledger.json",
            "authority_effect": False,
        }

    def test_request_wait_and_matching_result_resume_are_digest_bound(self) -> None:
        pending = self.pending()
        request = self.request(pending)
        self.assertEqual(request["schema"], HOST_REQUEST_SCHEMA)
        self.assertEqual(request["task_id"], "task-host-1")
        self.assertEqual(request["workflow_id"], "customer-agent-audit")
        self.assertEqual(request["step_id"], "audit")
        self.assertEqual(request["skill"]["path"], self.skill_path.as_posix())
        self.assertEqual(pending.host_wait["skill_sha256"], request["skill"]["sha256"])
        self.assertFalse((self.root / ".quality/skill-invocations").exists())

        pointer = self.bridge.submit_result(
            execution_id=request["execution_id"],
            result=self.result(request),
        )
        resumed_state = {**self.state, "host_execution_result": pointer}
        host_result = self.bridge.execute(
            skill_name="customer-agent-audit",
            request_class="AUDIT",
            step=self.step,
            state=resumed_state,
        )
        self.assertEqual(host_result.outcome, "findings")
        self.assertEqual(host_result.payload["finding_count"], 1)
        self.assertIn(pointer["result_ref"], host_result.evidence_refs)
        self.assertFalse((self.root / ".quality/skill-invocations").exists())

    def test_wrong_binding_tamper_and_unguarded_mutation_fail_closed(self) -> None:
        pending = self.pending()
        request = self.request(pending)
        cases = []
        wrong_request = self.result(request)
        wrong_request["request_fingerprint_sha256"] = "0" * 64
        cases.append(("request fingerprint", wrong_request))
        wrong_host = self.result(request)
        wrong_host["host_id"] = "chatgpt"
        cases.append(("host_id", wrong_host))
        wrong_skill = self.result(request)
        wrong_skill["loaded_skill"] = {**request["skill"], "sha256": "1" * 64}
        cases.append(("exact requested Skill", wrong_skill))
        wrong_output = self.result(request)
        wrong_output["output"]["sha256"] = "2" * 64
        cases.append(("output digest", wrong_output))
        unguarded = self.result(request, mutates=True)
        unguarded["tool_receipts"][0]["write_authority_checked"] = False
        cases.append(("write-authority", unguarded))
        missing_tools = self.result(request)
        missing_tools["tool_receipts"] = []
        cases.append(("one or more tool_receipts", missing_tools))

        for label, payload in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(HostSkillBridgeError, label):
                    self.bridge.submit_result(
                        execution_id=request["execution_id"],
                        result=payload,
                    )

    def test_result_is_immutable_and_skill_drift_cannot_resume(self) -> None:
        pending = self.pending()
        request = self.request(pending)
        original = self.result(request)
        pointer = self.bridge.submit_result(
            execution_id=request["execution_id"], result=original
        )
        self.assertEqual(
            pointer,
            self.bridge.submit_result(execution_id=request["execution_id"], result=original),
        )
        conflicting = self.result(request)
        conflicting["payload"] = {"finding_count": 2}
        with self.assertRaisesRegex(HostSkillBridgeError, "conflicting"):
            self.bridge.submit_result(
                execution_id=request["execution_id"], result=conflicting
            )

        (self.root / self.skill_path).write_text("# Changed Skill\n", encoding="utf-8")
        with self.assertRaisesRegex(HostSkillBridgeError, "request identity drifted"):
            self.bridge.execute(
                skill_name="customer-agent-audit",
                request_class="AUDIT",
                step=self.step,
                state={**self.state, "host_execution_result": pointer},
            )

    def test_concurrent_conflicting_result_has_one_winner_and_cannot_overwrite(self) -> None:
        pending = self.pending()
        request = self.request(pending)
        first = self.result(request)
        second = self.result(request)
        second["payload"] = {"finding_count": 2}
        barrier = threading.Barrier(2)
        original_create = self.bridge._atomic_create

        def synchronized_create(path, payload):
            barrier.wait(timeout=5)
            return original_create(path, payload)

        def submit(payload):
            try:
                return self.bridge.submit_result(
                    execution_id=request["execution_id"], result=payload
                )
            except Exception as exc:  # captured for exact winner/loser assertions
                return exc

        with mock.patch.object(
            self.bridge, "_atomic_create", side_effect=synchronized_create
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(submit, (first, second)))

        successes = [row for row in outcomes if isinstance(row, dict)]
        failures = [row for row in outcomes if isinstance(row, Exception)]
        self.assertEqual(len(successes), 1, outcomes)
        self.assertEqual(len(failures), 1, outcomes)
        self.assertIsInstance(failures[0], HostSkillBridgeError)
        self.assertIn("conflicting", str(failures[0]))
        result_path = self.root / successes[0]["result_ref"].removeprefix("file:")
        persisted = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertIn(persisted["payload"]["finding_count"], {1, 2})


if __name__ == "__main__":
    unittest.main()
