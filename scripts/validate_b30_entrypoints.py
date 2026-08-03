#!/usr/bin/env python3
"""Fail-closed validator for the B30 WP-01 runtime entrypoint inventory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_EXTERNAL = {
    "chat_turn_http",
    "chat_stream_sse",
    "transaction_start_http",
    "transaction_input_http",
    "transaction_authority_http",
    "transaction_reconcile_http",
    "business_resource_query_http",
    "transaction_query_http",
    "pending_interaction_query_http",
}
WORK_PACKAGES = {f"WP-{index:02d}" for index in range(1, 9)}
EFFECT_BEARING = {
    "conversation_mutation",
    "transaction_draft",
    "transaction_input",
    "transaction_commit",
    "transaction_recovery",
    "transaction_transition",
}
CONFORMANCE = {"PASS", "PASS_READ_PROJECTION", "PASS_WITH_ACCEPTANCE_TEST_REQUIRED", "GAP"}


class EntrypointContractError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EntrypointContractError(f"invalid_json:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise EntrypointContractError("inventory_root_must_be_object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EntrypointContractError(f"missing_or_empty:{label}")
    return value.strip()


def validate(inventory_path: Path, doc_path: Path) -> None:
    inventory = _read(inventory_path)
    documentation = doc_path.read_text(encoding="utf-8")
    if inventory.get("schema_version") != 1 or inventory.get("stage") != "B30":
        raise EntrypointContractError("entrypoint_schema_or_stage_invalid")
    _text(inventory.get("baseline_commit"), "baseline_commit")
    if set(inventory.get("required_external_entrypoint_ids") or []) != REQUIRED_EXTERNAL:
        raise EntrypointContractError("required_external_entrypoint_set_invalid")

    rows = inventory.get("entrypoints")
    if not isinstance(rows, list) or not rows:
        raise EntrypointContractError("entrypoints_missing")
    ids: list[str] = []
    external: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise EntrypointContractError("entrypoint_must_be_object")
        entry_id = _text(row.get("id"), "entrypoint.id")
        ids.append(entry_id)
        kind = _text(row.get("kind"), f"{entry_id}.kind")
        if kind == "external":
            external.add(entry_id)
        for field in ("source_path", "symbol", "transport", "effect_class", "terminal_projection", "conformance"):
            _text(row.get(field), f"{entry_id}.{field}")
        authorities = row.get("authority_sequence")
        if not isinstance(authorities, list) or not authorities or not all(isinstance(item, str) and item for item in authorities):
            raise EntrypointContractError(f"authority_sequence_invalid:{entry_id}")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise EntrypointContractError(f"entrypoint_evidence_missing:{entry_id}")
        state = str(row.get("conformance"))
        if state not in CONFORMANCE:
            raise EntrypointContractError(f"entrypoint_conformance_invalid:{entry_id}")
        if state == "GAP":
            package = _text(row.get("remediation_work_package"), f"{entry_id}.remediation_work_package")
            if package not in WORK_PACKAGES:
                raise EntrypointContractError(f"entrypoint_gap_work_package_invalid:{entry_id}")
            _text(row.get("fail_closed_behavior"), f"{entry_id}.fail_closed_behavior")
            if not isinstance(row.get("known_risks"), list) or not row["known_risks"]:
                raise EntrypointContractError(f"entrypoint_gap_risk_missing:{entry_id}")
        elif state == "PASS" and str(row.get("effect_class")) in EFFECT_BEARING:
            missing = {"TransactionRepository", "RuntimeOutcome"} - set(authorities)
            if missing:
                raise EntrypointContractError(
                    f"effect_bearing_pass_missing_authority:{entry_id}:{sorted(missing)}"
                )

    if len(ids) != len(set(ids)):
        raise EntrypointContractError("duplicate_entrypoint_id")
    if external != REQUIRED_EXTERNAL:
        raise EntrypointContractError(
            "external_entrypoint_inventory_incomplete:"
            + json.dumps(
                {"missing": sorted(REQUIRED_EXTERNAL - external), "extra": sorted(external - REQUIRED_EXTERNAL)},
                sort_keys=True,
            )
        )

    findings = inventory.get("wp01_findings")
    if not isinstance(findings, list) or not findings:
        raise EntrypointContractError("wp01_findings_missing")
    finding_ids: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise EntrypointContractError("wp01_finding_must_be_object")
        finding_id = _text(finding.get("id"), "wp01_finding.id")
        finding_ids.append(finding_id)
        for field in ("severity", "finding", "work_package", "required_resolution"):
            _text(finding.get(field), f"{finding_id}.{field}")
        if finding.get("work_package") not in WORK_PACKAGES:
            raise EntrypointContractError(f"wp01_finding_work_package_invalid:{finding_id}")
    if len(finding_ids) != len(set(finding_ids)):
        raise EntrypointContractError("duplicate_wp01_finding")

    for reference in (
        "chat_turn_http",
        "chat_stream_sse",
        "transaction_reconcile_http",
        "TurnRequestLedger",
        "RuntimeOutcome",
        "grounded_execution_plan",
        "WP-01",
    ):
        if reference not in documentation:
            raise EntrypointContractError(f"entrypoint_documentation_reference_missing:{reference}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("governance/architecture/b30-runtime-entrypoints.json"),
    )
    parser.add_argument(
        "--doc",
        type=Path,
        default=Path("docs/architecture/B30_RUNTIME_ENTRYPOINTS.md"),
    )
    args = parser.parse_args()
    try:
        validate(args.inventory, args.doc)
    except (EntrypointContractError, OSError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "PASS", "stage": "B30", "wp01": "MAPPED_WITH_GAPS"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
