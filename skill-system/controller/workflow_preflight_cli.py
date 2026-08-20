from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from capability_registry import CapabilityRegistryError  # type: ignore  # noqa: E402
from workflow_activation import (  # type: ignore  # noqa: E402
    WorkflowActivationError,
    activate_workflow,
)
from workflow_registry import WorkflowRegistryError  # type: ignore  # noqa: E402


def _preferences(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values:
        capability, separator, provider = str(raw).partition("=")
        capability = capability.strip()
        provider = provider.strip()
        if not separator or not capability or not provider:
            raise WorkflowActivationError("--prefer must use capability=provider format")
        if capability in result:
            raise WorkflowActivationError(f"duplicate provider preference: {capability}")
        result[capability] = provider
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve provider-neutral Workflow capability requirements against the active runtime providers"
    )
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument("--prefer", action="append", default=[])
    args = parser.parse_args()
    try:
        activation = activate_workflow(
            ROOT,
            workflow_id=args.workflow_id,
            available_provider_ids=args.provider,
            provider_preferences=_preferences(args.prefer),
        )
        payload = activation.as_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if activation.ready else 2
    except (CapabilityRegistryError, WorkflowActivationError, WorkflowRegistryError) as exc:
        print(
            json.dumps(
                {"schema": "workflow-activation@1", "status": "FAIL", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
