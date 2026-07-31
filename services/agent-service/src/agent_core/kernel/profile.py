from __future__ import annotations

"""Single runtime-profile resolver.

``APP_PROFILE`` is the only accepted profile selector.  A V20 workspace is a
current-contract runtime: deployment must declare its profile explicitly rather
than inferring it from retired environment variables.
"""

from dataclasses import dataclass
from enum import StrEnum
import os


class RuntimeProfile(StrEnum):
    LOCAL = "local"
    PREPROD = "preprod"
    PRODUCTION = "production"


_PROFILE_VALUES = {item.value for item in RuntimeProfile}


@dataclass(frozen=True)
class RuntimeProfileDiagnostics:
    profile: str | None
    source: str


def get_runtime_profile(*, strict: bool = False) -> RuntimeProfile | None:
    raw = (os.getenv("APP_PROFILE") or "").strip().lower()
    if raw:
        if raw not in _PROFILE_VALUES:
            raise RuntimeError("APP_PROFILE must be one of: local, preprod, production")
        return RuntimeProfile(raw)
    if strict:
        raise RuntimeError("APP_PROFILE is required. Set APP_PROFILE=local, preprod, or production.")
    return None


def require_runtime_profile() -> RuntimeProfile:
    profile = get_runtime_profile(strict=True)
    assert profile is not None
    return profile


def is_local_profile() -> bool:
    return get_runtime_profile(strict=False) is RuntimeProfile.LOCAL


def get_runtime_profile_diagnostics() -> RuntimeProfileDiagnostics:
    profile = get_runtime_profile(strict=False)
    return RuntimeProfileDiagnostics(
        profile=profile.value if profile else None,
        source="app_profile" if profile else "unconfigured",
    )


_VERIFIER_ALIASES = {"required": "model", "llm": "model", "candidate_only": "candidate"}
_VERIFIER_MODES = {"model", "candidate", "disabled"}


def resolve_verifier_mode(
    env_name: str,
    *,
    local_default: str = "candidate",
    model_when_local_key_present: bool = False,
) -> str:
    """Resolve one verifier mode with protected-profile fail-closed semantics.

    ``auto`` is configuration shorthand, not a runtime bypass.  In preprod and
    production every independent verifier resolves to ``model`` and an explicit
    ``candidate``/``disabled`` value raises immediately even when application
    startup validation was accidentally skipped.
    """
    configured = (os.getenv(env_name) or "auto").strip().lower()
    configured = _VERIFIER_ALIASES.get(configured, configured)
    if configured == "auto":
        profile = get_runtime_profile(strict=False)
        if profile in {RuntimeProfile.PREPROD, RuntimeProfile.PRODUCTION}:
            return "model"
        if model_when_local_key_present and os.getenv("OPENAI_API_KEY"):
            return "model"
        return local_default
    if configured not in _VERIFIER_MODES:
        raise RuntimeError(
            f"{env_name} must be one of: auto, model, candidate, disabled"
        )
    profile = get_runtime_profile(strict=False)
    if profile in {RuntimeProfile.PREPROD, RuntimeProfile.PRODUCTION} and configured != "model":
        raise RuntimeError(
            f"{env_name}={configured} is not allowed in preprod/production; use model or auto"
        )
    return configured
