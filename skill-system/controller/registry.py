from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "skill-system" / "registry"
RULE_LEVELS = {"HARD_INVARIANT", "STRONG_DEFAULT", "REFERENCE_PATTERN", "PROJECT_BASELINE", "WORKFLOW_DEFAULT", "EXAMPLE_ONLY"}


def _load(name: str, errors: list[str]) -> dict[str, Any] | None:
    path = REGISTRY / name
    if not path.is_file():
        errors.append(f"missing_registry:{name}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"invalid_json:{name}:{exc}")
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        errors.append(f"invalid_schema_version:{name}")
        return None
    return payload


def verify() -> list[str]:
    errors: list[str] = []
    required = [
        "active-rules.json",
        "active-skills.json",
        "active-policies.json",
        "active-targets.json",
        "active-claims.json",
        "deprecated-rules.json",
    ]
    loaded = {name: _load(name, errors) for name in required}

    rules_payload = loaded.get("active-rules.json") or {}
    rules = rules_payload.get("rules") or []
    ids = [str(row.get("id") or "") for row in rules if isinstance(row, dict)]
    if not ids or len(ids) != len(set(ids)):
        errors.append("missing_or_duplicate_active_rule_ids")
    active_rule_ids = set(ids)
    required_rule_fields = {
        "id", "level", "scope", "rationale", "verification", "owner",
        "introduced_at", "review_date", "variance_allowed", "status",
    }
    for row in rules:
        if not isinstance(row, dict):
            errors.append("invalid_rule_row")
            continue
        missing = required_rule_fields.difference(row)
        if missing:
            errors.append(f"rule_missing_fields:{row.get('id')}:{','.join(sorted(missing))}")
        if row.get("level") not in RULE_LEVELS:
            errors.append(f"invalid_rule_level:{row.get('id')}")
        scope = row.get("scope")
        if not isinstance(scope, dict) or not scope.get("applies_to"):
            errors.append(f"invalid_rule_scope:{row.get('id')}")
        if not row.get("verification"):
            errors.append(f"rule_without_verification:{row.get('id')}")
        if row.get("level") == "HARD_INVARIANT" and row.get("variance_allowed") is not False:
            errors.append(f"hard_invariant_allows_variance:{row.get('id')}")

    skills_payload = loaded.get("active-skills.json") or {}
    skill_names: set[str] = set()
    for row in skills_payload.get("skills") or []:
        if not isinstance(row, dict):
            errors.append("invalid_skill_row")
            continue
        name = str(row.get("name") or "")
        path = ROOT / str(row.get("path") or "")
        if not name or name in skill_names:
            errors.append(f"missing_or_duplicate_skill:{name}")
        skill_names.add(name)
        if not path.is_file():
            errors.append(f"active_skill_path_missing:{name}:{path.relative_to(ROOT)}")

    policies_payload = loaded.get("active-policies.json") or {}
    for raw in policies_payload.get("policies") or []:
        path = ROOT / str(raw)
        if not path.is_file():
            errors.append(f"active_policy_path_missing:{raw}")

    deprecated_payload = loaded.get("deprecated-rules.json") or {}
    deprecated_ids: set[str] = set()
    for row in deprecated_payload.get("rules") or []:
        if not isinstance(row, dict):
            errors.append("invalid_deprecated_rule_row")
            continue
        rule_id = str(row.get("id") or "")
        replacement = str(row.get("replacement") or "")
        if not rule_id or rule_id in deprecated_ids:
            errors.append(f"missing_or_duplicate_deprecated_rule:{rule_id}")
        deprecated_ids.add(rule_id)
        if not replacement:
            errors.append(f"deprecated_rule_without_replacement:{rule_id}")
        elif replacement not in active_rule_ids and replacement not in {"FOCUSED_SKILLS_WITH_SHARED_CORE"}:
            errors.append(f"deprecated_rule_replacement_not_active:{rule_id}:{replacement}")
    if active_rule_ids & deprecated_ids:
        errors.append("rule_is_both_active_and_deprecated")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.parse_args()
    errors = verify()
    print(json.dumps({"status": "PASS" if not errors else "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
