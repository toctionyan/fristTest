from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Mapping, Sequence

from github_repository_onboarding_transport import (
    GitHubReadTransport,
    GitHubRepositoryOnboardingError,
    GitHubRepositoryOnboardingTransport,
)


ROOT = Path(__file__).resolve().parents[2]
TOKEN_VARIABLE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class RepositoryOnboardingCliError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillctl repository-onboarding",
        description=(
            "Collect sealed names-only GitHub repository metadata and delegate "
            "readiness to the existing deterministic onboarding preflight."
        ),
    )
    commands = parser.add_subparsers(dest="onboarding_command", required=True)
    for name in ("collect", "preflight"):
        command = commands.add_parser(name)
        command.add_argument("--repository", required=True, help="exact owner/name")
        command.add_argument("--workspace-root", default=str(ROOT))
        command.add_argument("--output")
        command.add_argument("--api-base", default="https://api.github.com")
        command.add_argument(
            "--token-environment-variable",
            default="GITHUB_TOKEN",
            help="uppercase environment-variable name; the value is never persisted",
        )
        if name == "preflight":
            command.add_argument("--allow-public", action="store_true")
    return parser


def _preflight_module() -> ModuleType:
    script = ROOT / "scripts/repository_onboarding_preflight.py"
    spec = importlib.util.spec_from_file_location("repository_onboarding_preflight_live", script)
    if spec is None or spec.loader is None:
        raise RepositoryOnboardingCliError("repository onboarding evaluator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _output_path(workspace: Path, repository: str, requested: str | None) -> Path:
    root = workspace.resolve()
    requested_path = Path(requested) if requested else None
    candidate = (
        ((root / requested_path) if not requested_path.is_absolute() else requested_path).resolve()
        if requested_path is not None
        else root
        / ".harness/runtime/repository-onboarding"
        / f"{repository.replace('/', '--')}.json"
    )
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RepositoryOnboardingCliError("--output must remain inside --workspace-root") from exc
    return candidate


def _blocked(error: Exception) -> dict[str, object]:
    return {
        "schema": "github-repository-onboarding-result@1",
        "status": "BLOCKED",
        "error_code": (
            error.code
            if isinstance(error, GitHubRepositoryOnboardingError)
            else "ONBOARDING_PROCESS_ERROR"
        ),
        "error": str(error),
        "authority_effect": False,
        "deploy_allowed": False,
        "production_closed": False,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: GitHubReadTransport | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    try:
        variable = args.token_environment_variable.strip()
        if not TOKEN_VARIABLE_RE.fullmatch(variable):
            raise RepositoryOnboardingCliError(
                "--token-environment-variable must be a stable uppercase name"
            )
        token = environment.get(variable, "")
        if not token:
            raise RepositoryOnboardingCliError(
                f"required environment variable is empty: {variable}"
            )
        workspace = Path(args.workspace_root).resolve()
        if not workspace.is_dir():
            raise RepositoryOnboardingCliError("--workspace-root must be an existing directory")
        output = _output_path(workspace, args.repository, args.output)
        collector = GitHubRepositoryOnboardingTransport(
            repository_full_name=args.repository,
            token=token,
            api_base=args.api_base,
            transport=transport,
        )
        artifact = collector.collect_and_write(output)
        artifact = collector.load_artifact(
            output, expected_seal_sha256=artifact["seal_sha256"]
        )
        if args.onboarding_command == "collect":
            result = {
                "schema": "github-repository-onboarding-result@1",
                "status": "COLLECTED",
                "repository": artifact["repository"],
                "metadata_artifact": str(output),
                "metadata_seal_sha256": artifact["seal_sha256"],
                "authority_effect": False,
                "deploy_allowed": False,
                "production_closed": False,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0

        evaluator = _preflight_module()
        result = evaluator.evaluate(
            workspace,
            repository_metadata=artifact["metadata"],
            allow_public=bool(args.allow_public),
        )
        result.update(
            {
                "metadata_artifact": str(output),
                "metadata_seal_sha256": artifact["seal_sha256"],
                "authority_effect": False,
                "deploy_allowed": False,
                "production_closed": False,
            }
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["status"] == "PASS" else (
            78 if result["status"] == "BLOCKED_BY_ENVIRONMENT" else 1
        )
    except (
        GitHubRepositoryOnboardingError,
        RepositoryOnboardingCliError,
        OSError,
    ) as exc:
        print(
            json.dumps(_blocked(exc), ensure_ascii=False, indent=2, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    except Exception:
        error = RepositoryOnboardingCliError(
            "repository onboarding failed closed; inspect GitHub access and sealed local evidence"
        )
        print(
            json.dumps(_blocked(error), ensure_ascii=False, indent=2, sort_keys=True),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
