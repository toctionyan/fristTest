from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_SYSTEM = Path(__file__).resolve().parents[1]
CONTROLLER = SKILL_SYSTEM / "controller"
ROOT = SKILL_SYSTEM.parent
STARTER = SKILL_SYSTEM / "starters" / "customer-agent"
for search_path in (CONTROLLER, SKILL_SYSTEM):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

from starter_runtime import (  # type: ignore  # noqa: E402
    STARTER_HOST_CONFIRMATION_SCHEMA,
    STARTER_HOST_SELECTION_SCHEMA,
    StarterRuntimeError,
    build_starter_host_selection_request,
    load_starter_registration,
    register_starter_runtime,
    resolve_starter_host_selection,
)


class StarterHostSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="starter-host-selection-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.package = self.project / ".harness/customer-agent"
        self.package.parent.mkdir(parents=True)
        shutil.copytree(STARTER, self.package)
        self.registration = self.project / ".harness/runtime/customer-agent.registration.json"
        register_starter_runtime(
            project_workspace=self.project,
            starter_directory=self.package,
            output=self.registration,
            registry_workspace=ROOT,
        )
        self.loaded = load_starter_registration(
            project_workspace=self.project,
            registration=self.registration,
            registry_workspace=ROOT,
        )

    def request(self, text: str = "检查客服 Agent 总体还有哪些问题"):
        return build_starter_host_selection_request(
            self.loaded,
            registry_workspace=ROOT,
            host_id="chatgpt",
            user_request=text,
        )

    @staticmethod
    def selection(request, entrypoint):
        return {
            "schema": STARTER_HOST_SELECTION_SCHEMA,
            "host_id": request["host_id"],
            "request_fingerprint_sha256": request["request_fingerprint_sha256"],
            "selected_entrypoint": entrypoint,
            "authority_effect": False,
        }

    def test_host_language_request_exposes_only_verified_candidates_and_read_route_passes(self) -> None:
        request = self.request()
        self.assertEqual(
            {row["entrypoint"] for row in request["candidates"]},
            {
                "overall_audit", "module_audit", "architecture_review",
                "repair_and_prove", "repair_with_ci", "full_dev",
            },
        )
        self.assertFalse(request["policy"]["repository_keyword_router"])
        resolution = resolve_starter_host_selection(
            self.loaded,
            registry_workspace=ROOT,
            request=request,
            selection=self.selection(request, "overall_audit"),
        )
        self.assertEqual(resolution.record["status"], "PASS")
        self.assertIsNotNone(resolution.resolved)
        self.assertEqual(resolution.resolved.entrypoint, "overall_audit")
        self.assertFalse(resolution.record["confirmation_required"])
        self.assertFalse(resolution.record["policy"]["selection_grants_write_authority"])

    def test_mutating_language_selection_requires_exact_effect_preview_confirmation(self) -> None:
        request = self.request("修复 finding-17，测试后提交 GitHub CI")
        selection = self.selection(request, "repair_with_ci")
        preview = resolve_starter_host_selection(
            self.loaded,
            registry_workspace=ROOT,
            request=request,
            selection=selection,
        )
        self.assertEqual(preview.record["status"], "AWAITING_CONFIRMATION")
        self.assertIsNone(preview.resolved)
        self.assertIn("workspace.write", preview.record["effect_preview"]["mutating_capabilities"])
        self.assertFalse(preview.record["effect_preview"]["automatic_merge"])

        confirmation = {
            "schema": STARTER_HOST_CONFIRMATION_SCHEMA,
            "request_fingerprint_sha256": request["request_fingerprint_sha256"],
            "selected_entrypoint": "repair_with_ci",
            "effect_preview_sha256": preview.record["effect_preview_sha256"],
            "confirmed": True,
            "authority_effect": False,
        }
        confirmed = resolve_starter_host_selection(
            self.loaded,
            registry_workspace=ROOT,
            request=request,
            selection=selection,
            confirmation=confirmation,
        )
        self.assertEqual(confirmed.record["status"], "PASS")
        self.assertIsNotNone(confirmed.resolved)
        self.assertEqual(confirmed.resolved.entrypoint, "repair_with_ci")
        self.assertFalse(confirmed.record["policy"]["selection_grants_write_authority"])

    def test_unknown_candidate_stale_request_and_wrong_confirmation_fail_closed(self) -> None:
        request = self.request()
        with self.assertRaisesRegex(StarterRuntimeError, "outside the verified candidates"):
            resolve_starter_host_selection(
                self.loaded,
                registry_workspace=ROOT,
                request=request,
                selection=self.selection(request, "invented-workflow"),
            )

        stale = dict(request)
        stale["user_request"] = "changed after digest"
        with self.assertRaisesRegex(StarterRuntimeError, "stale or was modified"):
            resolve_starter_host_selection(
                self.loaded,
                registry_workspace=ROOT,
                request=stale,
                selection=self.selection(request, "overall_audit"),
            )

        mutation_request = self.request("修复 finding-17 并提交 CI")
        selection = self.selection(mutation_request, "repair_with_ci")
        with self.assertRaisesRegex(StarterRuntimeError, "exact preview"):
            resolve_starter_host_selection(
                self.loaded,
                registry_workspace=ROOT,
                request=mutation_request,
                selection=selection,
                confirmation={
                    "schema": STARTER_HOST_CONFIRMATION_SCHEMA,
                    "request_fingerprint_sha256": mutation_request["request_fingerprint_sha256"],
                    "selected_entrypoint": "repair_with_ci",
                    "effect_preview_sha256": "0" * 64,
                    "confirmed": True,
                    "authority_effect": False,
                },
            )


if __name__ == "__main__":
    unittest.main()
