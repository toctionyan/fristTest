#!/usr/bin/env python3
"""审计正式结构化结果的 Release Gate、Renderer、覆盖度与 Legacy 可达性。

该工具审计项目登记册与真实运行证据的闭环；不规定领域 payload 字段形状。
PASS 不证明模型理解正确，亦不替代真实路由/导入图审查。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from validate_presentation_contracts import load_contracts, validate_contract

REQUIRED_POLICY = {
    "structured_result_requires_registered_contract": True,
    "structured_result_requires_registered_renderer": True,
    "formal_response_gate": "fail_closed",
    "unknown_structured_result_action": "projection_contract_violation",
    "legacy_structured_result_formal_reachability": "forbidden",
}
VALID_PROFILE = {"structured_result", "transaction_outcome"}
VALID_COVERAGE = {"full", "paged", "summary", "not_collection"}
VALID_LEGACY_DISPOSITION = {"delete", "replace", "read_only_migration", "isolate", "controlled_bridge"}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 {path}: {exc}") from exc


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def str_list(value: Any) -> bool:
    return isinstance(value, list) and all(nonempty(item) for item in value)


def load_evidence(path: Path) -> list[dict[str, Any]]:
    raw = load_json(path)
    if isinstance(raw, dict) and isinstance(raw.get("evidence"), list):
        return [item for item in raw["evidence"] if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [raw]
    raise ValueError(f"{path}: 证据顶层必须是对象或包含 evidence 数组")


def validate_coverage(value: Any, prefix: str, *, evidence: bool) -> list[str]:
    if not isinstance(value, dict):
        return [f"{prefix}: coverage 必须是对象"]
    errors: list[str] = []
    mode = value.get("mode")
    if mode not in VALID_COVERAGE:
        return [f"{prefix}: coverage.mode 必须为 {sorted(VALID_COVERAGE)} 之一"]
    if not nonempty(value.get("source_population")):
        errors.append(f"{prefix}: coverage.source_population 必须是领域定义的非空语义")
    if mode == "full":
        proof_key = "presented_population_proof" if not evidence else "status"
        if not nonempty(value.get(proof_key)):
            errors.append(f"{prefix}: full coverage 缺少 {proof_key}")
        if evidence:
            if value.get("status") != "complete":
                errors.append(f"{prefix}: full coverage 证据必须 status=complete")
            for key in ("resolved_member_count", "presented_member_count"):
                if not isinstance(value.get(key), int) or value[key] < 0:
                    errors.append(f"{prefix}: full coverage 缺少非负整数 {key}")
            if isinstance(value.get("resolved_member_count"), int) and isinstance(value.get("presented_member_count"), int) and value["resolved_member_count"] != value["presented_member_count"]:
                errors.append(f"{prefix}: full coverage 解析成员数与展示成员数不一致")
    elif mode == "paged":
        required = ("total_count_semantic", "continuation_semantic") if not evidence else ("status", "total_count", "presented_member_count", "continuation_exposed")
        for key in required:
            if evidence and key in {"total_count", "presented_member_count"}:
                ok = isinstance(value.get(key), int) and value[key] >= 0
            elif evidence and key == "continuation_exposed":
                ok = value.get(key) is True
            else:
                ok = nonempty(value.get(key))
            if not ok:
                errors.append(f"{prefix}: paged coverage 缺少 {key}")
        if evidence and value.get("status") != "partial_visible":
            errors.append(f"{prefix}: paged coverage 证据必须 status=partial_visible")
    elif mode == "summary":
        key = "summary_scope_semantic" if not evidence else "status"
        if not nonempty(value.get(key)):
            errors.append(f"{prefix}: summary coverage 缺少 {key}")
        if evidence and value.get("status") != "summary_visible":
            errors.append(f"{prefix}: summary coverage 证据必须 status=summary_visible")
    else:  # not_collection
        key = "not_collection_reason" if not evidence else "status"
        if not nonempty(value.get(key)):
            errors.append(f"{prefix}: not_collection 缺少 {key}")
        if evidence and value.get("status") != "not_applicable":
            errors.append(f"{prefix}: not_collection 证据必须 status=not_applicable")
    return errors


def audit(inventory_path: Path, contracts_path: Path, evidence_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        inventory = load_json(inventory_path)
    except ValueError as exc:
        inventory = {}
        errors.append(str(exc))
    try:
        contracts = load_contracts(contracts_path)
    except ValueError as exc:
        contracts = []
        errors.append(str(exc))
    try:
        evidence_items = load_evidence(evidence_path)
    except ValueError as exc:
        evidence_items = []
        errors.append(str(exc))

    by_contract: dict[str, dict[str, Any]] = {}
    for path, contract in contracts:
        errors.extend(validate_contract(contract, path))
        cid = contract.get("contract_id") if isinstance(contract, dict) else None
        if nonempty(cid):
            by_contract[cid] = contract

    if not isinstance(inventory, dict):
        errors.append("inventory 顶层必须是对象")
        inventory = {}
    policy = inventory.get("release_policy")
    if not isinstance(policy, dict):
        errors.append("inventory.release_policy 必须是对象")
    else:
        for key, expected in REQUIRED_POLICY.items():
            if policy.get(key) != expected:
                errors.append(f"release_policy.{key} 必须为 {expected!r}")
    in_scope = inventory.get("structured_results_in_scope")
    if not isinstance(in_scope, bool):
        errors.append("structured_results_in_scope 必须是布尔值")
    entrypoints = inventory.get("formal_entrypoints")
    if not isinstance(entrypoints, list) or not entrypoints:
        errors.append("formal_entrypoints 必须是非空数组")
        entrypoints = []
    entrypoint_ids: set[str] = set()
    for item in entrypoints:
        if not isinstance(item, dict) or not nonempty(item.get("entrypoint_id")) or not nonempty(item.get("path")):
            errors.append("formal_entrypoints 每项必须有 entrypoint_id 与 path")
            continue
        eid = item["entrypoint_id"]
        if eid in entrypoint_ids:
            errors.append(f"formal_entrypoints 存在重复 entrypoint_id：{eid}")
        entrypoint_ids.add(eid)

    producers = inventory.get("structured_result_producers")
    if not isinstance(producers, list):
        errors.append("structured_result_producers 必须是数组")
        producers = []
    if in_scope is False:
        if producers:
            errors.append("structured_results_in_scope=false 时不得声明 active structured producers")
        if not nonempty(inventory.get("no_structured_results_reason")):
            errors.append("structured_results_in_scope=false 时必须填写 no_structured_results_reason")
    elif not producers:
        errors.append("structured_results_in_scope=true 时必须盘点至少一个 formal structured producer")

    evidence_by_case = {item.get("case_id"): item for item in evidence_items if nonempty(item.get("case_id"))}
    producer_ids: set[str] = set()
    for item in producers:
        prefix = f"producer:{item.get('producer_id', '<unknown>')}"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: producer 必须是对象")
            continue
        pid = item.get("producer_id")
        if not nonempty(pid):
            errors.append(f"{prefix}: 缺少 producer_id")
        elif pid in producer_ids:
            errors.append(f"重复 producer_id：{pid}")
        else:
            producer_ids.add(pid)
        if item.get("release_state") != "active":
            errors.append(f"{prefix}: 正式 producer 的 release_state 必须为 active；旧资产请登记到 legacy_presentation_assets")
        if item.get("result_profile") not in VALID_PROFILE:
            errors.append(f"{prefix}: result_profile 必须为 {sorted(VALID_PROFILE)} 之一")
        eps = item.get("formal_entrypoints")
        if not str_list(eps) or not eps:
            errors.append(f"{prefix}: formal_entrypoints 必须为非空字符串数组")
        else:
            missing = [x for x in eps if x not in entrypoint_ids]
            if missing:
                errors.append(f"{prefix}: 引用了未登记 formal_entrypoints：{missing}")
        cid = item.get("contract_id")
        contract = by_contract.get(cid) if nonempty(cid) else None
        if contract is None:
            errors.append(f"{prefix}: contract_id 未在 contracts 中注册：{cid!r}")
        else:
            if contract.get("result_profile") != item.get("result_profile"):
                errors.append(f"{prefix}: result_profile 与 Contract 不一致")
            if contract.get("projection_boundary") != item.get("projection_boundary"):
                errors.append(f"{prefix}: projection_boundary 与 Contract 不一致")
            release = contract.get("release")
            if not isinstance(release, dict) or release.get("formal_response_eligible") is not True or not nonempty(release.get("renderer_registry_key")):
                errors.append(f"{prefix}: Contract 必须声明 formal_response_eligible 与 renderer_registry_key")
        rr = item.get("renderer_requirements")
        if not isinstance(rr, dict) or not rr or not all(nonempty(channel) and nonempty(renderer) for channel, renderer in rr.items()):
            errors.append(f"{prefix}: renderer_requirements 必须声明至少一个渠道 Renderer")
        elif contract is not None:
            for channel, renderer in rr.items():
                if contract.get("renderer", {}).get(channel) != renderer:
                    errors.append(f"{prefix}: channel {channel!r} 的 Renderer 与 Contract 不一致")
        errors.extend(validate_coverage(item.get("coverage"), prefix, evidence=False))
        case_id = item.get("e2e_case_id")
        if not nonempty(case_id):
            errors.append(f"{prefix}: 缺少 e2e_case_id")
            continue
        evidence = evidence_by_case.get(case_id)
        if not isinstance(evidence, dict):
            errors.append(f"{prefix}: 未找到 e2e 证据 case_id={case_id!r}")
            continue
        if evidence.get("producer_id") != pid or evidence.get("contract_id") != cid:
            errors.append(f"{prefix}: e2e 证据 producer_id/contract_id 不一致")
        if not isinstance(evidence.get("provenance"), dict) or evidence["provenance"].get("kind") != "runtime_instrumentation":
            errors.append(f"{prefix}: e2e 证据必须来自 runtime_instrumentation，fixture 不可作为正式验收")
        pipeline = evidence.get("pipeline")
        required_stages = {"formal_entrypoint", "producer", "projection_boundary", "structured_result_release_gate", "api_or_sse_payload", "channel_renderer"}
        if not isinstance(pipeline, list) or not required_stages.issubset(set(pipeline)):
            errors.append(f"{prefix}: e2e pipeline 必须覆盖 {sorted(required_stages)}")
        assertions = evidence.get("assertions")
        if not isinstance(assertions, dict):
            errors.append(f"{prefix}: e2e assertions 必须是对象")
        else:
            for key in ("formal_release_gate_passed", "contract_preserved", "renderer_registered", "renderer_contract_preserved"):
                if assertions.get(key) is not True:
                    errors.append(f"{prefix}: e2e assertions.{key} 必须为 true")
            if assertions.get("legacy_path_reachable") is not False:
                errors.append(f"{prefix}: e2e assertions.legacy_path_reachable 必须为 false")
            if assertions.get("semantic_fallback_used") is not False:
                errors.append(f"{prefix}: e2e assertions.semantic_fallback_used 必须为 false")
            errors.extend(validate_coverage(assertions.get("coverage"), prefix, evidence=True))
        rendering = evidence.get("rendering_evidence")
        if not isinstance(rendering, dict) or not nonempty(rendering.get("kind")) or not nonempty(rendering.get("location")):
            errors.append(f"{prefix}: 必须有 DOM/accessibility/channel payload 等 rendering_evidence")

    legacy = inventory.get("legacy_presentation_assets", [])
    if not isinstance(legacy, list):
        errors.append("legacy_presentation_assets 必须是数组")
        legacy = []
    legacy_ids: set[str] = set()
    for item in legacy:
        prefix = f"legacy:{item.get('asset_id', '<unknown>')}"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: legacy asset 必须是对象")
            continue
        aid = item.get("asset_id")
        if not nonempty(aid):
            errors.append(f"{prefix}: 缺少 asset_id")
        elif aid in legacy_ids:
            errors.append(f"重复 legacy asset_id：{aid}")
        else:
            legacy_ids.add(aid)
        if not nonempty(item.get("path")) or not nonempty(item.get("kind")):
            errors.append(f"{prefix}: 必须声明 path 与 kind")
        if item.get("disposition") not in VALID_LEGACY_DISPOSITION:
            errors.append(f"{prefix}: disposition 必须为 {sorted(VALID_LEGACY_DISPOSITION)} 之一")
        if item.get("formal_reachable") is not False:
            errors.append(f"{prefix}: Legacy 展示资产不得从正式入口可达")
        if not nonempty(item.get("unreachability_evidence")):
            errors.append(f"{prefix}: 必须声明 unreachability_evidence")
        if not nonempty(item.get("owner")) or not nonempty(item.get("remove_when")):
            errors.append(f"{prefix}: 必须声明 owner 与 remove_when")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "inventory": str(inventory_path),
        "contracts": len(contracts),
        "formal_entrypoints": len(entrypoints),
        "formal_producers": len(producers),
        "legacy_assets": len(legacy),
        "errors": errors,
        "warnings": warnings,
        "note": "PASS 证明登记册、Contract、Release Gate 证据、Renderer 与 Legacy 不可达声明闭环；仍应结合真实路由/导入图和运行时 Trace 审查。",
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审计正式结构化结果 Release Gate 与 Legacy 可达性")
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--contracts", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args(argv)
    report = audit(Path(args.inventory).resolve(), Path(args.contracts).resolve(), Path(args.evidence).resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
