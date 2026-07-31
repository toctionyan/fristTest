
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class RuleLevel(str, Enum):
    HARD_INVARIANT = "HARD_INVARIANT"
    STRONG_DEFAULT = "STRONG_DEFAULT"
    REFERENCE_PATTERN = "REFERENCE_PATTERN"
    PROJECT_BASELINE = "PROJECT_BASELINE"
    WORKFLOW_DEFAULT = "WORKFLOW_DEFAULT"
    EXAMPLE_ONLY = "EXAMPLE_ONLY"


class TargetKind(str, Enum):
    DIAGNOSIS = "diagnosis"
    DESIGN = "design"
    ORACLE_REVIEW = "oracle-review"
    REPAIR = "repair"
    MIGRATION = "migration"
    REVERT = "revert"
    CERTIFICATION = "certification"

    @property
    def requires_candidate_change(self) -> bool:
        return self in {self.REPAIR, self.MIGRATION, self.REVERT}


@dataclass(frozen=True)
class ChangeContract:
    path: Path
    payload: dict[str, Any]

    @property
    def change_id(self) -> str:
        return str(self.payload["change_id"])

    @property
    def target_kind(self) -> TargetKind:
        return TargetKind(str(self.payload["target_kind"]))

    @property
    def profile(self) -> str:
        return str(self.payload["profile"])

    @property
    def allowed_paths(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.payload.get("allowed_paths") or [])

    @property
    def forbidden_paths(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.payload.get("forbidden_paths") or [])

    @property
    def status(self) -> str:
        return str(self.payload.get("status") or "")
