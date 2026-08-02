#!/usr/bin/env python3
"""Keep the project-local Codex Skill small, valid and executable."""
from __future__ import annotations

import json
import os
import re
import runpy
from copy import deepcopy
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "SKILL.md",
    "manifest.json",
    "agents/openai.yaml",
    "scripts/verify_convergence.py",
    "scripts/verify_skill_package.py",
    "templates/new-abstraction-record.md",
    "templates/module-closure.md",
    "templates/quality-loop-target.md",
    "templates/targets/diagnosis.md",
    "templates/targets/design.md",
    "templates/targets/oracle-review.md",
    "templates/targets/repair.md",
    "templates/targets/migration.md",
    "templates/targets/revert.md",
    "templates/targets/certification.md",
    "references/quality-loop-contract.md",
    "references/conversation-regression-contract.md",
    "references/operational-closure-contract.md",
    "references/context-protocol-contract.md",
    "references/state-machine-testing-contract.md",
    "references/product-journey-contract.md",
    "references/mutation-replay-contract.md",
    "references/semantic-planning-boundary-contract.md",
    "references/architecture-baseline-migration-contract.md",
}
MARKERS = (
    "HARD_INVARIANT",
    "STRONG_DEFAULT",
    "REFERENCE_PATTERN",
    "合法偏离",
    "业务最终事实由 Business Service 裁决",
    "相似能力替代",
    "一个正式裁决链",
    "Shadow 路径",
    "通用控制平面",
    "单一开放语义 Owner",
    "Goal 不迁就能力",
    "项目基线",
    "内部表示非唯一性",
    "Oracle 不得绑定唯一内部表示",
)
TARGET_TEMPLATE_MARKERS = (
    "targets/diagnosis.md",
    "targets/design.md",
    "targets/oracle-review.md",
    "targets/repair.md",
    "targets/migration.md",
    "targets/revert.md",
    "targets/certification.md",
)



def _frontmatter(body: str) -> dict[str, str] | None:
    match = re.match(r"\A---\n(.*?)\n---\n", body, re.DOTALL)
    if not match:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def _guard_policy(**overrides: Any) -> dict[str, Any]:
    """Return the smallest complete policy needed to exercise guard invariants."""
    policy: dict[str, Any] = {
        "required_workspace_paths": [],
        "forbidden_paths": [],
        "core_root": "services/agent-service/src/agent_core",
        "allowed_core_dirs": [],
        "allowed_core_root_modules": [],
        "composition_dir": "composition",
        "source_roots": [],
        "single_graph_update_owner": "app/services/lifecycle_command_runner.py",
        "banned_universal_tools": [],
        "modules_root": "services/agent-service/src/agent_modules",
        "module_manifests": [],
        "line_limits": [],
        "runtime_roots": [],
        "convergence_matrix": "docs/architecture/CONVERGENCE_MATRIX.md",
    }
    policy.update(overrides)
    return policy


def _expect_guard_failure(
    verify: Any,
    workspace: Path,
    policy: dict[str, Any],
    expected_marker: str,
    *,
    name: str,
) -> str | None:
    result = verify(workspace, policy)
    errors = "\n".join(result.get("errors") or []) if isinstance(result, dict) else repr(result)
    if result.get("status") != "FAIL" or expected_marker not in errors:
        return f"{name}: expected guard failure containing {expected_marker!r}, got {result!r}"
    return None


def _guard_self_check_errors() -> list[str]:
    """Exercise the architecture guard on negative fixtures in an isolated temp tree.

    These checks intentionally live in the selected Skill package verifier
    rather than an uncollected pytest file.  They are pure reads of the real
    Skill and only create disposable files under ``TemporaryDirectory``.
    """
    try:
        namespace = runpy.run_path(str(ROOT / "scripts" / "verify_convergence.py"))
        verify = namespace.get("verify")
        if not callable(verify):
            return ["architecture guard does not export callable verify()"]
        errors: list[str] = []
        with tempfile.TemporaryDirectory(prefix="architecture-skill-self-check-") as raw:
            workspace = Path(raw)
            core = workspace / "services" / "agent-service" / "src" / "agent_core"

            (core / "lifecycle").mkdir(parents=True)
            (core / "loop").mkdir()
            error = _expect_guard_failure(
                verify,
                workspace,
                _guard_policy(
                    forbidden_paths=["services/agent-service/src/agent_core/loop"],
                    allowed_core_dirs=["lifecycle"],
                ),
                "并行目录",
                name="parallel_lifecycle_directory",
            )
            if error:
                errors.append(error)

        with tempfile.TemporaryDirectory(prefix="architecture-skill-self-check-") as raw:
            workspace = Path(raw)
            core = workspace / "services" / "agent-service" / "src" / "agent_core"
            core.mkdir(parents=True)
            (core / "runtime.py").write_text("from agent_modules.ecommerce import EcommerceModule\n", encoding="utf-8")
            error = _expect_guard_failure(
                verify,
                workspace,
                _guard_policy(allowed_core_root_modules=["runtime.py"]),
                "直接导入领域模块",
                name="core_domain_import",
            )
            if error:
                errors.append(error)

        with tempfile.TemporaryDirectory(prefix="architecture-skill-self-check-") as raw:
            workspace = Path(raw)
            error = _expect_guard_failure(
                verify,
                workspace,
                _guard_policy(
                    configuration={
                        "templates": [{
                            "path": "services/agent-service/.env.example",
                            "required_variables": ["OPENAI_API_KEY"],
                        }],
                        "source_paths": [],
                    }
                ),
                "配置模板",
                name="missing_configuration_template",
            )
            if error:
                errors.append(error)

        with tempfile.TemporaryDirectory(prefix="architecture-skill-self-check-") as raw:
            workspace = Path(raw)
            core = workspace / "services" / "agent-service" / "src" / "agent_core"
            core.mkdir(parents=True)
            (core / "config.py").write_text('import os\nos.getenv("NEW_RUNTIME_SETTING")\n', encoding="utf-8")
            template = workspace / "services" / "agent-service" / ".env.example"
            template.parent.mkdir(parents=True, exist_ok=True)
            template.write_text("OPENAI_API_KEY=\n", encoding="utf-8")
            error = _expect_guard_failure(
                verify,
                workspace,
                _guard_policy(
                    allowed_core_root_modules=["config.py"],
                    configuration={
                        "templates": [{
                            "path": "services/agent-service/.env.example",
                            "required_variables": ["OPENAI_API_KEY"],
                        }],
                        "source_paths": ["services/agent-service/src"],
                    },
                ),
                "变量未文档化",
                name="undocumented_runtime_environment_variable",
            )
            if error:
                errors.append(error)
        return errors
    except Exception as exc:
        return [f"architecture guard self-check raised {exc.__class__.__name__}: {exc}"]


def _cumulative_profile_errors(profiles: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    previous: set[str] = set()
    for name in ("project-quick", "project-integration", "project-product", "project-release"):
        values = profiles.get(name)
        if not isinstance(values, list) or not values:
            errors.append(f"missing cumulative profile: {name}")
            continue
        current = {str(value) for value in values}
        if previous and not previous < current:
            errors.append(f"profile is not strictly cumulative: {name}")
        previous = current
    return errors


def _operational_closure_errors() -> list[str]:
    """Make the Skill's operational-closure rules executable, not markers."""
    workspace = ROOT.parent
    errors: list[str] = []
    try:
        catalog = json.loads((workspace / "governance/requirements/project-quality-requirements.json").read_text(encoding="utf-8"))
        inventory = json.loads((workspace / "governance/product-capability-inventory.json").read_text(encoding="utf-8"))
        if catalog.get("schema_version") != 2:
            errors.append("project requirement catalog must use schema v2")
        inventory_ids = {str(row.get("id") or "") for row in inventory.get("capabilities") or [] if isinstance(row, dict)}
        mapped: set[str] = set()
        for row in catalog.get("requirements") or []:
            if not isinstance(row, dict):
                errors.append("invalid project requirement row")
                continue
            strategies = {str(value) for value in row.get("required_strategies") or []}
            if not str(row.get("invariant") or "") or not str(row.get("failure_class") or ""):
                errors.append(f"requirement lacks invariant/failure class: {row.get('id')}")
            if str(row.get("risk") or "") in {"P0", "P1"} and not {"counterexample", "mutation"} <= strategies:
                errors.append(f"high-risk requirement lacks counterexample/mutation: {row.get('id')}")
            mapped.update(str(value) for value in row.get("inventory_ids") or [])
        if mapped != inventory_ids:
            errors.append("project requirement catalog does not exactly cover product inventory")
        errors.extend(_cumulative_profile_errors(catalog.get("profiles") or {}))

        # Negative self-check: the cumulative guard itself must reject a
        # release profile with one inherited requirement removed.
        mutated = deepcopy(catalog.get("profiles") or {})
        quick = list(mutated.get("project-quick") or [])
        if quick and isinstance(mutated.get("project-integration"), list):
            mutated["project-integration"] = [value for value in mutated["project-integration"] if value != quick[0]]
            if not _cumulative_profile_errors(mutated):
                errors.append("cumulative profile guard failed its negative self-check")

        policy = json.loads((workspace / "governance/quality-loop-policy.json").read_text(encoding="utf-8"))
        gate_ids = {str(row.get("id") or "") for row in policy.get("steps") or [] if isinstance(row, dict)}
        if "systemic-operational-counterexamples" not in gate_ids:
            errors.append("quality policy lacks systemic operational counterexample Gate")
        mutations = json.loads((workspace / "governance/mutations/systemic-mutations.json").read_text(encoding="utf-8"))
        mutation_ids = {str(row.get("id") or "") for row in mutations.get("mutations") or [] if isinstance(row, dict)}
        required_mutations = {
            "raw-message-tail-slice", "orphan-tool-result", "disable-historical-evidence",
            "collapse-thread-topology", "wildcard-private-import", "reset-order-selection",
            "non-cumulative-release-profile", "unredacted-failure-replay",
            "permit-free-current-turn-result-ref", "contradictory-target-mode-fields",
            "shared-model-budget-starvation",
        }
        if not required_mutations <= mutation_ids:
            errors.append("systemic mutation catalog is incomplete")
        shared = workspace / "services/agent-service/src/agent_modules/ecommerce/shared"
        wildcard_files = [path.name for path in shared.glob("*.py") if "import *" in path.read_text(encoding="utf-8")]
        if wildcard_files:
            errors.append("module slices still use wildcard imports: " + ",".join(sorted(wildcard_files)))
        dialogue = (workspace / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
        bundle = (workspace / "services/agent-service/src/agent_core/context/context_bundle.py").read_text(encoding="utf-8")
        if "compile_provider_context" not in dialogue or "compile_provider_context" not in bundle:
            errors.append("model payload and ContextBundle do not share the conversation protocol compiler")
    except Exception as exc:
        errors.append(f"operational closure verification raised {exc.__class__.__name__}: {exc}")
    return errors



def _semantic_authority_errors() -> list[str]:
    """Prevent semantic Oracles from becoming a second programmatic planner."""
    workspace = ROOT.parent
    errors: list[str] = []
    try:
        smoke = (workspace / "services/agent-service/scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        planning = (workspace / "services/agent-service/src/agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
        protocol = (workspace / "services/agent-service/src/agent_core/lifecycle/protocol.py").read_text(encoding="utf-8")
        regression = (workspace / "services/agent-service/tests/runtime/test_goal_dependency_declaration_semantics.py").read_text(encoding="utf-8")
        for marker in (
            "goal count mismatch",
            "goal dependency mismatch",
            "model emitted undeclared extra goals",
            "actual_dependencies != expected_dependencies",
        ):
            if marker in smoke:
                errors.append(f"protected semantic smoke binds unique internal representation: {marker}")
        required = {
            "smoke": (smoke, "_assert_effect_evidence_coverage"),
            "planning": (planning, "decomposition shape alone is not a failure"),
            "protocol": (protocol, "程序只验证引用存在"),
            "regression-equivalent": (regression, "accepts_equivalent_dependency_representations"),
            "regression-missing": (regression, "rejects_missing_requested_effect"),
        }
        for name, (body, marker) in required.items():
            if marker not in body:
                errors.append(f"semantic representation-independence guard missing {name}: {marker}")
    except Exception as exc:
        errors.append(f"semantic authority verification raised {exc.__class__.__name__}: {exc}")
    return errors

def _control_plane_errors() -> list[str]:
    workspace = ROOT.parent
    errors: list[str] = []
    required = [
        "AGENTS.md",
        "CLAUDE.md",
        "skill-system/core/constitution.md",
        "skill-system/core/rule-model.md",
        "skill-system/schemas/change-contract.schema.json",
        "skill-system/schemas/project-architecture-baseline.schema.json",
        "skill-system/schemas/architecture-migration-delta.schema.json",
        "skill-system/controller/architecture_policy.py",
        "skill-system/templates/architecture-migration-delta.json",
        "skill-system/registry/active-rules.json",
        ".codex/config.toml",
        ".claude/settings.json",
    ]
    for rel in required:
        if not (workspace / rel).is_file():
            errors.append(f"missing control-plane file: {rel}")
    try:
        baseline = json.loads((workspace / "governance/architecture-policy.json").read_text(encoding="utf-8"))
        if baseline.get("policy_kind") != "project-architecture-baseline" or baseline.get("schema_version") != 2:
            errors.append("architecture-policy.json is not a versioned project baseline")
        if not baseline.get("policy_id") or not baseline.get("baseline_semantics"):
            errors.append("project architecture baseline identity is incomplete")
    except Exception as exc:
        errors.append(f"project architecture baseline invalid: {exc}")
    for host_root in (".agents/skills", ".claude/skills"):
        for name in (
            "change-scope", "architecture-options", "red-baseline-repair",
            "oracle-review", "adversarial-review", "release-certification",
            "product-code-governance", "customer-agent-architecture",
        ):
            path = workspace / host_root / name / "SKILL.md"
            if not path.is_file():
                errors.append(f"missing host Skill: {path.relative_to(workspace)}")
            elif f"skill-system/skills/{name}/SKILL.md" not in path.read_text(encoding="utf-8"):
                errors.append(f"host Skill is not a thin adapter: {path.relative_to(workspace)}")
    try:
        rules = json.loads((workspace / "skill-system/registry/active-rules.json").read_text(encoding="utf-8"))
        for rule in rules.get("rules") or []:
            if rule.get("level") == "HARD_INVARIANT" and rule.get("variance_allowed") is not False:
                errors.append(f"hard invariant allows variance: {rule.get('id')}")
    except Exception as exc:
        errors.append(f"active rule registry invalid: {exc}")
    return errors


def main() -> int:
    artifact_profile = os.getenv("ARTIFACT_VALIDATION_PROFILE", "development-workspace").strip().lower()
    clean_release = artifact_profile == "clean-release"
    files = {
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    missing = sorted(EXPECTED - files)
    body = (ROOT / "SKILL.md").read_text(encoding="utf-8") if (ROOT / "SKILL.md").is_file() else ""
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8")) if (ROOT / "manifest.json").is_file() else {}
    frontmatter = _frontmatter(body)
    errors: list[str] = []
    if frontmatter is None or not {"name", "description", "version"} <= set(frontmatter):
        errors.append("SKILL.md frontmatter 必须包含 name、description、version")
    elif frontmatter.get("name") != manifest.get("name") or not frontmatter.get("description"):
        errors.append("SKILL.md name/description 与 manifest 不一致或为空")
    version_match = re.search(r"(?m)^Skill 版本：\s*([^\s]+)\s*$", body)
    if (
        not version_match
        or manifest.get("version") != version_match.group(1)
        or (frontmatter or {}).get("version") != version_match.group(1)
    ):
        errors.append("Skill frontmatter、正文版本与 manifest.version 不一致")
    for marker in MARKERS:
        if marker not in body:
            errors.append(f"缺少 Skill 标记：{marker}")
    target_template = (ROOT / "templates" / "quality-loop-target.md").read_text(encoding="utf-8") if (ROOT / "templates" / "quality-loop-target.md").is_file() else ""
    for marker in TARGET_TEMPLATE_MARKERS:
        if marker not in target_template:
            errors.append(f"质量 target 选择器缺少：{marker}")
    errors.extend(_guard_self_check_errors())
    errors.extend(_operational_closure_errors())
    errors.extend(_semantic_authority_errors())
    errors.extend(_control_plane_errors())
    if len(body.encode("utf-8")) > 12000:
        errors.append("领域 SKILL.md 超过 12KB；通用治理必须留在 skill-system/")
    openai_yaml = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8") if (ROOT / "agents" / "openai.yaml").is_file() else ""
    if "display_name:" not in openai_yaml or "short_description:" not in openai_yaml or "$customer-agent-architecture" not in openai_yaml:
        errors.append("agents/openai.yaml 缺少领域 Skill 界面元数据")
    caches = [str(path.relative_to(ROOT)) for path in ROOT.rglob("__pycache__") if path.is_dir()]
    result = {
        "status": "PASS" if not (missing or errors or (clean_release and caches)) else "FAIL",
        "missing": missing,
        "errors": errors,
        "cache_dirs": caches,
        "artifact_profile": artifact_profile,
        "cache_policy": "blocking" if clean_release else "reported_not_blocking",
        "skill_bytes": len(body.encode("utf-8")),
        "control_plane": f"skill-system@{manifest.get('control_plane_version')}",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
