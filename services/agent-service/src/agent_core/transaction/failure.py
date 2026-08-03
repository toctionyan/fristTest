from __future__ import annotations

"""Pure transaction failure classification.

This module has no persistence, configuration or graph dependency so recovery
and reconciliation can run in isolation after process restart.
"""


def classify_business_failure(*, code: int | str | None, error: str | None) -> str:
    """Classify an Agent observation without claiming business truth."""

    try:
        numeric = int(code) if code is not None else 0
    except (TypeError, ValueError):
        numeric = 0
    text = str(error or "").lower()
    if numeric in {0, 502, 503, 504} or any(
        token in text
        for token in (
            "timeout",
            "connection",
            "network",
            "temporarily",
            "request failed",
        )
    ):
        return "SUBMISSION_UNKNOWN"
    if numeric in {409, 423, 429}:
        return "FAILED_RETRYABLE"
    return "FAILED_FINAL"
