from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from github_agent_fixer import ModelConfig  # noqa: E402
from github_repair_authority import validate_rca  # noqa: E402
from github_repair_rca import RCAError, run_read_only_rca  # noqa: E402


class GovernedRepairRCATests(unittest.TestCase):
    def _workspace(self) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        path = "services/agent-service/src/agent_core/example.py"
        destination = root / path
        destination.parent.mkdir(parents=True)
        destination.write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
        return temp, root, path

    @staticmethod
    def _failure(path: str) -> dict[str, object]:
        return {
            "schema": "github-failure-ingest@1",
            "repository": "toctionyan/fristTest",
            "workflow_name": "quality",
            "workflow_run_id": "123",
            "workflow_run_attempt": "1",
            "head_sha": "a" * 40,
            "classification": "code_or_contract",
            "failure_signature": "b" * 64,
            "failed_gates": [{"gate_id": "gate-a", "status": "FAIL"}],
            "failure_summary": "semantic contract mismatch",
            "candidate_paths": [path],
        }

    @staticmethod
    def _config() -> ModelConfig:
        return ModelConfig(
            provider="openai",
            model="test-model",
            api_base="https://api.openai.com/v1",
            api_key="test",
        )

    @staticmethod
    def _response(path: str) -> bytes:
        payload = {
            "failure_class": "semantic_contract_drift",
            "violated_invariant": "INV-DEPENDENCY-001",
            "authority_owner": "deterministic_dependency_reducer",
            "drifted_projection": "candidate_blind_prompt",
            "root_cause": "semantic copy diverged from the structural authority",
            "existing_gate_gap": "no canonical provenance check",
            "required_permanent_guard": "canonical contract plus mutation proof",
            "repair_plan": [
                "replace the drifted projection with a canonical projection",
                "retain deterministic final-edge authority",
            ],
            "write_scope_recommendation": {
                "decision": "GRANT",
                "paths": [path],
            },
        }
        envelope = {"choices": [{"message": {"content": json.dumps(payload)}}]}
        return json.dumps(envelope).encode("utf-8")

    def test_rca_is_read_only_and_bound(self) -> None:
        temp, root, path = self._workspace()
        self.addCleanup(temp.cleanup)
        before = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout

        result = run_read_only_rca(
            workspace=root,
            failure_case=self._failure(path),
            candidate_paths=(path,),
            repair_round=1,
            config=self._config(),
            request_fn=lambda _config, _messages: self._response(path),
        )

        after = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(before, after)
        self.assertTrue(result["read_only"])
        self.assertFalse(result["workspace_mutated"])
        self.assertEqual(
            validate_rca(
                result,
                failure_case=self._failure(path),
                candidate_paths=(path,),
            ),
            (path,),
        )

    def test_rca_cannot_expand_write_scope(self) -> None:
        temp, root, path = self._workspace()
        self.addCleanup(temp.cleanup)
        other = "services/agent-service/src/agent_core/other.py"
        payload = {
            "failure_class": "semantic_contract_drift",
            "violated_invariant": "INV-DEPENDENCY-001",
            "authority_owner": "deterministic_dependency_reducer",
            "drifted_projection": "candidate_blind_prompt",
            "root_cause": "semantic copy diverged",
            "existing_gate_gap": "missing gate",
            "required_permanent_guard": "new guard",
            "repair_plan": ["repair product source"],
            "write_scope_recommendation": {
                "decision": "GRANT",
                "paths": [other],
            },
        }
        envelope = {"choices": [{"message": {"content": json.dumps(payload)}}]}
        with self.assertRaises(RCAError):
            run_read_only_rca(
                workspace=root,
                failure_case=self._failure(path),
                candidate_paths=(path,),
                repair_round=1,
                config=self._config(),
                request_fn=lambda _config, _messages: json.dumps(envelope).encode("utf-8"),
            )

    def test_rca_detects_workspace_mutation(self) -> None:
        temp, root, path = self._workspace()
        self.addCleanup(temp.cleanup)

        def mutate_then_respond(_config: ModelConfig, _messages: list[dict[str, str]]) -> bytes:
            (root / path).write_text("VALUE = 2\n", encoding="utf-8")
            return self._response(path)

        with self.assertRaises(RCAError):
            run_read_only_rca(
                workspace=root,
                failure_case=self._failure(path),
                candidate_paths=(path,),
                repair_round=1,
                config=self._config(),
                request_fn=mutate_then_respond,
            )


if __name__ == "__main__":
    unittest.main()
