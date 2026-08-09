#!/usr/bin/env python3
"""Run real Chromium desktop/mobile journeys against isolated product APIs."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from verify_full_lifecycle_canary import (
    AGENT_PYTHON,
    ProductRuntimeEnvironmentBlocked,
    ProductRuntimeHarness,
    find_free_port,
    terminate_process,
    wait_http,
)


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "services/agent-service/frontend"
AGENT_ROOT = ROOT / "services/agent-service"
AGENT_SRC = AGENT_ROOT / "src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from agent_core.kernel.plan_projection_contract import read_plan_projection  # noqa: E402
from agent_core.persistence.database_settings import DatabaseSettings  # noqa: E402
from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider  # noqa: E402

_ENVIRONMENTAL_MODEL_FAILURE_CATEGORIES = frozenset({
    "http_401", "http_402", "http_403", "http_429", "timeout", "connection",
})


def is_environmental_model_failure_category(category: str) -> bool:
    return str(category or "") in _ENVIRONMENTAL_MODEL_FAILURE_CATEGORIES

JOURNEYS = {
    "product": FRONTEND / "e2e/product_journey.mjs",
    "strong-context": FRONTEND / "e2e/strong_context_journey.mjs",
    "strong-context-campaign": FRONTEND / "e2e/strong_context_campaign_journey.mjs",
}


class BrowserRuntimeEnvironmentBlocked(RuntimeError):
    """The browser runtime is missing or controlled independently of product code."""

    def __init__(self, reason: str, diagnostics: dict[str, Any]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics


class ConfiguredModelEnvironmentBlocked(RuntimeError):
    """A configured provider is unavailable independently of product code."""

    def __init__(self, diagnostics: dict[str, Any]) -> None:
        super().__init__("configured model environment is unavailable")
        self.diagnostics = diagnostics


def _safe_model_failure(state: dict[str, Any]) -> dict[str, Any] | None:
    tool_error = state.get("tool_error") if isinstance(state.get("tool_error"), dict) else {}
    replay = tool_error.get("replay") if isinstance(tool_error.get("replay"), dict) else {}
    error = replay.get("error") if isinstance(replay.get("error"), dict) else {}
    category = str(error.get("category") or "")
    if not category:
        return None
    return {
        "type": str(error.get("type") or "UnknownError"),
        "category": category,
        "fingerprint": str(replay.get("fingerprint") or ""),
    }


def _project_graph_diagnostic_rows(rows: list[tuple[Any, Any]]) -> list[dict[str, Any]]:
    """Project graph snapshots to a bounded, secret-free browser diagnostic."""

    diagnostics: list[dict[str, Any]] = []
    for thread_id, raw in rows:
        try:
            payload = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        state = payload.get("state") if isinstance(payload.get("state"), dict) else payload
        if not isinstance(state, dict):
            continue
        tool_trace = [row for row in list(state.get("tool_trace") or []) if isinstance(row, dict)]
        declared_args = next((
            dict(row.get("args") or {}) for row in tool_trace
            if str(row.get("name") or "") == "declare_turn_goals"
            and isinstance(row.get("args"), dict)
        ), {})
        workflow = read_plan_projection(state) or {}
        diagnostics.append({
            "thread_id": str(thread_id or ""),
            "turn": state.get("turn_index"),
            "input": str(state.get("current_user_input") or "")[:300],
            "status": state.get("status"),
            "model_failure": _safe_model_failure(state),
            "final_answer": str(state.get("current_final_answer") or "")[:300],
            "tools": [
                {
                    "name": row.get("name"),
                    "code": (row.get("result") or {}).get("code"),
                    "ok": (row.get("result") or {}).get("ok"),
                    "args": {
                        key: (row.get("args") or {}).get(key)
                        for key in (
                            "target", "query", "constraint_bindings", "reference_span",
                            "status_span", "question_span", "goal_ids",
                        )
                        if key in (row.get("args") or {})
                    },
                    "permit_code": ((row.get("result") or {}).get("match_proof") or {}).get("reason_code"),
                }
                for row in tool_trace
            ],
            "answer_release_alignment": state.get("answer_release_alignment"),
            "presentation_contract_violations": state.get("presentation_contract_violations"),
            "plan_projection": {
                "status": workflow.get("status"),
                "goal_coverage_complete": workflow.get("goal_coverage_complete"),
                "goals": list(workflow.get("goals") or []),
            },
            "clarification": {
                "pending": state.get("pending_clarification"),
                "resolution": declared_args.get("clarification_resolution"),
            },
            "capability_surface": state.get("capability_surface"),
            "workflow_status": workflow.get("status"),
            "model_iterations": [
                {
                    "loop_step": call.get("loop_step"),
                    "tool_names": call.get("tool_names"),
                    "response_content": str(call.get("response_content") or "")[:200],
                }
                for call in list(state.get("debug_llm_calls") or [])
                if isinstance(call, dict)
            ],
            "model_call_trace": [
                {
                    key: call.get(key)
                    for key in (
                        "purpose", "model", "sequence", "lane", "status", "latency_ms",
                        "prompt_tokens", "completion_tokens", "total_tokens",
                        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "prompt_cache_hit_rate",
                    )
                    if call.get(key) is not None
                }
                for call in list(state.get("model_call_trace") or [])
                if isinstance(call, dict)
            ],
            "context": {
                "recent": [
                    {"role": row.get("role"), "content": str(row.get("content") or "")[:160]}
                    for row in list((state.get("context_bundle") or {}).get("recent_conversation_window") or [])
                    if isinstance(row, dict)
                ],
                "visible_refs": [
                    {
                        "source_turn": row.get("source_turn"),
                        "shape": row.get("shape"),
                        "member_labels": row.get("member_labels"),
                    }
                    for row in list((state.get("context_bundle") or {}).get("visible_result_refs") or [])
                    if isinstance(row, dict)
                ],
            },
        })
    return diagnostics


def _graph_diagnostics(database: Path, *, limit: int = 4) -> list[dict[str, Any]]:
    if not database.is_file():
        return []
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute(
            "SELECT thread_id, output_json FROM trace_logs "
            "WHERE event_type='graph_snapshot' ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return _project_graph_diagnostic_rows(rows)


def _postgres_graph_diagnostics(database_url: str, *, limit: int = 4) -> list[dict[str, Any]]:
    """Read protected traces through the same repository authority as Runtime."""

    provider = None
    try:
        provider = build_sqlalchemy_store_provider(DatabaseSettings(
            backend="postgres",
            database_url=str(database_url or "").strip(),
            sqlite_path=AGENT_ROOT / "runtime/sqlite/app.db",
            create_schema=False,
        ))
        records = provider.traces.list_recent_by_event_type(
            "graph_snapshot", max(1, min(int(limit), 500))
        )
        rows = [(row.get("thread_id"), row.get("output_json")) for row in records]
    except Exception as exc:
        # Do not expose exception text because database errors may echo credentials.
        return [{"diagnostic_status": "unavailable", "error_type": exc.__class__.__name__}]
    finally:
        if provider is not None:
            provider.close()
    return _project_graph_diagnostic_rows(rows)

def _configured_model_preflight(env: dict[str, str]) -> dict[str, Any]:
    """Fail fast before an expensive browser journey when the provider is down."""
    completed = subprocess.run(
        [str(AGENT_PYTHON), "-B", "scripts/verify_model_smoke.py"],
        cwd=AGENT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    try:
        payload = json.loads((completed.stdout or "").strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        payload = {
            "status": "FAIL",
            "error_type": "InvalidSmokeEvidence",
            "error_category": "unclassified",
        }
    if completed.returncode == 78:
        raise ConfiguredModelEnvironmentBlocked({"phase": "preflight", "model_smoke": payload})
    if completed.returncode != 0:
        raise RuntimeError({
            "configured_model_preflight": payload,
            "stderr": (completed.stderr or "")[-2000:],
        })
    return payload


def _environmental_failure(diagnostics: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in reversed(diagnostics):
        failure = row.get("model_failure") if isinstance(row.get("model_failure"), dict) else {}
        if is_environmental_model_failure_category(str(failure.get("category") or "")):
            return {"turn": row.get("turn"), "status": row.get("status"), **failure}
    return None


def _playwright_browser_executable() -> Path | None:
    """Return Playwright's lock-matched browser when it is already installed."""
    try:
        completed = subprocess.run(
            [
                str(_node_binary()),
                "-e",
                "const { chromium } = require('playwright'); process.stdout.write(chromium.executablePath())",
            ],
            cwd=FRONTEND,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    candidate = Path((completed.stdout or "").strip()).expanduser()
    return candidate.resolve() if candidate.is_file() else None


def _browser_executable() -> Path:
    configured = str(
        os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        or os.getenv("CHROMIUM_EXECUTABLE_PATH")
        or ""
    ).strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        raise BrowserRuntimeEnvironmentBlocked(
            "declared_browser_missing",
            {"configured_path": str(candidate)},
        )

    bundled = _playwright_browser_executable()
    if bundled is not None:
        return bundled

    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"):
        discovered = shutil.which(name)
        if discovered and Path(discovered).is_file():
            return Path(discovered).resolve()
    raise BrowserRuntimeEnvironmentBlocked(
        "browser_executable_missing",
        {
            "configured_path": None,
            "playwright_browser_installed": False,
            "system_browser_found": False,
        },
    )


def _browser_environment_failure(stdout: str, stderr: str) -> dict[str, Any] | None:
    combined = f"{stdout}\n{stderr}"
    if "ERR_BLOCKED_BY_ADMINISTRATOR" in combined:
        return {
            "reason": "browser_managed_policy_blocked_local_runtime",
            "signal": "ERR_BLOCKED_BY_ADMINISTRATOR",
        }
    if "Executable doesn't exist at" in combined or "Please run the following command to download new browsers" in combined:
        return {
            "reason": "playwright_browser_not_installed",
            "signal": "playwright_executable_missing",
        }
    return None


def _node_binary() -> Path:
    configured = os.getenv("NODE_BINARY", "").strip()
    candidates = [Path(configured)] if configured else []
    discovered = shutil.which("node")
    if discovered:
        candidates.append(Path(discovered))
    candidates.extend(sorted((ROOT / ".quality/tools").glob("node-*/bin/node"), reverse=True))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("Node.js runtime is missing; run the locked workspace bootstrap")


def _start_vite(*, harness: ProductRuntimeHarness, port: int) -> tuple[subprocess.Popen[Any], Any]:
    node = _node_binary()
    vite = FRONTEND / "node_modules/vite/bin/vite.js"
    if not vite.is_file():
        raise RuntimeError("Vite dependency is missing")
    log = (harness.runtime_dir / "vite.log").open("w", encoding="utf-8")
    env = dict(harness.env)
    env["VITE_AGENT_DEV_TARGET"] = harness.agent_url
    process = subprocess.Popen(
        [str(node), str(vite), "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=FRONTEND,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    return process, log


def _protected_runtime_preflight(env: dict[str, str]) -> None:
    provider = str(env.get("EMBEDDING_PROVIDER") or "").strip().lower()
    if provider not in {"openai", "openai_compatible", "http", "local_http"}:
        raise BrowserRuntimeEnvironmentBlocked(
            "protected_embedding_provider_unavailable",
            {"phase": "protected_runtime_preflight", "required": "openai-compatible or http embedding provider"},
        )
    if provider in {"openai", "openai_compatible"}:
        key = str(env.get("EMBEDDING_API_KEY") or "").strip()
        if not key:
            raise BrowserRuntimeEnvironmentBlocked(
                "protected_embedding_credentials_missing",
                {"phase": "protected_runtime_preflight", "required": "EMBEDDING_API_KEY"},
            )
        if not str(env.get("EMBEDDING_MODEL") or "").strip():
            raise BrowserRuntimeEnvironmentBlocked(
                "protected_embedding_model_missing",
                {"phase": "protected_runtime_preflight", "required": "EMBEDDING_MODEL"},
            )
    if provider in {"http", "local_http"} and not str(env.get("EMBEDDING_BASE_URL") or "").strip():
        raise BrowserRuntimeEnvironmentBlocked(
            "protected_embedding_endpoint_missing",
            {"phase": "protected_runtime_preflight", "required": "EMBEDDING_BASE_URL"},
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journey", choices=tuple(JOURNEYS), default="product")
    parser.add_argument("--model-mode", choices=("deterministic", "configured"), default="deterministic")
    parser.add_argument(
        "--runtime-profile",
        choices=("local", "protected-preprod"),
        default=os.getenv("PRODUCT_BROWSER_RUNTIME_PROFILE", "local"),
    )
    parser.add_argument("--campaign-seed", type=int, default=20260715)
    parser.add_argument("--campaign-phase", choices=("repair-retest", "unseen"), default="repair-retest")
    parser.add_argument("--campaign-min-turn-pass-rate", type=float)
    parser.add_argument("--campaign-min-scenarios-at-eight", type=int)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    journey = JOURNEYS[args.journey]
    deterministic_model = args.model_mode == "deterministic"
    protected_preprod = args.runtime_profile == "protected-preprod"
    vite_process: subprocess.Popen[Any] | None = None
    vite_log: Any | None = None
    try:
        if not journey.is_file():
            raise RuntimeError(f"browser journey is missing: {journey}")
        web_port = find_free_port()
        web_origin = f"http://127.0.0.1:{web_port}"
        web_url = f"{web_origin}/web/"
        persistence_url = str(os.getenv("PRODUCT_BROWSER_POSTGRES_URL") or "").strip() or None
        database_fingerprint = str(
            os.getenv("PRODUCT_BROWSER_DATABASE_INSTANCE_FINGERPRINT_SHA256_16") or ""
        ).strip().lower()
        if protected_preprod:
            if not persistence_url:
                raise BrowserRuntimeEnvironmentBlocked(
                    "protected_postgres_runtime_missing",
                    {"phase": "protected_runtime_preflight", "required": "PRODUCT_BROWSER_POSTGRES_URL"},
                )
            if len(database_fingerprint) != 16 or any(ch not in "0123456789abcdef" for ch in database_fingerprint):
                raise BrowserRuntimeEnvironmentBlocked(
                    "protected_postgres_identity_missing",
                    {
                        "phase": "protected_runtime_preflight",
                        "required": "PRODUCT_BROWSER_DATABASE_INSTANCE_FINGERPRINT_SHA256_16",
                    },
                )
            _protected_runtime_preflight(dict(os.environ))

        with ProductRuntimeHarness(
            deterministic_model=deterministic_model,
            persistence_url=persistence_url,
            protected_preprod=protected_preprod,
            allowed_origins=web_origin if protected_preprod else None,
        ) as harness:
            model_preflight = None
            if not deterministic_model:
                model_preflight = _configured_model_preflight(harness.env)
            vite_process, vite_log = _start_vite(harness=harness, port=web_port)
            wait_http(web_url)
            artifact_dir = Path(
                os.getenv("PRODUCT_BROWSER_ARTIFACT_DIR")
                or harness.runtime_dir / "browser-artifacts"
            ).resolve()
            artifact_dir.mkdir(parents=True, exist_ok=True)
            env = dict(harness.env)
            env.update({
                "PRODUCT_WEB_URL": web_url,
                "PRODUCT_BROWSER_ARTIFACT_DIR": str(artifact_dir),
                "PRODUCT_BROWSER_MODEL_MODE": args.model_mode,
                "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH": str(_browser_executable()),
            })
            if protected_preprod:
                env["PRODUCT_BROWSER_AUTH_TOKEN"] = harness.browser_auth_token
            if args.journey == "strong-context-campaign":
                default_rate = 0.75 if args.campaign_phase == "unseen" else 0.80
                default_scenarios = 15 if args.campaign_phase == "unseen" else 16
                env.update({
                    "PRODUCT_BROWSER_CAMPAIGN_SEED": str(args.campaign_seed),
                    "PRODUCT_BROWSER_CAMPAIGN_PHASE": args.campaign_phase,
                    "PRODUCT_BROWSER_CAMPAIGN_MIN_TURN_PASS_RATE": str(
                        args.campaign_min_turn_pass_rate
                        if args.campaign_min_turn_pass_rate is not None else default_rate
                    ),
                    "PRODUCT_BROWSER_CAMPAIGN_MIN_SCENARIOS_AT_EIGHT": str(
                        args.campaign_min_scenarios_at_eight
                        if args.campaign_min_scenarios_at_eight is not None else default_scenarios
                    ),
                })
            timeout = 7200 if args.journey == "strong-context-campaign" else (720 if not deterministic_model else 150)
            result = subprocess.run(
                [str(_node_binary()), str(journey)],
                cwd=FRONTEND,
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            diagnostic_limit = 500 if args.journey == "strong-context-campaign" else 4
            graph_diagnostics = (
                _postgres_graph_diagnostics(persistence_url, limit=diagnostic_limit)
                if protected_preprod and persistence_url
                else _graph_diagnostics(harness.runtime_dir / "agent.db", limit=diagnostic_limit)
            )
            if args.journey == "strong-context-campaign":
                (artifact_dir / f"strong-context-campaign-{args.campaign_seed}-{args.campaign_phase}-runtime.json").write_text(
                    json.dumps(graph_diagnostics, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            if result.returncode != 0:
                if vite_log is not None:
                    vite_log.flush()
                browser_failure = _browser_environment_failure(result.stdout, result.stderr)
                if browser_failure is not None:
                    raise BrowserRuntimeEnvironmentBlocked(
                        str(browser_failure["reason"]),
                        {
                            "phase": "browser_journey",
                            **browser_failure,
                            "browser_executable": env.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"),
                        },
                    )
                provider_failure = _environmental_failure(graph_diagnostics)
                if provider_failure is not None:
                    raise ConfiguredModelEnvironmentBlocked({
                        "phase": "browser_journey",
                        "provider_failure": provider_failure,
                        "model_preflight": model_preflight,
                    })
                raise RuntimeError({
                    "journey_stdout": result.stdout[-6000:],
                    "journey_stderr": result.stderr[-6000:],
                    "graph_diagnostics": graph_diagnostics,
                    "service_logs": harness.diagnostic_tails(),
                })
            if result.stdout.strip():
                print(result.stdout.strip())
            evidence = {
                "contract": "protected-browser-journey-runtime@1" if protected_preprod else "browser-journey-runtime@1",
                "status": "PASS",
                "journey": args.journey,
                "model_mode": args.model_mode,
                "runtime_authority": harness.runtime_authority_evidence(),
                "database_instance_fingerprint_sha256_16": database_fingerprint if protected_preprod else None,
                "e2e_stdout_sha256_16": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()[:16],
            }
            print(json.dumps(evidence, ensure_ascii=False))
            return 0
    except (BrowserRuntimeEnvironmentBlocked, ProductRuntimeEnvironmentBlocked) as exc:
        print(json.dumps({
            "status": "BLOCKED_BY_ENVIRONMENT",
            "reason": exc.reason,
            "diagnostics": exc.diagnostics,
        }, ensure_ascii=False))
        return 78
    except ConfiguredModelEnvironmentBlocked as exc:
        print(json.dumps({
            "status": "BLOCKED_BY_ENVIRONMENT",
            "reason": "configured_model_environment_unavailable",
            "diagnostics": exc.diagnostics,
        }, ensure_ascii=False))
        return 78
    except Exception as exc:
        print(json.dumps({
            "status": "FAIL",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }, ensure_ascii=False))
        return 1
    finally:
        terminate_process(vite_process)
        if vite_log is not None:
            vite_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
