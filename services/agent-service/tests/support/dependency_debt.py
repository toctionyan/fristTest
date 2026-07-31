from __future__ import annotations

from typing import Any


def assert_dependency_debt_monotonic(
    payload: dict[str, Any],
    *,
    removed_member: str,
    maximum_current_members: int,
) -> None:
    """Accept continued reduction or final resolution, never debt growth."""

    architecture_status = str(payload.get("architecture_status") or "")
    status = str(payload.get("architecture_debt_status") or "")
    assert architecture_status in {"PASS_WITH_DEBT", "PASS"}
    debt = payload["checks"]["dependency_cycle_debt"]
    assert status in {"REDUCED", "RESOLVED"}
    assert int(debt["current_member_count"]) <= int(maximum_current_members)
    assert all(removed_member not in cycle for cycle in debt["current_cycles"])
    if status == "RESOLVED":
        assert architecture_status == "PASS"
        assert debt["current_cycles"] == []
        resolved_members = {
            member
            for row in debt["resolved_components"]
            for member in list(row.get("members") or [])
        }
        assert removed_member in resolved_members
    else:
        assert architecture_status == "PASS_WITH_DEBT"
        assert any(
            removed_member in row.get("removed_members", [])
            for row in debt["matches"]
        )
