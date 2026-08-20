from __future__ import annotations

"""External integration capability registry.

GitHub, GitLab, CI systems and deployment platforms are integrations, not Skills.
Workflow definitions decide whether an integration is required.
"""

from dataclasses import dataclass
from pathlib import Path
import json


@dataclass(frozen=True)
class IntegrationDefinition:
    integration_id: str
    enabled: bool
    capabilities: tuple[str, ...]


class IntegrationRegistry:
    def __init__(self, registry_file: Path):
        self.registry_file = registry_file

    def load(self) -> dict[str, IntegrationDefinition]:
        if not self.registry_file.exists():
            return {}
        data = json.loads(self.registry_file.read_text(encoding="utf-8"))
        result = {}
        for item in data.get("integrations", []):
            key = str(item.get("id", "")).strip()
            if not key:
                continue
            result[key] = IntegrationDefinition(
                integration_id=key,
                enabled=bool(item.get("enabled", False)),
                capabilities=tuple(item.get("capabilities", [])),
            )
        return result

    def require(self, integration_id: str) -> IntegrationDefinition:
        item = self.load().get(integration_id)
        if item is None:
            raise ValueError(f"integration not configured: {integration_id}")
        return item
