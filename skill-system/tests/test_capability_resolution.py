from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
REGISTRY_DIR = Path(__file__).resolve().parents[1] / "registry"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from capability_registry import (  # type: ignore
    CapabilityRegistryError,
    load_capability_contracts,
    load_provider_registry,
    preflight_capabilities,
    resolve_capability,
)
from workflow_activation import activate_workflow  # type: ignore


class CapabilityResolutionTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="capability-registry-"))
        target = root / "skill-system/registry"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("capabilities.json", "executors.json", "integrations.json", "dev-workflows.json"):
            shutil.copy2(REGISTRY_DIR / name, target / name)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_registry_loads_provider_neutral_contracts_and_provider_catalog(self) -> None:
        workspace = self.workspace()
        contracts = load_capability_contracts(workspace)
        providers = load_provider_registry(workspace)
        self.assertEqual(contracts["test.run"].provider_type, "executor")
        self.assertTrue(contracts["ci.run.wait"].external_wait)
        self.assertIn("test.run", providers["local.process"].capabilities)
        self.assertIn("ci.run.wait", providers["github.actions"].capabilities)

    def test_provider_registration_does_not_make_provider_available(self) -> None:
        workspace = self.workspace()
        self.assertIsNone(
            resolve_capability(
                workspace,
                "ci.run.wait",
                available_provider_ids=[],
            )
        )

    def test_resolver_can_bind_same_capability_to_different_integrations(self) -> None:
        workspace = self.workspace()
        github = resolve_capability(
            workspace,
            "ci.run.wait",
            available_provider_ids=["github.actions"],
        )
        jenkins = resolve_capability(
            workspace,
            "ci.run.wait",
            available_provider_ids=["jenkins.ci"],
        )
        self.assertEqual(github.provider_id, "github.actions")
        self.assertEqual(jenkins.provider_id, "jenkins.ci")
        self.assertTrue(github.external_wait)
        self.assertTrue(jenkins.external_wait)

    def test_explicit_activation_preference_overrides_registry_priority(self) -> None:
        workspace = self.workspace()
        binding = resolve_capability(
            workspace,
            "ci.run.wait",
            available_provider_ids=["github.actions", "gitlab.ci"],
            provider_preferences={"ci.run.wait": "gitlab.ci"},
        )
        self.assertEqual(binding.provider_id, "gitlab.ci")

    def test_missing_required_capability_blocks_but_missing_optional_does_not(self) -> None:
        workspace = self.workspace()
        blocked = preflight_capabilities(
            workspace,
            required=["code_review.pull_request.create"],
            optional=["ci.run.wait"],
            available_provider_ids=["local.process"],
        )
        self.assertEqual(blocked.status, "BLOCKED_CONFIGURATION")
        self.assertEqual(blocked.missing_required, ("code_review.pull_request.create",))
        self.assertEqual(blocked.missing_optional, ("ci.run.wait",))

        ready = preflight_capabilities(
            workspace,
            required=["test.run"],
            optional=["ci.run.wait"],
            available_provider_ids=["local.process"],
        )
        self.assertEqual(ready.status, "PASS")
        self.assertEqual(ready.missing_required, ())
        self.assertEqual(ready.missing_optional, ("ci.run.wait",))

    def test_governed_repair_activation_uses_capabilities_not_github_identity(self) -> None:
        workspace = self.workspace()
        activation = activate_workflow(
            workspace,
            workflow_id="governed-repair",
            available_provider_ids=["local.workspace", "local.process", "local.git"],
        )
        payload = activation.as_dict()
        self.assertEqual(payload["status"], "PASS")
        bound = {
            row["capability_id"]: row["provider_id"]
            for row in payload["capability_preflight"]["required_bindings"]
        }
        self.assertEqual(bound["workspace.write"], "local.workspace")
        self.assertEqual(bound["test.run"], "local.process")
        self.assertEqual(bound["quality.evaluate"], "local.process")
        self.assertIn("code_review.pull_request.create", payload["capability_preflight"]["missing_optional"])
        self.assertFalse(payload["policy"]["taskrun_authority_changed"])
        self.assertFalse(payload["policy"]["quality_authority_changed"])
        self.assertFalse(payload["policy"]["completion_authority_changed"])
        self.assertFalse(payload["policy"]["write_authority_changed"])

    def test_unknown_capability_fails_closed(self) -> None:
        with self.assertRaisesRegex(CapabilityRegistryError, "unknown capability"):
            resolve_capability(
                self.workspace(),
                "github.magic",
                available_provider_ids=["github.actions"],
            )


if __name__ == "__main__":
    unittest.main()
