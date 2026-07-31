from __future__ import annotations

"""Fail-closed identity and response attestation for real-model certification.

The production runtime intentionally supports OpenAI-compatible endpoints.  A
release certification is narrower: it must prove that the call went to a known
official provider rather than a localhost stub or an arbitrary compatibility
proxy.  This module owns that certification-only boundary and never exposes the
credential value.
"""

from dataclasses import dataclass
import hashlib
import ipaddress
import os
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit


_OFFICIAL_ENDPOINTS: dict[str, tuple[str, str]] = {
    "openai": ("api.openai.com", "/v1"),
    "deepseek": ("api.deepseek.com", ""),
}
_ALLOWED_PATHS = {"", "/", "/v1", "/v1/"}
_TEST_MARKERS = (
    "deterministic",
    "not-a-real",
    "not_a_real",
    "placeholder",
    "changeme",
    "dummy",
    "example",
    "fake",
    "mock",
    "stub",
    "test-key",
    "test_key",
    "test-model",
    "test_model",
)
_DEPRECATED_DEEPSEEK_ALIASES = frozenset({"deepseek-chat", "deepseek-reasoner"})


@dataclass(frozen=True)
class RealModelCertificationError(RuntimeError):
    code: str
    detail: str
    phase: str = "identity"
    environment_blocked: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def _value(env: Mapping[str, str], name: str) -> str:
    return str(env.get(name) or "").strip()


def _contains_test_marker(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in _TEST_MARKERS)


def _provider_from_host(host: str) -> str | None:
    for provider, (official_host, _default_path) in _OFFICIAL_ENDPOINTS.items():
        if host == official_host:
            return provider
    return None


def _validate_host_is_not_local(host: str) -> None:
    normalized = host.rstrip(".").casefold()
    if normalized in {"localhost", "localhost.localdomain"} or normalized.endswith(".localhost"):
        raise RealModelCertificationError(
            "local_endpoint_forbidden",
            "real-model certification cannot use localhost",
        )
    try:
        address = ipaddress.ip_address(normalized.strip("[]"))
    except ValueError:
        return
    if not address.is_global:
        raise RealModelCertificationError(
            "private_endpoint_forbidden",
            "real-model certification cannot use a non-global IP address",
        )


def _canonical_endpoint(raw_base_url: str) -> tuple[str, str]:
    if not raw_base_url:
        provider = "openai"
        host, path = _OFFICIAL_ENDPOINTS[provider]
        return provider, f"https://{host}{path}"

    parsed = urlsplit(raw_base_url)
    if parsed.scheme.casefold() != "https":
        raise RealModelCertificationError(
            "https_required",
            "real-model certification requires an HTTPS provider endpoint",
        )
    if parsed.username or parsed.password:
        raise RealModelCertificationError(
            "endpoint_userinfo_forbidden",
            "provider endpoint must not contain embedded credentials",
        )
    if parsed.query or parsed.fragment:
        raise RealModelCertificationError(
            "endpoint_suffix_forbidden",
            "provider endpoint must not contain a query or fragment",
        )
    host = str(parsed.hostname or "").rstrip(".").casefold()
    if not host:
        raise RealModelCertificationError("endpoint_host_missing", "provider endpoint host is missing")
    _validate_host_is_not_local(host)
    if parsed.port not in {None, 443}:
        raise RealModelCertificationError(
            "endpoint_port_forbidden",
            "official provider certification only accepts the default HTTPS port",
        )
    provider = _provider_from_host(host)
    if provider is None:
        raise RealModelCertificationError(
            "unofficial_endpoint_forbidden",
            "real-model certification only accepts an official OpenAI or DeepSeek API host",
        )
    path = parsed.path or ""
    if path not in _ALLOWED_PATHS:
        raise RealModelCertificationError(
            "endpoint_path_forbidden",
            "provider endpoint path must be empty or /v1",
        )
    canonical_path = "/v1" if provider == "openai" and path in {"", "/"} else path.rstrip("/")
    return provider, urlunsplit(("https", host, canonical_path, "", ""))


def _validate_api_key(api_key: str) -> str:
    if not api_key:
        raise RealModelCertificationError(
            "api_key_missing",
            "OPENAI_API_KEY is required for protected real-model certification",
            environment_blocked=True,
        )
    if len(api_key) < 20 or _contains_test_marker(api_key):
        raise RealModelCertificationError(
            "test_credential_forbidden",
            "credential is too short or contains a test/placeholder marker",
        )
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def _validate_model(provider: str, model: str) -> None:
    if not model:
        raise RealModelCertificationError("model_missing", "OPENAI_MODEL is required")
    if _contains_test_marker(model):
        raise RealModelCertificationError(
            "test_model_forbidden",
            "model name contains a test/stub marker",
        )
    normalized = model.casefold()
    if provider == "deepseek":
        if normalized in _DEPRECATED_DEEPSEEK_ALIASES:
            raise RealModelCertificationError(
                "deprecated_deepseek_model_alias",
                "use a current DeepSeek model name instead of a deprecated compatibility alias",
            )
        if not normalized.startswith("deepseek-"):
            raise RealModelCertificationError(
                "provider_model_mismatch",
                "DeepSeek certification requires a DeepSeek model name",
            )
    elif normalized.startswith("deepseek-"):
        raise RealModelCertificationError(
            "provider_model_mismatch",
            "DeepSeek model names require the official DeepSeek endpoint",
        )


def resolve_real_model_identity(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = os.environ if env is None else env
    api_key = _value(source, "OPENAI_API_KEY")
    model = _value(source, "OPENAI_MODEL") or "gpt-4o-mini"
    provider, endpoint = _canonical_endpoint(_value(source, "OPENAI_API_BASE"))
    provider_hint = _value(source, "REAL_MODEL_CERTIFICATION_PROVIDER").casefold()
    if provider_hint and provider_hint not in _OFFICIAL_ENDPOINTS:
        raise RealModelCertificationError(
            "provider_hint_invalid",
            "REAL_MODEL_CERTIFICATION_PROVIDER must be openai or deepseek",
        )
    if provider_hint and provider_hint != provider:
        raise RealModelCertificationError(
            "provider_hint_mismatch",
            "declared certification provider does not match the official endpoint",
        )
    credential_fingerprint = _validate_api_key(api_key)
    _validate_model(provider, model)
    parsed = urlsplit(endpoint)
    return {
        "contract": "real-model-identity@1",
        "provider": provider,
        "endpoint": endpoint,
        "endpoint_host": parsed.hostname,
        "model": model,
        "credential_fingerprint_sha256_16": credential_fingerprint,
        "official_endpoint": True,
        "https": True,
    }


def _usage_metadata(response: Any) -> dict[str, int]:
    metadata = getattr(response, "response_metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    raw = metadata.get("token_usage")
    raw = raw if isinstance(raw, dict) else {}
    normalized = getattr(response, "usage_metadata", None)
    normalized = normalized if isinstance(normalized, dict) else {}

    def nonnegative(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    prompt = nonnegative(raw.get("prompt_tokens"))
    completion = nonnegative(raw.get("completion_tokens"))
    total = nonnegative(raw.get("total_tokens"))
    if prompt is None:
        prompt = nonnegative(normalized.get("input_tokens"))
    if completion is None:
        completion = nonnegative(normalized.get("output_tokens"))
    if total is None:
        total = nonnegative(normalized.get("total_tokens"))
    result: dict[str, int] = {}
    if prompt is not None:
        result["prompt_tokens"] = prompt
    if completion is not None:
        result["completion_tokens"] = completion
    if total is not None:
        result["total_tokens"] = total
    return result


def _reported_model(response: Any) -> str:
    metadata = getattr(response, "response_metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    for value in (
        metadata.get("model_name"),
        metadata.get("model"),
        getattr(response, "model_name", None),
        getattr(response, "model", None),
    ):
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _model_matches(*, configured: str, reported: str, provider: str) -> bool:
    left = configured.casefold()
    right = reported.casefold()
    if left == right:
        return True
    if provider == "openai" and right.startswith(left + "-"):
        # OpenAI aliases may report a dated snapshot of the configured model.
        return True
    return False


def attest_real_model_metadata(
    *,
    response: Any,
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Attest provider-reported metadata without interpreting response content."""

    reported_model = _reported_model(response)
    if not reported_model:
        raise RealModelCertificationError(
            "provider_model_metadata_missing",
            "provider response did not report a model identity",
            phase="response",
        )
    configured_model = str(identity.get("model") or "")
    provider = str(identity.get("provider") or "")
    if not _model_matches(
        configured=configured_model,
        reported=reported_model,
        provider=provider,
    ):
        raise RealModelCertificationError(
            "provider_model_metadata_mismatch",
            "provider-reported model does not match the configured certification model",
            phase="response",
        )
    metadata = getattr(response, "response_metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    finish_reason = str(metadata.get("finish_reason") or metadata.get("stop_reason") or "").strip()
    if not finish_reason:
        raise RealModelCertificationError(
            "finish_reason_missing",
            "provider response did not report a finish reason",
            phase="response",
        )
    usage = _usage_metadata(response)
    if int(usage.get("total_tokens") or 0) <= 0:
        raise RealModelCertificationError(
            "token_usage_missing",
            "provider response did not report positive token usage",
            phase="response",
        )
    fingerprint = metadata.get("system_fingerprint")
    response_id = str(getattr(response, "id", "") or "")
    return {
        "contract": "real-model-metadata-attestation@1",
        "provider": provider,
        "configured_model": configured_model,
        "reported_model": reported_model,
        "finish_reason": finish_reason,
        "token_usage": usage,
        "system_fingerprint_present": bool(fingerprint),
        "response_id_present": bool(response_id),
    }


def attest_real_model_call_record(
    *,
    record: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Attest one safe ModelCall Gateway record from a complete lifecycle run."""

    if str(record.get("status") or "") != "ok":
        raise RealModelCertificationError(
            "model_call_not_successful",
            "certified lifecycle contains a model call that did not complete successfully",
            phase="response",
        )
    reported_model = str(record.get("provider_model") or "").strip()
    if not reported_model:
        raise RealModelCertificationError(
            "provider_model_metadata_missing",
            "model call record did not report a provider model identity",
            phase="response",
        )
    configured_model = str(identity.get("model") or "")
    provider = str(identity.get("provider") or "")
    if not _model_matches(configured=configured_model, reported=reported_model, provider=provider):
        raise RealModelCertificationError(
            "provider_model_metadata_mismatch",
            "model call provider identity does not match the configured certification model",
            phase="response",
        )
    finish_reason = str(record.get("finish_reason") or "").strip()
    if not finish_reason:
        raise RealModelCertificationError(
            "finish_reason_missing",
            "model call record did not report a finish reason",
            phase="response",
        )
    try:
        total_tokens = int(record.get("total_tokens") or 0)
    except (TypeError, ValueError):
        total_tokens = 0
    if total_tokens <= 0:
        raise RealModelCertificationError(
            "token_usage_missing",
            "model call record did not report positive token usage",
            phase="response",
        )
    return {
        "contract": "real-model-call-attestation@1",
        "provider": provider,
        "configured_model": configured_model,
        "reported_model": reported_model,
        "finish_reason": finish_reason,
        "total_tokens": total_tokens,
        "purpose": str(record.get("purpose") or ""),
    }


def attest_real_model_response(
    *,
    response: Any,
    identity: Mapping[str, Any],
    expected_content: str,
) -> dict[str, Any]:
    content = str(getattr(response, "content", "") or "").strip()
    if content != expected_content:
        raise RealModelCertificationError(
            "dynamic_challenge_mismatch",
            "model did not return the exact per-run challenge",
            phase="response",
        )
    attestation = attest_real_model_metadata(response=response, identity=identity)
    return {
        **attestation,
        "contract": "real-model-response-attestation@1",
        "challenge_sha256": hashlib.sha256(expected_content.encode("utf-8")).hexdigest(),
    }


__all__ = [
    "RealModelCertificationError",
    "resolve_real_model_identity",
    "attest_real_model_metadata",
    "attest_real_model_call_record",
    "attest_real_model_response",
]
