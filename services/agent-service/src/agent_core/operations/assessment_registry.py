from __future__ import annotations

from typing import Iterable

from agent_core.operations.assessment import OperationAssessmentDefinition


class OperationAssessmentRegistry:
    def __init__(self, assessments: Iterable[OperationAssessmentDefinition]) -> None:
        rows = list(assessments)
        self._assessments = {row.assessment_id: row for row in rows}
        if len(rows) != len(self._assessments):
            raise ValueError("duplicate operation assessment registration")

    def all(self) -> list[OperationAssessmentDefinition]:
        return list(self._assessments.values())

    def get(self, assessment_id: str) -> OperationAssessmentDefinition | None:
        return self._assessments.get(str(assessment_id or "").strip())

    def ids(self) -> set[str]:
        return set(self._assessments)
