from __future__ import annotations

"""Business Service runtime profile contract.

``APP_PROFILE`` is the sole selector shared with the Agent. Retired variables
are intentionally not read: they cannot select, downgrade, or conflict with the
active trust profile. Deployment validation should remove obsolete variables,
but runtime authority depends only on this one explicit contract.
"""

from enum import StrEnum
import os


class RuntimeProfile(StrEnum):
    LOCAL = "local"
    PREPROD = "preprod"
    PRODUCTION = "production"


def get_runtime_profile(*, strict: bool = False) -> RuntimeProfile | None:
    raw = (os.getenv("APP_PROFILE") or "").strip().lower()
    if not raw:
        if strict:
            raise RuntimeError(
                "APP_PROFILE is required. Set APP_PROFILE=local, preprod, or production."
            )
        return None
    try:
        return RuntimeProfile(raw)
    except ValueError as exc:
        raise RuntimeError(
            "APP_PROFILE must be one of: local, preprod, production"
        ) from exc
