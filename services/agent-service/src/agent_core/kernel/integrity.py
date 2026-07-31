from __future__ import annotations

"""Startup-time RuntimeRegistry integrity checks.

The Kernel validates a registry supplied by the application Composition Root.
It never locates installed modules or another global registry provider.
"""

from typing import Any

from agent_core.kernel.registry import RuntimeRegistry


def validate_runtime_architecture(registry: RuntimeRegistry) -> dict[str, Any]:
    registry.validate_integrity()
    return {
        "architecture_version": "18.0",
        "resource_types": sorted(registry.resource_types()),
        "operation_ids": sorted(registry.preparable_action_ids()),
        "assessment_ids": sorted(registry.assessment_ids()),
    }
