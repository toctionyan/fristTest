import os
import sqlite3
from functools import lru_cache
from pathlib import Path

from agent_core.runtime.turn_fencing import AtomicallyFencedPostgresSaver, FencedCheckpointer
from agent_core.runtime.profile import (
    RuntimeProfile,
    get_runtime_profile,
    get_runtime_profile_diagnostics,
    is_local_profile,
    require_runtime_profile,
    resolve_verifier_mode,
)

try:
    from dotenv import load_dotenv
except Exception:  # 允许无依赖环境做静态检查/单元测试

    def load_dotenv(*args, **kwargs):  # type: ignore
        return False


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-4:]


def _bounded_float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number between {minimum} and {maximum}") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer between {minimum} and {maximum}") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def get_model_settings() -> dict[str, object]:
    """Return the complete, non-secret OpenAI-compatible model configuration.

    The settings are intentionally read from ``.env`` rather than hard-coded in
    ``get_model``.  A model change must be reproducible from deployment
    configuration and visible in the model profile without exposing credentials.
    """
    return {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "base_url": os.getenv("OPENAI_API_BASE") or None,
        "temperature": _bounded_float_env("MODEL_TEMPERATURE", 0.0, minimum=0.0, maximum=2.0),
        "timeout_seconds": _bounded_float_env("MODEL_TIMEOUT_SECONDS", 60.0, minimum=1.0, maximum=600.0),
        "max_retries": _bounded_int_env("MODEL_MAX_RETRIES", 2, minimum=0, maximum=10),
    }


def get_model_profile() -> dict[str, object]:
    """Return a non-secret, replayable model identity for debug/audit records.

    PromptProfile identifies the behavioural instruction.  This profile records
    the provider/model settings that can materially change how that instruction
    behaves, without exposing API keys or arbitrary endpoint credentials.
    """
    settings = get_model_settings()
    return {
        "provider": "openai_compatible",
        "model": settings["model"],
        "base_url_configured": bool(settings["base_url"]),
        "temperature": settings["temperature"],
        "timeout_seconds": settings["timeout_seconds"],
        "max_retries": settings["max_retries"],
        "structured_output": "continuous_agent_loop_with_grounded_observations_and_action_gateway",
    }


def get_runtime_config_diagnostics(mask_secrets: bool = True) -> dict[str, object]:
    api_key = os.getenv("OPENAI_API_KEY")
    business_token = os.getenv("BUSINESS_SERVICE_TOKEN")
    try:
        model_settings = get_model_settings()
        model_config_error: str | None = None
    except RuntimeError as exc:
        model_settings = {}
        model_config_error = str(exc)

    budget_names = {
        "total": ("MODEL_CALL_MAX_PER_TURN", 18),
        "planner": ("MODEL_CALL_MAX_PLANNER_PER_TURN", 8),
        "verifier": ("MODEL_CALL_MAX_VERIFIER_PER_TURN", 8),
        "support": ("MODEL_CALL_MAX_SUPPORT_PER_TURN", 2),
    }
    model_call_budget: dict[str, int] = {}
    model_call_budget_error: str | None = None
    try:
        for lane, (name, default) in budget_names.items():
            value = int((os.getenv(name) or str(default)).strip())
            if value < 0 or (lane == "total" and value < 1):
                raise ValueError(name)
            model_call_budget[lane] = value
        if model_call_budget["total"] != sum(model_call_budget[lane] for lane in ("planner", "verifier", "support")):
            raise ValueError("MODEL_CALL_MAX_PER_TURN must equal planner + verifier + support")
    except (TypeError, ValueError) as exc:
        model_call_budget_error = str(exc) or "model call budgets must be non-negative integers"

    data = {
        "env_path": str(ENV_PATH),
        "env_file_exists": ENV_PATH.exists(),
        "openai_api_key_present": bool(api_key),
        "openai_api_key": mask_secret(api_key) if mask_secrets else api_key,
        "openai_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "openai_api_base": os.getenv("OPENAI_API_BASE") or None,
        "model_temperature": model_settings.get("temperature"),
        "model_timeout_seconds": model_settings.get("timeout_seconds"),
        "model_max_retries": model_settings.get("max_retries"),
        "model_config_error": model_config_error,
        "model_call_budget": model_call_budget,
        "model_call_budget_error": model_call_budget_error,
        "business_service_base_url": os.getenv(
            "BUSINESS_SERVICE_BASE_URL", "http://127.0.0.1:9000"
        ),
        "business_service_token_present": bool(business_token),
        "business_service_token": mask_secret(business_token)
        if mask_secrets
        else business_token,
        "agent_db_backend": os.getenv("AGENT_DB_BACKEND")
        or os.getenv("DATABASE_BACKEND")
        or "sqlite",
        "agent_database_url_present": bool(
            os.getenv("AGENT_DATABASE_URL") or os.getenv("DATABASE_URL")
        ),
        "checkpoint_backend": os.getenv("CHECKPOINT_BACKEND", "sqlite"),
        "strict_persistence": os.getenv("STRICT_PERSISTENCE", "false"),
        "rag_backend": os.getenv("RAG_BACKEND", "local_sparse"),
        "embedding_provider": os.getenv("EMBEDDING_PROVIDER", "local_sparse"),
        "embedding_model": os.getenv("EMBEDDING_MODEL") or None,
        "embedding_api_key_present": bool(os.getenv("EMBEDDING_API_KEY")),
        "embedding_api_base": os.getenv("EMBEDDING_API_BASE") or None,
        "rag_collection": os.getenv("RAG_COLLECTION", "agent_knowledge"),
        "action_config_dir": str(
            project_path(os.getenv("ACTION_CONFIG_DIR"), "action_configs")
        ),
        "action_extension_modules": [
            item.strip()
            for item in os.getenv("AGENT_ACTION_EXTENSION_MODULES", "").split(",")
            if item.strip()
        ],
        "agent_loop_profile": {
            "contract": "continuous-agent-loop@15",
            "context": "verified-observations+task-board+action-gateway",
        },
        "capability_semantic_verifier_mode": os.getenv("CAPABILITY_SEMANTIC_VERIFIER_MODE", "auto"),
        "goal_alignment_verifier_mode": os.getenv("GOAL_ALIGNMENT_VERIFIER_MODE", "auto"),
        "answer_release_alignment_verifier_mode": os.getenv("ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE", "auto"),
        "agent_require_auth": os.getenv("AGENT_REQUIRE_AUTH", "true" if not is_local_dev() else "false"),
        "agent_auth_provider": os.getenv("AGENT_AUTH_PROVIDER", "remote_userinfo" if not is_local_dev() else "dev_headers"),
        "local_dev": is_local_dev(),
        "agent_auth_userinfo_url": os.getenv("AGENT_AUTH_USERINFO_URL") or None,
        "agent_jwt_secret_present": bool(os.getenv("AGENT_JWT_SECRET")),
    }
    missing = []
    if not api_key:
        missing.append("OPENAI_API_KEY")
    data["missing_required"] = missing
    data["ok"] = not missing and not model_config_error and not model_call_budget_error
    return data


def check_runtime_config(strict: bool = False) -> dict[str, object]:
    diagnostics = get_runtime_config_diagnostics(mask_secrets=True)
    problems: list[str] = []
    if diagnostics.get("missing_required"):
        problems.append("缺少必要环境变量：" + ", ".join(diagnostics["missing_required"]))
    if diagnostics.get("model_config_error"):
        problems.append(str(diagnostics["model_config_error"]))
    if diagnostics.get("model_call_budget_error"):
        problems.append(str(diagnostics["model_call_budget_error"]))
    if problems:
        template = PROJECT_ROOT / ".env.example"
        message = (
            "; ".join(problems)
            + f"。请先复制 {template} 为 {ENV_PATH}，再填写模型与运行配置。"
        )
        if strict:
            raise RuntimeError(message)
        print(f"[WARN] {message}")
    return diagnostics


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def get_runtime_profile_name(*, strict: bool = False) -> str | None:
    profile = get_runtime_profile(strict=strict)
    return profile.value if profile is not None else None


def is_local_dev() -> bool:
    """Return whether the explicitly declared profile is local."""
    return is_local_profile()


def is_production_env() -> bool:
    profile = get_runtime_profile(strict=False)
    return profile in {RuntimeProfile.PREPROD, RuntimeProfile.PRODUCTION}


def validate_production_security() -> None:
    """Fail closed outside explicit APP_PROFILE=local mode."""
    profile = get_runtime_profile(strict=True)
    if profile is RuntimeProfile.LOCAL:
        return
    errors: list[str] = []
    if not _truthy(os.getenv("AGENT_REQUIRE_AUTH", "false")):
        errors.append("AGENT_REQUIRE_AUTH must be true")
    provider = os.getenv("AGENT_AUTH_PROVIDER", "remote_userinfo").strip().lower()
    if provider in {"dev_headers", "headers", "mock", "dev_token", "local_token"}:
        errors.append(
            "AGENT_AUTH_PROVIDER must be remote_userinfo or jwt_hs256 in production"
        )
    if (
        provider in {"remote", "remote_userinfo", "business", "ruoyi"}
        and not os.getenv("AGENT_AUTH_USERINFO_URL", "").strip()
    ):
        errors.append(
            "AGENT_AUTH_USERINFO_URL is required for remote_userinfo authentication"
        )
    if provider in {"jwt", "jwt_hs256", "hs256"}:
        secret = os.getenv("AGENT_JWT_SECRET", "")
        if (
            not secret
            or len(secret) < 32
            or "dev" in secret.lower()
            or "change" in secret.lower()
        ):
            errors.append("AGENT_JWT_SECRET must be a strong non-default secret")
    business_token = os.getenv("BUSINESS_SERVICE_TOKEN", "")
    if (
        not business_token
        or business_token == "dev-service-token"
        or len(business_token) < 24
    ):
        errors.append("BUSINESS_SERVICE_TOKEN must be a strong non-default token")
    if not _truthy(os.getenv("BUSINESS_REQUIRE_ACTOR_SIGNATURE", "false")):
        errors.append("BUSINESS_REQUIRE_ACTOR_SIGNATURE must be true in preprod/production")
    actor_secret = os.getenv("BUSINESS_ACTOR_SIGNING_SECRET", "")
    if (
        not actor_secret
        or len(actor_secret) < 32
        or "dev" in actor_secret.lower()
        or "change" in actor_secret.lower()
    ):
        errors.append("BUSINESS_ACTOR_SIGNING_SECRET must be a strong non-default secret")
    if os.getenv("AGENT_ALLOWED_ORIGINS", "").strip() in {"*", ""}:
        errors.append("AGENT_ALLOWED_ORIGINS must be an explicit allow-list")
    for verifier_env in (
        "CAPABILITY_SEMANTIC_VERIFIER_MODE",
        "GOAL_ALIGNMENT_VERIFIER_MODE",
        "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE",
    ):
        try:
            resolved = resolve_verifier_mode(verifier_env)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        if resolved != "model":
            errors.append(f"{verifier_env} must resolve to model in preprod/production")
    checkpoint_backend = os.getenv("CHECKPOINT_BACKEND", "sqlite").lower()
    if checkpoint_backend == "memory":
        errors.append("CHECKPOINT_BACKEND=memory is not allowed in production")
    if checkpoint_backend not in {"postgres", "postgresql"}:
        errors.append("CHECKPOINT_BACKEND must be postgres in preprod/production")
    if not _truthy(os.getenv("STRICT_PERSISTENCE", "false")):
        errors.append("STRICT_PERSISTENCE must be true in production")
    if os.getenv("STATE_CONTRACT_MODE", "audit").strip().lower() != "strict":
        errors.append("STATE_CONTRACT_MODE=strict is required in production")
    if os.getenv("TRACE_REDACTION_MODE", "standard").strip().lower() != "standard":
        errors.append("TRACE_REDACTION_MODE=standard is required in production")
    try:
        if int(os.getenv("TRACE_RETENTION_DAYS", "30")) <= 0:
            errors.append("TRACE_RETENTION_DAYS must be a positive integer")
    except ValueError:
        errors.append("TRACE_RETENTION_DAYS must be a positive integer")
    db_backend = (
        os.getenv("AGENT_DB_BACKEND") or os.getenv("DATABASE_BACKEND") or "sqlite"
    ).lower()
    if db_backend in {"sqlite", "local"}:
        errors.append("AGENT_DB_BACKEND must be postgres/sqlalchemy/mysql in preprod/production")
    rag_backend = (os.getenv("RAG_BACKEND") or "local_sparse").lower()
    if rag_backend in {"local", "local_sparse", "sqlite", "sparse"}:
        errors.append("RAG_BACKEND must be pgvector or qdrant in preprod/production")
    document_job_backend = (os.getenv("DOCUMENT_JOB_BACKEND") or "sqlalchemy").strip().lower()
    if document_job_backend not in {"sqlalchemy", "postgres", "postgresql"}:
        errors.append("DOCUMENT_JOB_BACKEND must use the shared database in preprod/production")
    document_job_url = (
        os.getenv("DOCUMENT_JOB_DATABASE_URL")
        or os.getenv("AGENT_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or ""
    ).strip()
    if not document_job_url.lower().startswith(("postgresql://", "postgresql+")):
        errors.append("DOCUMENT_JOB_DATABASE_URL must use PostgreSQL in preprod/production")
    object_backend = (os.getenv("DOCUMENT_OBJECT_STORE_BACKEND") or "").strip().lower()
    if object_backend not in {"shared", "shared_filesystem", "s3"}:
        errors.append("DOCUMENT_OBJECT_STORE_BACKEND must be shared_filesystem or s3 in preprod/production")
    if object_backend in {"shared", "shared_filesystem"}:
        shared_root = (os.getenv("DOCUMENT_OBJECT_STORE_ROOT") or "").strip()
        if not shared_root or not Path(shared_root).is_absolute():
            errors.append("DOCUMENT_OBJECT_STORE_ROOT must be an absolute shared mount path")
    if object_backend == "s3" and not (os.getenv("DOCUMENT_S3_BUCKET") or "").strip():
        errors.append("DOCUMENT_S3_BUCKET is required when DOCUMENT_OBJECT_STORE_BACKEND=s3")
    try:
        lock_ttl = int(os.getenv("CONVERSATION_LOCK_TTL_SECONDS", "300"))
        if lock_ttl < 30 or lock_ttl > 3600:
            errors.append("CONVERSATION_LOCK_TTL_SECONDS must be between 30 and 3600")
    except ValueError:
        errors.append("CONVERSATION_LOCK_TTL_SECONDS must be an integer")
    if errors:
        raise RuntimeError(
            "Unsafe preprod/production Agent configuration: " + "; ".join(errors)
        )


def project_path(value: str | None, default: str) -> Path:
    raw = value or default
    p = Path(raw)
    if p.is_absolute():
        return p
    return PROJECT_ROOT / p


@lru_cache(maxsize=1)
def get_model():
    try:
        from langchain_openai import ChatOpenAI
    except Exception as e:
        raise RuntimeError(
            "缺少 langchain_openai，请先安装 requirements.txt 后再启动 Agent 服务。"
        ) from e

    api_key = os.getenv("OPENAI_API_KEY")
    settings = get_model_settings()

    if not api_key:
        template = PROJECT_ROOT / ".env.example"
        raise RuntimeError(
            f"""
没有找到 OPENAI_API_KEY。
请先复制配置模板：
cp {template} {ENV_PATH}
然后填写：
OPENAI_API_KEY=你的key
OPENAI_MODEL=gpt-4o-mini
OPENAI_API_BASE=
"""
        )

    return ChatOpenAI(
        model=str(settings["model"]),
        api_key=api_key,
        base_url=settings["base_url"],
        temperature=float(settings["temperature"]),
        timeout=float(settings["timeout_seconds"]),
        max_retries=int(settings["max_retries"]),
    )


@lru_cache(maxsize=1)
def get_storage_paths() -> dict[str, Path]:
    paths = {
        "sqlite_db": project_path(os.getenv("SQLITE_DB_PATH"), "runtime/sqlite/app.db"),
        "vector_db": project_path(
            os.getenv("VECTOR_DB_PATH"), "runtime/vector-store/vector_store.db"
        ),
        "uploads": project_path(os.getenv("UPLOAD_DIR"), "runtime/uploads"),
        "checkpoint_db": project_path(
            os.getenv("CHECKPOINT_DB_PATH"), "runtime/sqlite/checkpoints.db"
        ),
        "logs": project_path(None, "runtime/logs"),
    }
    for p in paths.values():
        if p.suffix:
            p.parent.mkdir(parents=True, exist_ok=True)
        else:
            p.mkdir(parents=True, exist_ok=True)
    return paths


_CHECKPOINTER_CACHE: object | None = None
_CHECKPOINTER_CONNECTION: object | None = None
_CHECKPOINTER_CONTEXT: object | None = None


def _close_checkpointer_resource(value: object | None) -> None:
    """Best-effort close for the sqlite checkpointer and its owned connection."""
    global _CHECKPOINTER_CONNECTION, _CHECKPOINTER_CONTEXT
    if _CHECKPOINTER_CONTEXT is not None:
        exit_fn = getattr(_CHECKPOINTER_CONTEXT, "__exit__", None)
        if callable(exit_fn):
            try:
                exit_fn(None, None, None)
            except Exception:
                pass
        _CHECKPOINTER_CONTEXT = None
    candidates: list[object] = []
    if value is not None:
        candidates.append(value)
        connection = getattr(value, "conn", None)
        if connection is not None:
            candidates.append(connection)
    if _CHECKPOINTER_CONNECTION is not None:
        candidates.append(_CHECKPOINTER_CONNECTION)

    seen: set[int] = set()
    for candidate in candidates:
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        close = getattr(candidate, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Cache cleanup must not replace a business outcome with a close error.
                pass


def clear_checkpointer_cache() -> None:
    """Close the cached checkpoint resource before discarding it.

    A bare ``lru_cache.cache_clear()`` drops the Python reference to
    ``SqliteSaver`` but leaves its sqlite3 connection open.  Tests rebuild graphs
    frequently and production shutdowns may do the same during a reload, so the
    cache must own and close that resource explicitly.
    """
    global _CHECKPOINTER_CACHE, _CHECKPOINTER_CONNECTION, _CHECKPOINTER_CONTEXT
    _close_checkpointer_resource(_CHECKPOINTER_CACHE)
    _CHECKPOINTER_CACHE = None
    _CHECKPOINTER_CONNECTION = None
    _CHECKPOINTER_CONTEXT = None


def build_checkpointer():
    global _CHECKPOINTER_CACHE, _CHECKPOINTER_CONNECTION, _CHECKPOINTER_CONTEXT
    if _CHECKPOINTER_CACHE is not None:
        return _CHECKPOINTER_CACHE

    backend = os.getenv("CHECKPOINT_BACKEND", "sqlite").lower()
    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except Exception as e:
        raise RuntimeError(
            "缺少 langgraph，请先安装 requirements.txt 后再启动 Agent 服务。"
        ) from e

    if backend == "memory":
        _CHECKPOINTER_CACHE = FencedCheckpointer(InMemorySaver())
        _CHECKPOINTER_CONNECTION = None
        return _CHECKPOINTER_CACHE

    if backend in {"postgres", "postgresql"}:
        database_url = (
            os.getenv("CHECKPOINT_DATABASE_URL")
            or os.getenv("AGENT_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or ""
        ).strip()
        if not database_url:
            raise RuntimeError("CHECKPOINT_BACKEND=postgres requires CHECKPOINT_DATABASE_URL or AGENT_DATABASE_URL")
        # Agent/Business repositories use SQLAlchemy URLs, while psycopg and
        # LangGraph's PostgresSaver require a native PostgreSQL connection URI.
        # Normalize before both setup and the long-lived connection; otherwise
        # a managed ``postgresql+psycopg://`` authority makes graph compilation
        # fail even though the database itself is healthy.
        psycopg_url = database_url
        for sqlalchemy_scheme in ("postgresql+psycopg://", "postgresql+psycopg2://"):
            if psycopg_url.lower().startswith(sqlalchemy_scheme):
                psycopg_url = "postgresql://" + psycopg_url[len(sqlalchemy_scheme):]
                break
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            import psycopg
            from psycopg.rows import dict_row
        except Exception as e:
            raise RuntimeError(
                "CHECKPOINT_BACKEND=postgres requires langgraph-checkpoint-postgres and psycopg."
            ) from e
        if os.getenv("CHECKPOINT_SETUP", "true").lower() in {"1", "true", "yes", "on"}:
            # Setup contains PostgreSQL operations (including concurrent index
            # creation) that intentionally run outside turn transactions.
            with PostgresSaver.from_conn_string(psycopg_url) as setup_saver:
                setup_saver.setup()
        conn = psycopg.connect(
            psycopg_url,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        saver = AtomicallyFencedPostgresSaver(conn)
        _CHECKPOINTER_CACHE = FencedCheckpointer(saver)
        _CHECKPOINTER_CONNECTION = conn
        _CHECKPOINTER_CONTEXT = None
        return _CHECKPOINTER_CACHE

    db_path = get_storage_paths()["checkpoint_db"]
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        saver = SqliteSaver(conn)
        if hasattr(saver, "setup"):
            saver.setup()
        _CHECKPOINTER_CACHE = FencedCheckpointer(saver)
        _CHECKPOINTER_CONNECTION = conn
        return _CHECKPOINTER_CACHE
    except Exception as e:
        # Close a connection allocated before a constructor/setup failure.
        if "conn" in locals():
            try:
                conn.close()
            except Exception:
                pass
        strict = os.getenv("STRICT_PERSISTENCE", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        if strict:
            raise RuntimeError(
                f"SQLite checkpointer 加载失败，STRICT_PERSISTENCE=true 禁止退回内存: {e}"
            ) from e
        print(f"[WARN] SQLite checkpointer 加载失败，退回 InMemorySaver: {e}")
        _CHECKPOINTER_CACHE = FencedCheckpointer(InMemorySaver())
        _CHECKPOINTER_CONNECTION = None
        return _CHECKPOINTER_CACHE


# Preserve the existing public call shape used by scripts and tests while making
# cache clears resource-safe.
build_checkpointer.cache_clear = clear_checkpointer_cache  # type: ignore[attr-defined]


def retrieval_top_k() -> int:
    return int(os.getenv("RETRIEVAL_TOP_K", "5"))


def retrieval_min_score() -> float:
    return float(os.getenv("RETRIEVAL_MIN_SCORE", "0.12"))


def chunk_size() -> int:
    return int(os.getenv("CHUNK_SIZE", "700"))


def chunk_overlap() -> int:
    return int(os.getenv("CHUNK_OVERLAP", "120"))
