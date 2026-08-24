from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parent
ROOT = CONTROLLER.parents[1]
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from harness_authoring import (  # type: ignore  # noqa: E402
    HarnessAuthoringError,
    compile_workflow_declaration,
    explain_workflow,
    initialize_project,
    load_declaration,
    validate_declaration,
)
from harness_composition import compose_workflow  # type: ignore  # noqa: E402
from harness_starter import (  # type: ignore  # noqa: E402
    initialize_starter,
    list_builtin_starters,
    verify_starter,
)


def _pairs(values: list[str], *, option: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values:
        key, separator, value = raw.partition("=")
        key = key.strip()
        value = value.strip()
        if not separator or not key or not value:
            raise HarnessAuthoringError(f"{option} must use key=value format")
        if key in result:
            raise HarnessAuthoringError(f"duplicate {option} key: {key}")
        result[key] = value
    return result


def _write_or_print(payload: dict[str, object], output: str | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(rendered, end="")
        return
    target = Path(output)
    if target.exists():
        raise HarnessAuthoringError(f"refusing to overwrite existing output: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {"schema": "harness-authoring-result@1", "status": "PASS", "output": str(target)},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def _write_json(payload: dict[str, object], output: str) -> None:
    target = Path(output)
    if target.exists():
        raise HarnessAuthoringError(f"refusing to overwrite existing output: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _project_path(project_workspace: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_workspace / path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Initialize, validate, compose, compile, and explain portable Harness declarations"
        )
    )
    commands = parser.add_subparsers(dest="authoring_command", required=True)

    project_init = commands.add_parser("project-init", help="generate a minimal Project declaration")
    project_init.add_argument("--output", required=True)
    project_init.add_argument("--project-id", required=True)
    project_init.add_argument("--project-type", required=True)
    project_init.add_argument("--command", action="append", default=[], help="name=command")
    project_init.add_argument("--write-scope", action="append", default=[])
    project_init.add_argument("--provider", action="append", default=[], help="capability=provider")
    project_init.add_argument("--default", action="append", default=[], help="name=workflow-id")
    project_init.add_argument("--force", action="store_true")

    validate = commands.add_parser("validate", help="validate one or more declarations")
    validate.add_argument("paths", nargs="+")

    compile_command = commands.add_parser("compile", help="compile a Workflow declaration")
    compile_command.add_argument("--workflow", required=True)
    compile_command.add_argument("--output")

    explain = commands.add_parser("explain", help="explain a compiled Workflow without running it")
    explain.add_argument("--workflow", required=True)

    compose = commands.add_parser(
        "compose", help="derive a Workflow from a base declaration and extension bindings"
    )
    compose.add_argument("--workflow", required=True, help="base harness-workflow@1 declaration")
    compose.add_argument("--composition", required=True, help="harness-composition@1 overlay")
    compose.add_argument(
        "--skill-contract",
        action="append",
        required=True,
        help="host or extension harness-skill-contract@1 declaration; repeat as needed",
    )
    compose.add_argument("--output", help="optional composed-workflow-plan@1 JSON output")
    compose.add_argument(
        "--derived-workflow-output",
        help="optional standalone derived harness-workflow@1 JSON output",
    )

    commands.add_parser("starter-list", help="list and verify built-in Starter packages")

    starter_verify = commands.add_parser(
        "starter-verify", help="verify one installed Starter package"
    )
    starter_verify.add_argument("--directory", required=True)

    starter_init = commands.add_parser(
        "starter-init", help="install a verified built-in Starter into a new directory"
    )
    starter_init.add_argument("--starter", required=True)
    starter_init.add_argument("--output", required=True)

    starter_register = commands.add_parser(
        "starter-register",
        help="seal a verified installed Starter for exact runtime activation",
    )
    starter_register.add_argument("--project-workspace", required=True)
    starter_register.add_argument(
        "--directory",
        required=True,
        help="installed Starter directory, absolute or relative to project workspace",
    )
    starter_register.add_argument(
        "--output",
        required=True,
        help="new registration JSON, absolute or relative to project workspace",
    )

    host_init = commands.add_parser(
        "host-init",
        help="install and seal one concrete ChatGPT/Codex Host project",
    )
    host_init.add_argument("--project-workspace", required=True)
    host_init.add_argument("--starter", default="customer-agent")
    host_init.add_argument("--test-profile", action="append", default=[])
    host_init.add_argument("--quality-profile", action="append", default=[])
    host_init.add_argument("--process-timeout-seconds", type=int, default=900)
    host_init.add_argument(
        "--github-repository",
        help="optional owner/repository; configured integration requires its token at runtime",
    )
    host_init.add_argument("--github-token-environment-variable", default="GITHUB_TOKEN")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.authoring_command == "project-init":
            if not args.command:
                raise HarnessAuthoringError("project-init requires at least one --command")
            if not args.write_scope:
                raise HarnessAuthoringError("project-init requires at least one --write-scope")
            declaration = initialize_project(
                Path(args.output),
                project_id=args.project_id,
                project_type=args.project_type,
                commands=_pairs(args.command, option="--command"),
                write_scope=args.write_scope,
                providers=_pairs(args.provider, option="--provider"),
                defaults=_pairs(args.default, option="--default"),
                force=args.force,
            )
            print(
                json.dumps(
                    {
                        "schema": "harness-authoring-result@1",
                        "status": "PASS",
                        "output": str(Path(args.output)),
                        "declaration": declaration.as_dict(),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if args.authoring_command == "validate":
            results = []
            for raw_path in args.paths:
                path = Path(raw_path)
                validated = validate_declaration(load_declaration(path))
                results.append(
                    {"path": str(path), "schema": validated["schema"], "status": "PASS"}
                )
            print(
                json.dumps(
                    {"schema": "harness-validation@1", "status": "PASS", "results": results},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if args.authoring_command == "compile":
            plan = compile_workflow_declaration(load_declaration(Path(args.workflow)))
            _write_or_print(plan.as_dict(), args.output)
            return 0
        if args.authoring_command == "explain":
            plan = compile_workflow_declaration(load_declaration(Path(args.workflow)))
            _write_or_print(explain_workflow(plan), None)
            return 0
        if args.authoring_command == "compose":
            plan = compose_workflow(
                load_declaration(Path(args.workflow)),
                load_declaration(Path(args.composition)),
                [load_declaration(Path(path)) for path in args.skill_contract],
            )
            payload = plan.as_dict()
            if args.derived_workflow_output:
                _write_json(payload["derived_workflow"], args.derived_workflow_output)
            _write_or_print(payload, args.output)
            return 0
        if args.authoring_command == "starter-list":
            _write_or_print(
                {
                    "schema": "harness-starter-list@1",
                    "status": "PASS",
                    "starters": list_builtin_starters(registry_workspace=ROOT),
                },
                None,
            )
            return 0
        if args.authoring_command == "starter-verify":
            verification = verify_starter(
                Path(args.directory), registry_workspace=ROOT
            )
            _write_or_print(verification.as_dict(), None)
            return 0
        if args.authoring_command == "starter-init":
            verification = initialize_starter(
                args.starter,
                Path(args.output),
                registry_workspace=ROOT,
            )
            payload = verification.as_dict()
            payload["installed_to"] = str(Path(args.output))
            _write_or_print(payload, None)
            return 0
        if args.authoring_command == "starter-register":
            from starter_runtime import StarterRuntimeError, register_starter_runtime

            project_workspace = Path(args.project_workspace).resolve()
            try:
                payload = register_starter_runtime(
                    project_workspace=project_workspace,
                    starter_directory=_project_path(project_workspace, args.directory),
                    output=_project_path(project_workspace, args.output),
                    registry_workspace=ROOT,
                )
            except StarterRuntimeError as exc:
                raise HarnessAuthoringError(str(exc)) from exc
            _write_or_print(payload, None)
            return 0
        if args.authoring_command == "host-init":
            from project_initializer import (
                ProjectInitializerError,
                initialize_concrete_host_project,
            )

            try:
                payload = initialize_concrete_host_project(
                    project_workspace=Path(args.project_workspace),
                    registry_workspace=ROOT,
                    starter_id=args.starter,
                    test_profiles=args.test_profile or ("test",),
                    quality_profiles=args.quality_profile or ("quality",),
                    process_timeout_seconds=args.process_timeout_seconds,
                    github_repository=args.github_repository,
                    github_token_environment_variable=(
                        args.github_token_environment_variable
                    ),
                )
            except ProjectInitializerError as exc:
                raise HarnessAuthoringError(str(exc)) from exc
            _write_or_print(payload, None)
            return 0
        raise HarnessAuthoringError(f"unsupported authoring command: {args.authoring_command}")
    except (HarnessAuthoringError, OSError) as exc:
        print(
            json.dumps(
                {"schema": "harness-authoring-result@1", "status": "FAIL", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
