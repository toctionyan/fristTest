from __future__ import annotations

"""Canonical direct-API projection for runtime conclusions.

Chat graph and structured API use cases must not each invent customer text for
rejections or infrastructure failures.  They create a RuntimeOutcome and use
this seam to pass it through the same ResponseProjector as graph results.
"""

from typing import Any

from app.schemas.chat_schema import ChatResponse
from agent_core.runtime.outcomes import RuntimeOutcome


def project_runtime_outcome(
    service: Any,
    *,
    thread_id: str,
    value: RuntimeOutcome,
    include_debug: bool,
) -> ChatResponse:
    result = {
        "runtime_outcome": value.as_dict(),
        "correlation_id": value.correlation_id,
        "sources": [],
    }
    response = service._normalize(thread_id, result, include_debug=include_debug)
    service._persist_public_response(thread_id, response, result)
    return response
