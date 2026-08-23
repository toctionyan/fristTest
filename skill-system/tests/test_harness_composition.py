from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from harness_authoring import HarnessAuthoringError, validate_declaration  # type: ignore  # noqa: E402
from harness_composition import (  # type: ignore  # noqa: E402
    compose_workflow,
    parse_composition_declaration,
)


def base_workflow(*, mode: str = "READ_ONLY") -> dict[str, object]:
    return {
        "schema": "harness-workflow@1",
        "id": "audit-customer-agent",
        "version": "1.0.0",
        "request_class": "DIAGNOSIS",
        "skills": ["customer-agent-audit"],
        "mode": mode,
        "status_first": False,
        "deterministic_response": False,
        "write_governed": mode == "WRITE",
        "requirements": {
            "capabilities": {"required": ["quality.evaluate"], "optional": ["vcs.diff.read"]}
        },
        "graph": {
            "start": "inspect",
            "steps": {
                "inspect": {
                    "type": "skill",
                    "use": "customer-agent-audit",
                    "routes": {"issues": "quality", "clean": "END"},
                },
                "quality": {
                    "type": "gate",
                    "use": "quality.evaluate",
                    "routes": {"pass": "END", "fail": "BLOCKED_UNRECOVERABLE"},
                },
            },
        },
        "completion": {
            "transition_to": "VALIDATING",
            "policy": "audit-report-produced@1",
            "authority": "TaskRun",
        },
    }


def host_contract() -> dict[str, object]:
    return {
        "schema": "harness-skill-contract@1",
        "skill": "customer-agent-audit",
        "version": "1.0.0",
        "mode": "read_only",
        "inputs": ["request-context@1"],
        "capabilities": [],
        "outputs": ["finding-set@1"],
        "extension_type": "procedure",
        "extension_points": {
            "before-analysis": ["context-provider", "audit-lens"],
            "finding-enrichment": ["finding-enricher"],
            "before-validation": ["gate", "audit-lens", "procedure"],
        },
    }


def extension_contract(
    skill: str,
    extension_type: str,
    *,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    capabilities: list[str] | None = None,
    mode: str = "read_only",
) -> dict[str, object]:
    return {
        "schema": "harness-skill-contract@1",
        "skill": skill,
        "version": "1.0.0",
        "mode": mode,
        "inputs": inputs or [],
        "capabilities": capabilities or [],
        "outputs": outputs or [f"{skill}-result@1"],
        "extension_type": extension_type,
        "extension_points": {},
    }


def binding(
    binding_id: str,
    extension_skill: str,
    at: str,
    *,
    kind: str,
    step: str,
    outcome: str | None = None,
    order: int = 100,
) -> dict[str, object]:
    anchor: dict[str, object] = {"kind": kind, "step": step}
    if outcome is not None:
        anchor["outcome"] = outcome
    return {
        "id": binding_id,
        "host_skill": "customer-agent-audit",
        "extension_skill": extension_skill,
        "at": at,
        "anchor": anchor,
        "order": order,
        "routes": {"continue": "$CONTINUE", "blocked": "BLOCKED_UNRECOVERABLE"},
    }


def composition(bindings: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "harness-composition@1",
        "id": "audit-customer-agent-with-policy",
        "version": "1.1.0",
        "base_workflow": "audit-customer-agent",
        "bindings": bindings,
    }


class HarnessCompositionTest(unittest.TestCase):
    def test_before_step_and_after_route_compose_without_mutating_base(self) -> None:
        base = base_workflow()
        original = copy.deepcopy(base)
        raw_composition = composition(
            [
                binding(
                    "policy-gate", "customer-policy-gate", "before-validation",
                    kind="before_step", step="quality", order=200,
                ),
                binding(
                    "security-gate", "customer-security-gate", "before-validation",
                    kind="before_step", step="quality", order=100,
                ),
                binding(
                    "finding-enricher", "customer-finding-enricher", "finding-enrichment",
                    kind="after_route", step="inspect", outcome="issues",
                ),
            ]
        )
        contracts = [
            host_contract(),
            extension_contract(
                "customer-policy-gate", "gate", inputs=["finding-set@1"],
                capabilities=["policy.evaluate"],
            ),
            extension_contract(
                "customer-security-gate", "gate", inputs=["finding-set@1"]
            ),
            extension_contract(
                "customer-finding-enricher", "finding-enricher",
                inputs=["finding-set@1"], outputs=["enriched-finding-set@1"],
            ),
        ]

        plan = compose_workflow(base, raw_composition, contracts)
        derived = plan.derived_workflow
        steps = derived["graph"]["steps"]

        self.assertEqual(base, original)
        self.assertEqual(steps["inspect"]["routes"]["issues"], "finding-enricher")
        self.assertEqual(steps["finding-enricher"]["routes"]["continue"], "security-gate")
        self.assertEqual(steps["security-gate"]["routes"]["continue"], "policy-gate")
        self.assertEqual(steps["policy-gate"]["routes"]["continue"], "quality")
        self.assertIn("policy.evaluate", derived["requirements"]["capabilities"]["required"])
        self.assertEqual(plan.compiled_plan["completion"]["authority"], "TaskRun")
        self.assertEqual(plan.compiled_plan["completion"]["transition_to"], "VALIDATING")
        self.assertEqual(len(plan.as_dict()["provenance_sha256"]), 64)

    def test_before_start_changes_only_derived_start(self) -> None:
        raw_composition = composition(
            [
                binding(
                    "context-provider", "customer-context-provider", "before-analysis",
                    kind="before_step", step="inspect",
                )
            ]
        )
        provider = extension_contract(
            "customer-context-provider",
            "context-provider",
            outputs=["request-context@1"],
        )
        plan = compose_workflow(base_workflow(), raw_composition, [host_contract(), provider])
        self.assertEqual(plan.derived_workflow["graph"]["start"], "context-provider")
        self.assertEqual(
            plan.derived_workflow["graph"]["steps"]["context-provider"]["routes"]["continue"],
            "inspect",
        )

    def test_binding_order_is_deterministic_independent_of_declaration_order(self) -> None:
        alpha = binding(
            "alpha", "alpha-gate", "before-validation",
            kind="before_step", step="quality", order=100,
        )
        beta = binding(
            "beta", "beta-gate", "before-validation",
            kind="before_step", step="quality", order=100,
        )
        contracts = [
            host_contract(),
            extension_contract("alpha-gate", "gate", inputs=["finding-set@1"]),
            extension_contract("beta-gate", "gate", inputs=["finding-set@1"]),
        ]
        forward = compose_workflow(base_workflow(), composition([alpha, beta]), contracts)
        reversed_plan = compose_workflow(base_workflow(), composition([beta, alpha]), contracts)
        self.assertEqual(forward.derived_workflow, reversed_plan.derived_workflow)
        self.assertEqual(
            [row["id"] for row in forward.resolved_bindings],
            ["alpha", "beta"],
        )
        self.assertEqual(
            forward.derived_workflow["graph"]["steps"]["alpha"]["routes"]["continue"],
            "beta",
        )

    def test_structural_validation_accepts_composition_schema(self) -> None:
        raw = composition(
            [
                binding(
                    "policy-gate", "customer-policy-gate", "before-validation",
                    kind="before_step", step="quality",
                )
            ]
        )
        self.assertEqual(validate_declaration(raw)["schema"], "harness-composition@1")

    def test_undeclared_hook_and_incompatible_type_fail_closed(self) -> None:
        contracts = [
            host_contract(),
            extension_contract("policy-gate", "gate", inputs=["finding-set@1"]),
        ]
        unknown = composition(
            [binding("policy", "policy-gate", "unknown-hook", kind="before_step", step="quality")]
        )
        with self.assertRaisesRegex(HarnessAuthoringError, "does not declare extension point"):
            compose_workflow(base_workflow(), unknown, contracts)

        incompatible = composition(
            [binding("policy", "policy-gate", "finding-enrichment", kind="before_step", step="quality")]
        )
        with self.assertRaisesRegex(HarnessAuthoringError, "is not accepted"):
            compose_workflow(base_workflow(), incompatible, contracts)

    def test_unknown_anchor_collision_and_invalid_continuation_fail_closed(self) -> None:
        contract = extension_contract("policy-gate", "gate", inputs=["finding-set@1"])
        unknown = composition(
            [binding("policy", "policy-gate", "before-validation", kind="before_step", step="missing")]
        )
        with self.assertRaisesRegex(HarnessAuthoringError, "unknown base step"):
            compose_workflow(base_workflow(), unknown, [host_contract(), contract])

        collision = composition(
            [binding("quality", "policy-gate", "before-validation", kind="before_step", step="quality")]
        )
        with self.assertRaisesRegex(HarnessAuthoringError, "collides"):
            compose_workflow(base_workflow(), collision, [host_contract(), contract])

        invalid = composition(
            [binding("policy", "policy-gate", "before-validation", kind="before_step", step="quality")]
        )
        invalid["bindings"][0]["routes"] = {"continue": "some-other-step"}
        with self.assertRaisesRegex(HarnessAuthoringError, "must be.*CONTINUE"):
            parse_composition_declaration(invalid)

    def test_duplicate_bindings_and_contracts_fail_closed(self) -> None:
        duplicate = composition(
            [
                binding("policy", "policy-gate", "before-validation", kind="before_step", step="quality"),
                binding("policy", "other-gate", "before-validation", kind="before_step", step="quality"),
            ]
        )
        with self.assertRaisesRegex(HarnessAuthoringError, "duplicate binding id"):
            parse_composition_declaration(duplicate)

        one = composition(
            [binding("policy", "policy-gate", "before-validation", kind="before_step", step="quality")]
        )
        contract = extension_contract("policy-gate", "gate", inputs=["finding-set@1"])
        with self.assertRaisesRegex(HarnessAuthoringError, "duplicate Skill contract"):
            compose_workflow(base_workflow(), one, [host_contract(), contract, contract])

        invalid_id = composition(
            [binding("invalid/id", "policy-gate", "before-validation", kind="before_step", step="quality")]
        )
        with self.assertRaisesRegex(HarnessAuthoringError, "stable identifier"):
            parse_composition_declaration(invalid_id)

        self_extension = composition(
            [
                binding(
                    "self-extension", "customer-agent-audit", "before-validation",
                    kind="before_step", step="quality",
                )
            ]
        )
        with self.assertRaisesRegex(HarnessAuthoringError, "host Skill as the extension"):
            compose_workflow(base_workflow(), self_extension, [host_contract()])

    def test_artifact_incompatibility_and_read_only_mutation_fail_closed(self) -> None:
        raw = composition(
            [binding("policy", "policy-gate", "before-validation", kind="before_step", step="quality")]
        )
        incompatible = extension_contract("policy-gate", "gate", inputs=["unknown-artifact@1"])
        with self.assertRaisesRegex(HarnessAuthoringError, "requires artifacts not exposed"):
            compose_workflow(base_workflow(), raw, [host_contract(), incompatible])

        mutating = extension_contract(
            "policy-gate", "procedure", inputs=["finding-set@1"],
            outputs=["patch-set@1"], capabilities=["workspace.write"], mode="mutating",
        )
        with self.assertRaisesRegex(HarnessAuthoringError, "already WRITE"):
            compose_workflow(base_workflow(), raw, [host_contract(), mutating])

    def test_provider_specific_capability_is_rejected_by_canonical_compiler(self) -> None:
        raw = composition(
            [binding("policy", "policy-gate", "before-validation", kind="before_step", step="quality")]
        )
        provider_bound = extension_contract(
            "policy-gate", "gate", inputs=["finding-set@1"],
            capabilities=["github.actions.run"],
        )
        with self.assertRaisesRegex(HarnessAuthoringError, "provider-neutral"):
            compose_workflow(base_workflow(), raw, [host_contract(), provider_bound])

    def test_root_cli_emits_plan_and_standalone_derived_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            paths = {
                "workflow": directory / "workflow.json",
                "composition": directory / "composition.json",
                "host": directory / "host.json",
                "extension": directory / "extension.json",
            }
            raw = composition(
                [binding("policy", "policy-gate", "before-validation", kind="before_step", step="quality")]
            )
            payloads = {
                "workflow": base_workflow(),
                "composition": raw,
                "host": host_contract(),
                "extension": extension_contract("policy-gate", "gate", inputs=["finding-set@1"]),
            }
            for name, path in paths.items():
                path.write_text(json.dumps(payloads[name]), encoding="utf-8")
            derived = directory / "derived.json"
            result = subprocess.run(
                [
                    sys.executable, "-B", "skillctl.py", "authoring", "compose",
                    "--workflow", str(paths["workflow"]),
                    "--composition", str(paths["composition"]),
                    "--skill-contract", str(paths["host"]),
                    "--skill-contract", str(paths["extension"]),
                    "--derived-workflow-output", str(derived),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["schema"], "composed-workflow-plan@1")
            derived_payload = json.loads(derived.read_text(encoding="utf-8"))
            self.assertEqual(derived_payload["schema"], "harness-workflow@1")
            self.assertEqual(derived_payload["id"], "audit-customer-agent-with-policy")


if __name__ == "__main__":
    unittest.main()
