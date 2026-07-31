#!/usr/bin/env python3
"""校验结构化结果的展示合同 Manifest 与正式 Release Gate 元数据。

该工具验证治理元数据，不规定领域 payload 的固定字段形状；PASS 不等于真实 UI 已通过端到端验收；还需 audit_structured_result_release.py 证明正式路径与 Legacy 不可达。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

VALID_PROFILES = {"dialogue_text", "structured_result", "transaction_outcome"}
VALID_VALIDATION_MODES = {"strict", "allow_optional_degradation"}
VALID_DEGRADATION = {"show_unavailable_and_trace", "controlled_error", "block_or_reconcile"}
VALID_TEST_LEVELS = {"unit", "contract", "end_to_end"}
REQUIRED = {
    "contract_id", "version", "result_profile", "contract_owner", "projection_boundary",
    "producer", "renderer", "adequacy", "validation_mode", "degradation_policy", "testing", "release",
}


def json_files(path: Path) -> Iterable[Path]:
    if path.is_file() and path.suffix == ".json":
        yield path
    elif path.exists():
        yield from sorted(path.rglob("*.json"))


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def string_list(value: Any) -> bool:
    return isinstance(value, list) and all(nonempty_string(item) for item in value)


def load_contracts(path: Path) -> list[tuple[Path, dict[str, Any]]]:
    contracts: list[tuple[Path, dict[str, Any]]] = []
    for file in json_files(path):
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取 {file}: {exc}") from exc
        if isinstance(raw, dict) and isinstance(raw.get("contracts"), list):
            for item in raw["contracts"]:
                if isinstance(item, dict):
                    contracts.append((file, item))
        elif isinstance(raw, dict):
            contracts.append((file, raw))
        else:
            raise ValueError(f"{file}: 顶层必须是合同对象或含 contracts 数组的对象")
    return contracts


def validate_contract(contract: Any, path: Path) -> list[str]:
    prefix = str(path)
    if not isinstance(contract, dict):
        return [f"{prefix}: 合同必须是对象"]
    contract_id = contract.get("contract_id", "<unknown>")
    errors: list[str] = []
    missing = sorted(REQUIRED - set(contract))
    if missing:
        return [f"{prefix}: {contract_id} 缺少字段：{', '.join(missing)}"]
    for key in ("contract_id", "contract_owner", "projection_boundary", "producer"):
        if not nonempty_string(contract.get(key)):
            errors.append(f"{prefix}: {contract_id} 的 {key} 必须是非空字符串")
    if not isinstance(contract.get("version"), int) or contract["version"] < 1:
        errors.append(f"{prefix}: {contract_id} 的 version 必须为 >=1 的整数")
    profile = contract.get("result_profile")
    if profile not in VALID_PROFILES:
        errors.append(f"{prefix}: {contract_id} 的 result_profile 必须为 {sorted(VALID_PROFILES)} 之一")
    renderer = contract.get("renderer")
    if not isinstance(renderer, dict) or not any(nonempty_string(v) for v in renderer.values()):
        errors.append(f"{prefix}: {contract_id} 必须声明至少一个 channel renderer")
    adequacy = contract.get("adequacy")
    if not isinstance(adequacy, dict):
        errors.append(f"{prefix}: {contract_id} 的 adequacy 必须是对象")
    else:
        required_visible = adequacy.get("required_visible_semantics", [])
        optional_visible = adequacy.get("optional_visible_semantics", [])
        if profile in {"structured_result", "transaction_outcome"} and (not string_list(required_visible) or not required_visible):
            errors.append(f"{prefix}: {contract_id} 的 {profile} 必须声明 required_visible_semantics；使用领域语义名，不要依赖固定字段名")
        if required_visible and not string_list(required_visible):
            errors.append(f"{prefix}: {contract_id} 的 required_visible_semantics 必须是字符串数组")
        if optional_visible and not string_list(optional_visible):
            errors.append(f"{prefix}: {contract_id} 的 optional_visible_semantics 必须是字符串数组")
        if profile in {"structured_result", "transaction_outcome"} and not nonempty_string(adequacy.get("rule")):
            errors.append(f"{prefix}: {contract_id} 必须声明 adequacy.rule")
    if contract.get("validation_mode") not in VALID_VALIDATION_MODES:
        errors.append(f"{prefix}: {contract_id} 的 validation_mode 必须为 {sorted(VALID_VALIDATION_MODES)} 之一")
    policy = contract.get("degradation_policy")
    if not isinstance(policy, dict):
        errors.append(f"{prefix}: {contract_id} 的 degradation_policy 必须是对象")
    else:
        if policy.get("optional_semantics") not in VALID_DEGRADATION:
            errors.append(f"{prefix}: {contract_id} 的 optional_semantics 策略无效")
        if policy.get("identity_or_decision_semantics") != "controlled_error":
            errors.append(f"{prefix}: {contract_id} 缺少身份/决策语义时必须 controlled_error，避免静默降级")
        if profile == "transaction_outcome" and policy.get("authority_or_receipt_semantics") != "block_or_reconcile":
            errors.append(f"{prefix}: {contract_id} 的事务结果缺少授权/Receipt 时必须 block_or_reconcile")
        if profile != "transaction_outcome" and policy.get("authority_or_receipt_semantics") not in VALID_DEGRADATION:
            errors.append(f"{prefix}: {contract_id} 的 authority_or_receipt_semantics 策略无效")
    release = contract.get("release")
    if not isinstance(release, dict):
        errors.append(f"{prefix}: {contract_id} 的 release 必须是对象")
    elif profile in {"structured_result", "transaction_outcome"}:
        if release.get("formal_response_eligible") is not True:
            errors.append(f"{prefix}: {contract_id} 的结构化/事务 Contract 必须 formal_response_eligible=true")
        if not nonempty_string(release.get("renderer_registry_key")):
            errors.append(f"{prefix}: {contract_id} 缺少 release.renderer_registry_key")
        if release.get("release_gate") != "StructuredResultReleaseGate":
            errors.append(f"{prefix}: {contract_id} 必须声明 release.release_gate=StructuredResultReleaseGate")
        legacy_replaces = release.get("legacy_output_replaces", [])
        if not isinstance(legacy_replaces, list) or not all(nonempty_string(item) for item in legacy_replaces):
            errors.append(f"{prefix}: {contract_id} 的 release.legacy_output_replaces 必须为字符串数组")
    testing = contract.get("testing")
    if not isinstance(testing, dict):
        errors.append(f"{prefix}: {contract_id} 的 testing 必须是对象")
    else:
        levels = testing.get("required_levels")
        if not string_list(levels) or any(level not in VALID_TEST_LEVELS for level in levels):
            errors.append(f"{prefix}: {contract_id} 的 testing.required_levels 必须为 {sorted(VALID_TEST_LEVELS)} 的非空数组")
        elif profile in {"structured_result", "transaction_outcome"} and "contract" not in levels:
            errors.append(f"{prefix}: {contract_id} 的结构化/事务结果至少需要 contract 测试")
        e2e_required = testing.get("end_to_end_required")
        if not isinstance(e2e_required, bool):
            errors.append(f"{prefix}: {contract_id} 的 testing.end_to_end_required 必须是布尔值")
        elif profile == "transaction_outcome" and e2e_required is not True:
            errors.append(f"{prefix}: {contract_id} 的 transaction_outcome 必须要求端到端验证")
        if profile in {"structured_result", "transaction_outcome"} and testing.get("release_gate_required") is not True:
            errors.append(f"{prefix}: {contract_id} 的结构化/事务结果必须 testing.release_gate_required=true")
        if profile in {"structured_result", "transaction_outcome"} and testing.get("coverage_evidence_required") is not True:
            errors.append(f"{prefix}: {contract_id} 的结构化/事务结果必须 testing.coverage_evidence_required=true")
        elif profile == "structured_result" and e2e_required is False and not nonempty_string(testing.get("end_to_end_reason")):
            errors.append(f"{prefix}: {contract_id} 若不要求结构化结果端到端验证，必须说明 end_to_end_reason")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验通用展示合同 Manifest 与 Release Gate 元数据")
    parser.add_argument("--contracts", required=True, action="append", help="单个 JSON 合同文件或目录；可重复指定")
    args = parser.parse_args(argv)
    errors: list[str] = []
    ids: set[str] = set()
    contracts: list[tuple[Path, dict[str, Any]]] = []
    for value in args.contracts:
        root = Path(value).resolve()
        try:
            contracts.extend(load_contracts(root))
        except ValueError as exc:
            errors.append(str(exc))
    if not contracts:
        errors.append("未找到任何展示合同")
    for path, contract in contracts:
        errors.extend(validate_contract(contract, path))
        cid = contract.get("contract_id") if isinstance(contract, dict) else None
        if isinstance(cid, str):
            if cid in ids:
                errors.append(f"重复 contract_id：{cid}")
            ids.add(cid)
    result = {
        "status": "PASS" if not errors else "FAIL",
        "contracts": len(contracts),
        "errors": errors,
        "note": "PASS 仅说明 Contract 与 Release Gate 元数据符合治理规则；真实项目仍需运行 audit_structured_result_release.py 并提供 API/SSE/Renderer 端到端证据。",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
