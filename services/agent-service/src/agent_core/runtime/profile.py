from __future__ import annotations

"""Compatibility exports for the Kernel-owned runtime profile contract.

Deployment-profile ownership lives in :mod:`agent_core.kernel.profile`.
Runtime keeps this import surface temporarily so existing callers receive the
same classes and resolver functions without a second implementation.
"""

from agent_core.kernel.profile import (
    RuntimeProfile,
    RuntimeProfileDiagnostics,
    get_runtime_profile,
    get_runtime_profile_diagnostics,
    is_local_profile,
    require_runtime_profile,
    resolve_verifier_mode,
)

__all__ = [
    "RuntimeProfile",
    "RuntimeProfileDiagnostics",
    "get_runtime_profile",
    "get_runtime_profile_diagnostics",
    "is_local_profile",
    "require_runtime_profile",
    "resolve_verifier_mode",
]
