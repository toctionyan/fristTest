from __future__ import annotations

"""Bounded retention for persisted traces and action audits."""

import os
from datetime import datetime, timedelta, timezone
from typing import Any


def trace_retention_days() -> int:
    raw = (os.getenv("TRACE_RETENTION_DAYS") or "30").strip()
    try:
        days = int(raw)
    except ValueError as exc:
        raise ValueError("TRACE_RETENTION_DAYS must be a positive integer") from exc
    if days <= 0:
        raise ValueError("TRACE_RETENTION_DAYS must be a positive integer")
    return days


def prune_observability(provider: Any) -> dict[str, int | str]:
    """Prune persisted observability data once at process initialization."""
    days = trace_retention_days()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    traces = int(provider.traces.prune_older_than(cutoff))
    audits = int(provider.action_audits.prune_older_than(cutoff))
    return {"retention_days": days, "cutoff": cutoff, "traces_deleted": traces, "audits_deleted": audits}
