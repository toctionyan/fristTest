from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


def test_module_slices_have_no_wildcard_imports_and_runtime_helper_executes() -> None:
    shared = ROOT / "services/agent-service/src/agent_modules/ecommerce/shared"
    offenders = [path.name for path in shared.glob("*.py") if "import *" in path.read_text(encoding="utf-8")]
    assert offenders == []

    from agent_modules.ecommerce.shared.runtime_tools import _query_transaction_lifecycle

    result = _query_transaction_lifecycle(
        {
            "current_user_input": "查一下刚才办理到哪了",
            "current_user_id": "u001",
            "current_tenant_id": "tenant-a",
            "current_thread_id": "thread-a",
        },
        {"query_span": "查一下刚才办理到哪了"},
        transactions=None,
    )
    assert result["ok"] is False
    assert result["code"] == "TRANSACTION_REPOSITORY_UNAVAILABLE"


def test_project_requirement_catalog_is_inventory_backed_and_cumulative() -> None:
    catalog = json.loads((ROOT / "governance/requirements/project-quality-requirements.json").read_text(encoding="utf-8"))
    inventory = json.loads((ROOT / "governance/product-capability-inventory.json").read_text(encoding="utf-8"))
    assert catalog["schema_version"] == 2
    inventory_ids = {row["id"] for row in inventory["capabilities"]}
    mapped = set()
    for requirement in catalog["requirements"]:
        assert requirement["invariant"]
        assert requirement["failure_class"]
        assert {"counterexample", "mutation"}.issubset(requirement["required_strategies"])
        mapped.update(requirement["inventory_ids"])
    assert mapped == inventory_ids
    profiles = catalog["profiles"]
    assert set(profiles["project-quick"]) < set(profiles["project-integration"])
    assert set(profiles["project-integration"]) < set(profiles["project-product"])
    assert set(profiles["project-product"]) < set(profiles["project-release"])


def test_systemic_mutation_manifest_has_executable_kill_proofs() -> None:
    payload = json.loads((ROOT / "governance/mutations/systemic-mutations.json").read_text(encoding="utf-8"))
    required = {
        "raw-message-tail-slice",
        "orphan-tool-result",
        "disable-historical-evidence",
        "collapse-thread-topology",
        "wildcard-private-import",
        "reset-order-selection",
        "non-cumulative-release-profile",
        "unredacted-failure-replay",
        "permit-free-current-turn-result-ref",
        "contradictory-target-mode-fields",
        "shared-model-budget-starvation",
        "drop-live-notice-block-rendering",
        "weaken-strong-context-browser-oracle",
        "omit-configured-model-browser-gate",
    }
    rows = {row["id"]: row for row in payload["mutations"]}
    assert required <= set(rows)
    for mutation_id in required:
        row = rows[mutation_id]
        assert row["invariant_id"]
        assert row["kill_test"]
        test_path, selector = row["kill_test"].split("::", 1)
        source = (ROOT / test_path).read_text(encoding="utf-8")
        if test_path.endswith(".py"):
            assert f"def {selector}(" in source
        else:
            assert selector in source


def test_failure_replay_is_redacted_stable_and_runtime_emitted() -> None:
    from langchain_core.messages import HumanMessage

    from agent_core.lifecycle.dialogue_runtime import agent_loop_node
    from agent_core.observability.failure_replay import build_failure_replay

    state = {
        "current_tenant_id": "tenant-secret",
        "current_user_id": "alice@example.com",
        "current_thread_id": "thread-secret",
        "current_user_input": "我的 token=console.secret API_KEY=sk-secret",
        "turn_index": 7,
        "tool_trace": [{"name": "lookup", "result": {"ok": False, "password": "123456"}}],
    }
    first = build_failure_replay(state=state, stage="agent_loop", error_type="ProviderError", error_message="Bearer console.secret")
    second = build_failure_replay(state=state, stage="agent_loop", error_type="ProviderError", error_message="Bearer console.secret")
    encoded = json.dumps(first, ensure_ascii=False)
    assert first["fingerprint"] == second["fingerprint"]
    for secret in ("tenant-secret", "alice@example.com", "thread-secret", "console.secret", "sk-secret", "123456"):
        assert secret not in encoded
    assert first["schema_version"] == 1

    class ProviderFailure(RuntimeError):
        pass

    runtime_update = agent_loop_node(
        {
            **state,
            "messages": [HumanMessage(content=state["current_user_input"])],
            "agent_loop_step": 0,
            "agent_loop_max_steps": 4,
        },
        context_bundle_builder=object(),  # resolver fails before context compilation
        capability_registry=object(),
        model_resolver=lambda: (_ for _ in ()).throw(
            ProviderFailure("Bearer console.secret")
        ),
    )
    runtime_replay = runtime_update["tool_error"]["replay"]
    assert runtime_update["status"] == "LLMUnavailable"
    assert runtime_replay["error"]["type"] == "ProviderFailure"
    assert runtime_replay["stage"] == "agent_loop"
    runtime_encoded = json.dumps(runtime_replay, ensure_ascii=False)
    for secret in ("tenant-secret", "alice@example.com", "thread-secret", "console.secret", "sk-secret", "123456"):
        assert secret not in runtime_encoded


def test_full_lifecycle_gate_crosses_public_boundary_contract() -> None:
    script = ROOT / "scripts/verify_full_lifecycle_canary.py"
    assert script.is_file(), "full lifecycle canary runner is missing"
    source = script.read_text(encoding="utf-8")
    for marker in (
        "tests.integration.model_stub:app",
        "run_business_api.py",
        "run_api.py",
        "verify_product_http_smoke.py",
        "PRODUCT_HTTP_SMOKE_EPHEMERAL_DATA",
    ):
        assert marker in source


def test_documented_local_api_entrypoint_seeds_builtin_knowledge_only_for_local_profile() -> None:
    source = (ROOT / "services/agent-service/scripts/run_api.py").read_text(encoding="utf-8")

    assert 'if profile.value == "local"' in source
    assert "get_module_registry()" in source
    assert "RagBootstrapService().seed_builtin_knowledge()" in source
    assert "local RAG bootstrap failed" in source
    assert source.index('if profile.value == "local"') < source.index("get_module_registry()") < source.index("seed_builtin_knowledge()")


def test_managed_integration_owns_environment_without_faking_real_model_gate() -> None:
    runner = ROOT / "scripts/run_managed_quality_integration.py"
    build_runner = ROOT / "services/agent-service/frontend/scripts/build.mjs"
    package = json.loads(
        (ROOT / "services/agent-service/frontend/package.json").read_text(encoding="utf-8")
    )
    source = runner.read_text(encoding="utf-8")
    build_source = build_runner.read_text(encoding="utf-8")

    for marker in (
        "ManagedPostgres",
        "ProductRuntimeHarness",
        "AGENT_TEST_POSTGRES_URL",
        "BUSINESS_SERVICE_BASE_URL",
        'for key in ("OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_MODEL")',
        "quality_loop.py",
    ):
        assert marker in source
    assert package["scripts"]["build"] == "node scripts/build.mjs"
    assert "QUALITY_EVIDENCE_DIR" in build_source
    assert '"artifacts", "frontend-dist"' in build_source


def test_browser_gate_uses_real_playwright_desktop_and_mobile() -> None:
    runner = ROOT / "scripts/verify_product_browser_journey.py"
    journey = ROOT / "services/agent-service/frontend/e2e/product_journey.mjs"
    package = ROOT / "services/agent-service/frontend/package.json"
    assert runner.is_file(), "real-browser gate runner is missing"
    assert journey.is_file(), "Playwright product journey is missing"
    runner_source = runner.read_text(encoding="utf-8")
    journey_source = journey.read_text(encoding="utf-8")
    package_source = package.read_text(encoding="utf-8")
    assert "product_journey.mjs" in runner_source
    assert "playwright" in package_source
    for marker in ("chromium", "1440", "900", "390", "844", "getByRole"):
        assert marker in journey_source
    for marker in ("/api/transactions/input", "/api/transactions/authority", "确认提交", "lifecycleCommitted", "receiptVisible", "terminalTitleAccurate"):
        assert marker in journey_source


def test_strong_context_browser_gate_is_semantic_and_mutation_guarded() -> None:
    policy = json.loads((ROOT / "governance/quality-loop-policy.json").read_text(encoding="utf-8"))
    steps = {step["id"]: step for step in policy["steps"]}
    duplicate_real_model_gates = {"configured-model-browser-conversation", "configured-model-browser-campaign"}
    assert duplicate_real_model_gates.isdisjoint(steps)
    assert steps["production-certification-bundle"]["modes"] == ["release"]
    controller_source = (ROOT / "scripts/verify_production_certification_bundle.py").read_text(encoding="utf-8")
    browser_bundle_source = (ROOT / "scripts/verify_production_browser_bundle.py").read_text(encoding="utf-8")
    assert '"browser": SCRIPTS / "verify_production_browser_bundle.py"' in controller_source
    for marker in (
        '"configured-strong-context"',
        '"configured-strong-context-campaign"',
        '"--model-mode", "configured"',
        '"--runtime-profile", "protected-preprod"',
    ):
        assert marker in browser_bundle_source

    runner = (ROOT / "scripts/verify_product_browser_journey.py").read_text(encoding="utf-8")
    journey = (ROOT / "services/agent-service/frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
    assert "ProductRuntimeHarness(" in runner
    assert "deterministic_model=deterministic_model" in runner
    assert "protected_preprod=protected_preprod" in runner
    assert 'choices=("local", "protected-preprod")' in runner
    assert "_configured_model_preflight" in runner
    assert "ConfiguredModelEnvironmentBlocked" in runner
    assert "is_environmental_model_failure_category" in runner
    assert '"status": "BLOCKED_BY_ENVIRONMENT"' in runner
    assert 'modelMode === "configured"' in journey
    for marker in (
        "assistant rendered an empty live bubble",
        "assertReloadEquivalent",
        "live/history transcript mismatch",
        "我买过什么？",
        "我都买了什么",
        "哪些在路上",
        "可以退货退款吗？",
        "visible collection member eligibility",
        "acceptableAnyGroups",
        "其中最贵的是哪个？",
        "订单10004能开发票吗？我只问发票，不要退款，也不要售后。",
        "它现在能退吗？",
        "它是什么商品？",
        "requiredAll",
        "requiredAny",
        "forbidden",
        "clarificationResumeTurns",
        "short answer resumes refund eligibility",
        "clarificationAbandonTurns",
        "explicit new request abandons suspended refund goal",
        "先不问退款了，查订单10004能不能开发票",
    ):
        assert marker in journey
    assert 'forbidden: ["10003", "无线鼠标", "待发货"]' in journey
    assert 'forbidden: ["知识库资料不足", "已提交", "申请成功"]' in journey
    assert "reportedRegressionTurns" in journey
    assert "reported eligibility follow-up" in journey
    assert journey.count("sendTurn(page") >= 4


def test_real_model_lifecycle_gate_owns_the_service_model_identity() -> None:
    script = ROOT / "services/agent-service/scripts/verify_preprod_full_lifecycle.py"
    source = script.read_text(encoding="utf-8")
    assert "ProductRuntimeHarness(deterministic_model=False)" in source
    assert 'external_url = str(os.getenv("AGENT_TEST_URL")' not in source
    assert '"CAPABILITY_SEMANTIC_VERIFIER_MODE": "model"' in source
