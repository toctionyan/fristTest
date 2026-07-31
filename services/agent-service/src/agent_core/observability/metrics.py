from __future__ import annotations

from typing import Any, Protocol


class TraceMetricsReader(Protocol):
    def query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]: ...


def simple_metrics(trace_logger: TraceMetricsReader) -> dict[str, int]:
    rows = trace_logger.query_all("SELECT event_type, COUNT(*) as cnt FROM trace_logs GROUP BY event_type")
    return {str(row["event_type"]): int(row["cnt"]) for row in rows}
