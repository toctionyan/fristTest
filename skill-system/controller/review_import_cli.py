from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from .agent_attestation import (
        IMPLEMENTER_ROLE,
        import_attestation,
        load_manifest,
        register_implementer,
    )
    from .contract import load_contract
    from .multi_agent_governance import (
        validate_multi_agent_begin_ready,
        validate_multi_agent_verification_ready,
    )
except ImportError:
    from agent_attestation import (  # type: ignore
        IMPLEMENTER_ROLE,
        import_attestation,
        load_manifest,
        register_implementer,
    )
    from contract import load_contract  # type: ignore
    from multi_agent_governance import (  # type: ignore
        validate_multi_agent_begin_ready,
        validate_multi_agent_verification_ready,
    )


def _workspace() -> Path:
    return Path.cwd().resolve()


def _contract() -> dict[str, object]:
    return load_contract(_workspace(), require_approved=False).payload


def _identity_arg(value: str | None, env_name: str) -> str:
    result = (value or os.environ.get(env_name, "")).strip()
    if not result:
        raise ValueError(f"{env_name} or its CLI override is required")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex multi-agent attestation import and validation")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register-implementer")
    register.add_argument("--provider", choices=["codex-cloud", "codex-app", "codex-cli"], default="codex-cloud")
    register.add_argument("--task-id")
    register.add_argument("--thread-id")
    register.add_argument("--worktree-id")
    register.add_argument("--baseline-commit")
    register.add_argument("--replace", action="store_true")

    import_cmd = sub.add_parser("import-review")
    import_cmd.add_argument(
        "--role",
        required=True,
        choices=[
            "failure-explorer",
            "repair-plan-reviewer",
            "diff-integrity-reviewer",
            "closure-arbiter",
        ],
    )
    import_cmd.add_argument("--artifact", required=True)
    import_cmd.add_argument("--attestation", required=True)
    import_cmd.add_argument("--replace", action="store_true")

    validate = sub.add_parser("validate")
    validate.add_argument("--stage", choices=["begin", "verification"], required=True)
    validate.add_argument("--result", default="CONVERGED")

    sub.add_parser("status")
    args = parser.parse_args()

    workspace = _workspace()
    contract = _contract()
    try:
        if args.command == "register-implementer":
            result = register_implementer(
                workspace,
                contract,
                provider=args.provider,
                task_id=_identity_arg(args.task_id, "CODEX_TASK_ID"),
                thread_id=_identity_arg(args.thread_id, "CODEX_THREAD_ID"),
                worktree_id=_identity_arg(args.worktree_id, "CODEX_WORKTREE_ID"),
                baseline_commit=args.baseline_commit,
                replace=args.replace,
            )
        elif args.command == "import-review":
            result = import_attestation(
                workspace,
                contract,
                role=args.role,
                artifact_source=Path(args.artifact).resolve(),
                attestation_source=Path(args.attestation).resolve(),
                replace=args.replace,
            )
        elif args.command == "validate":
            result = (
                validate_multi_agent_begin_ready(workspace, contract)
                if args.stage == "begin"
                else validate_multi_agent_verification_ready(
                    workspace,
                    contract,
                    expected_result=args.result,
                )
            )
        else:
            case_dir = workspace / str(contract.get("repair_governance") or "")
            manifest = load_manifest(case_dir)
            result = {
                "status": "PASS",
                "change_id": contract.get("change_id"),
                "baseline_commit": manifest.get("baseline_commit"),
                "stages": manifest.get("stages"),
            }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "FAIL", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
