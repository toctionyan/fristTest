from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


ARTIFACT_SCHEMA = "github-repository-onboarding-metadata@1"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_PAGES = 100
TOP_LEVEL_KEYS = {
    "schema",
    "repository",
    "api_origin",
    "metadata",
    "authority_effect",
    "deploy_allowed",
    "production_closed",
    "seal_sha256",
}
METADATA_KEYS = {
    "repository_full_name",
    "default_branch",
    "visibility",
    "permissions",
    "is_empty",
    "branch_protection",
    "environments",
    "secret_names",
    "workspace_marker",
}


class GitHubRepositoryOnboardingError(RuntimeError):
    """Fail-closed live repository metadata collection error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GitHubHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class GitHubReadTransport(Protocol):
    def request(
        self, *, method: str, url: str, headers: Mapping[str, str]
    ) -> GitHubHttpResponse:
        ...


class UrllibGitHubReadTransport:
    """Bounded HTTP client that exposes status but never embeds bodies in errors."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise GitHubRepositoryOnboardingError("timeout_invalid", "timeout must be positive")
        self.timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def request(
        self, *, method: str, url: str, headers: Mapping[str, str]
    ) -> GitHubHttpResponse:
        if method != "GET":
            raise GitHubRepositoryOnboardingError("method_forbidden", "only GitHub GET is allowed")
        request = urllib.request.Request(url=url, headers=dict(headers), method="GET")
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                status = int(response.status)
                response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_RESPONSE_BYTES + 1)
            status = int(exc.code)
            response_headers = dict(exc.headers.items()) if exc.headers is not None else {}
        except urllib.error.URLError as exc:
            raise GitHubRepositoryOnboardingError(
                "transport_failed", "GitHub API transport failed"
            ) from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise GitHubRepositoryOnboardingError(
                "response_too_large", "GitHub API response exceeds the size limit"
            )
        return GitHubHttpResponse(status=status, headers=response_headers, body=body)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[no-untyped-def]
        self, req, fp, code, msg, headers, newurl
    ):
        return None


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _seal(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _object(raw: bytes, *, context: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubRepositoryOnboardingError(
            "response_json_invalid", f"GitHub {context} response is not valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise GitHubRepositoryOnboardingError(
            "response_shape_invalid", f"GitHub {context} response must be an object"
        )
    return value


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GitHubRepositoryOnboardingError(
            "response_field_invalid", f"GitHub response field is invalid: {field}"
        )
    return value.strip()


class GitHubRepositoryOnboardingTransport:
    """Collect names-only GitHub repository admission evidence.

    This component cannot decide onboarding readiness and has no mutation method.
    """

    def __init__(
        self,
        *,
        repository_full_name: str,
        token: str,
        api_base: str = "https://api.github.com",
        transport: GitHubReadTransport | None = None,
    ) -> None:
        repository = repository_full_name.strip()
        if not REPOSITORY_RE.fullmatch(repository):
            raise GitHubRepositoryOnboardingError(
                "repository_invalid", "repository must be an exact owner/name"
            )
        if not isinstance(token, str) or not token:
            raise GitHubRepositoryOnboardingError(
                "token_missing", "GitHub token environment variable is empty"
            )
        parsed = urllib.parse.urlparse(api_base.strip())
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise GitHubRepositoryOnboardingError(
                "api_origin_invalid", "GitHub API base must be an HTTPS origin without credentials"
            )
        self.repository_full_name = repository
        self._repository_path = f"/repos/{repository}"
        self._token = token
        self.api_origin = f"{parsed.scheme}://{parsed.netloc}"
        self.transport = transport or UrllibGitHubReadTransport()

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "fristtest-repository-onboarding/1",
        }

    def _validate_url(
        self,
        url: str,
        *,
        expected_path: str,
        allowed_query_keys: frozenset[str] = frozenset({"page", "per_page"}),
    ) -> None:
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin != self.api_origin or parsed.path != expected_path or parsed.fragment:
            raise GitHubRepositoryOnboardingError(
                "pagination_invalid", "GitHub pagination escaped its repository-bound endpoint"
            )
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if set(query) - allowed_query_keys:
            raise GitHubRepositoryOnboardingError(
                "pagination_invalid", "GitHub pagination contained unsupported parameters"
            )
        if "per_page" in query and query["per_page"] != ["100"]:
            raise GitHubRepositoryOnboardingError(
                "pagination_invalid", "GitHub pagination changed the bounded page size"
            )
        if "page" in query:
            values = query["page"]
            if len(values) != 1 or not values[0].isdigit() or int(values[0]) < 1:
                raise GitHubRepositoryOnboardingError(
                    "pagination_invalid", "GitHub pagination page is invalid"
                )

    def _response(
        self,
        url: str,
        *,
        expected_path: str,
        missing_is_none: bool = False,
        allowed_query_keys: frozenset[str] = frozenset({"page", "per_page"}),
    ) -> GitHubHttpResponse | None:
        self._validate_url(
            url,
            expected_path=expected_path,
            allowed_query_keys=allowed_query_keys,
        )
        try:
            response = self.transport.request(method="GET", url=url, headers=self._headers())
        except GitHubRepositoryOnboardingError:
            raise
        except Exception as exc:
            raise GitHubRepositoryOnboardingError(
                "transport_failed", "GitHub API transport failed"
            ) from exc
        if response.status == 404 and missing_is_none:
            return None
        if response.status != 200:
            raise GitHubRepositoryOnboardingError(
                "http_failed", f"GitHub API returned HTTP {response.status}"
            )
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise GitHubRepositoryOnboardingError(
                "response_too_large", "GitHub API response exceeds the size limit"
            )
        return response

    def _get_object(
        self,
        path: str,
        *,
        query: str = "",
        context: str,
        missing_is_none: bool = False,
        allowed_query_keys: frozenset[str] = frozenset({"page", "per_page"}),
    ) -> dict[str, Any] | None:
        url = f"{self.api_origin}{path}{query}"
        response = self._response(
            url,
            expected_path=path,
            missing_is_none=missing_is_none,
            allowed_query_keys=allowed_query_keys,
        )
        return None if response is None else _object(response.body, context=context)

    @staticmethod
    def _next_link(headers: Mapping[str, str]) -> str | None:
        link = next((str(value) for key, value in headers.items() if key.casefold() == "link"), "")
        if not link:
            return None
        for part in link.split(","):
            match = re.match(r"\s*<([^>]+)>\s*(;.*)\s*$", part)
            if not match:
                raise GitHubRepositoryOnboardingError(
                    "pagination_invalid", "GitHub pagination Link header is malformed"
                )
            relation = re.search(r'(?:^|;)\s*rel="([^"]+)"(?:\s*;|\s*$)', match.group(2))
            if relation and "next" in relation.group(1).split():
                return match.group(1)
        return None

    def _list_names(self, path: str, *, collection_key: str) -> list[str]:
        url = f"{self.api_origin}{path}?per_page=100"
        names: set[str] = set()
        visited: set[str] = set()
        for _ in range(MAX_PAGES):
            if url in visited:
                raise GitHubRepositoryOnboardingError(
                    "pagination_invalid", "GitHub pagination loop detected"
                )
            visited.add(url)
            response = self._response(url, expected_path=path)
            assert response is not None
            payload = _object(response.body, context=collection_key)
            items = payload.get(collection_key)
            if not isinstance(items, list):
                raise GitHubRepositoryOnboardingError(
                    "response_shape_invalid", f"GitHub {collection_key} must be a list"
                )
            for item in items:
                if not isinstance(item, Mapping):
                    raise GitHubRepositoryOnboardingError(
                        "response_shape_invalid", f"GitHub {collection_key} item must be an object"
                    )
                names.add(_required_text(item.get("name"), field=f"{collection_key}.name"))
            next_url = self._next_link(response.headers)
            if next_url is None:
                return sorted(names)
            self._validate_url(next_url, expected_path=path)
            url = next_url
        raise GitHubRepositoryOnboardingError(
            "pagination_exhausted", "GitHub pagination exceeded the page limit"
        )

    def _remote_file(self, relative_path: str, *, branch: str) -> bytes:
        encoded_path = urllib.parse.quote(relative_path, safe="/")
        path = f"{self._repository_path}/contents/{encoded_path}"
        query = f"?ref={urllib.parse.quote(branch, safe='')}"
        payload = self._get_object(
            path,
            query=query,
            context=f"contents:{relative_path}",
            allowed_query_keys=frozenset({"ref"}),
        )
        assert payload is not None
        if payload.get("type") != "file" or payload.get("encoding") != "base64":
            raise GitHubRepositoryOnboardingError(
                "content_encoding_invalid", f"remote file encoding is invalid: {relative_path}"
            )
        content = payload.get("content")
        size = payload.get("size")
        if not isinstance(content, str) or not isinstance(size, int) or isinstance(size, bool):
            raise GitHubRepositoryOnboardingError(
                "content_shape_invalid", f"remote file metadata is invalid: {relative_path}"
            )
        if size < 0 or size > MAX_CONTENT_BYTES:
            raise GitHubRepositoryOnboardingError(
                "content_size_invalid", f"remote file exceeds the size limit: {relative_path}"
            )
        try:
            compact_content = re.sub(r"[\t\n\r ]+", "", content)
            raw = base64.b64decode(compact_content, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise GitHubRepositoryOnboardingError(
                "content_base64_invalid", f"remote file base64 is invalid: {relative_path}"
            ) from exc
        if len(raw) != size:
            raise GitHubRepositoryOnboardingError(
                "content_size_invalid", f"remote file size does not match: {relative_path}"
            )
        return raw

    @staticmethod
    def _json_file(raw: bytes, *, relative_path: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubRepositoryOnboardingError(
                "content_json_invalid", f"remote file JSON is invalid: {relative_path}"
            ) from exc
        if not isinstance(payload, dict):
            raise GitHubRepositoryOnboardingError(
                "content_json_invalid", f"remote file must contain an object: {relative_path}"
            )
        return payload

    def _workspace_marker(self, *, branch: str) -> dict[str, str]:
        manifest_raw = self._remote_file("PHASE_CANDIDATE_MANIFEST.json", branch=branch)
        release_raw = self._remote_file("release/MANIFEST.json", branch=branch)
        manifest = self._json_file(manifest_raw, relative_path="PHASE_CANDIDATE_MANIFEST.json")
        release = self._json_file(release_raw, relative_path="release/MANIFEST.json")
        skill = release.get("skill") if isinstance(release.get("skill"), Mapping) else {}
        marker = {
            "workspace": _required_text(release.get("workspace"), field="release.workspace"),
            "version": _required_text(release.get("version"), field="release.version"),
            "skill_version": _required_text(skill.get("version"), field="release.skill.version"),
            "phase": str(release.get("phase") or manifest.get("phase") or ""),
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        }
        return marker

    def collect(self) -> dict[str, Any]:
        repository = self._get_object(self._repository_path, context="repository")
        assert repository is not None
        full_name = _required_text(repository.get("full_name"), field="full_name")
        if full_name.casefold() != self.repository_full_name.casefold():
            raise GitHubRepositoryOnboardingError(
                "repository_identity_mismatch",
                "GitHub repository identity does not match the configured target",
            )
        default_branch = _required_text(
            repository.get("default_branch"), field="default_branch"
        )
        visibility = repository.get("visibility")
        if not isinstance(visibility, str) or visibility.casefold() not in {
            "private",
            "public",
            "internal",
        }:
            if isinstance(repository.get("private"), bool):
                visibility = "private" if repository["private"] else "public"
            else:
                raise GitHubRepositoryOnboardingError(
                    "repository_visibility_invalid", "GitHub repository visibility is ambiguous"
                )
        size = repository.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise GitHubRepositoryOnboardingError(
                "repository_empty_state_invalid", "GitHub repository empty state is ambiguous"
            )
        permissions_payload = repository.get("permissions")
        if not isinstance(permissions_payload, Mapping):
            raise GitHubRepositoryOnboardingError(
                "repository_permissions_invalid", "GitHub repository permissions are unavailable"
            )
        permissions: dict[str, bool] = {}
        for name in ("admin", "maintain", "push"):
            value = permissions_payload.get(name, False)
            if not isinstance(value, bool):
                raise GitHubRepositoryOnboardingError(
                    "repository_permissions_invalid", "GitHub repository permission is not boolean"
                )
            permissions[name] = value

        protection_path = f"{self._repository_path}/branches/main/protection"
        protected = self._get_object(
            protection_path,
            context="branch-protection",
            missing_is_none=True,
        ) is not None
        environments_path = f"{self._repository_path}/environments"
        environments = self._list_names(environments_path, collection_key="environments")
        secret_names: list[str] = []
        if "production-certification" in environments:
            secrets_path = (
                f"{self._repository_path}/environments/production-certification/secrets"
            )
            secret_names = self._list_names(secrets_path, collection_key="secrets")

        is_empty = size == 0
        marker = None if is_empty else self._workspace_marker(branch=default_branch)
        metadata = {
            "repository_full_name": full_name,
            "default_branch": default_branch,
            "visibility": visibility.casefold(),
            "permissions": permissions,
            "is_empty": is_empty,
            "branch_protection": {"main": protected},
            "environments": environments,
            "secret_names": secret_names,
            "workspace_marker": marker,
        }
        unsigned: dict[str, Any] = {
            "schema": ARTIFACT_SCHEMA,
            "repository": self.repository_full_name,
            "api_origin": self.api_origin,
            "metadata": metadata,
            "authority_effect": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
        return {**unsigned, "seal_sha256": _seal(unsigned)}

    def _validate_artifact(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        if set(artifact) != TOP_LEVEL_KEYS:
            raise GitHubRepositoryOnboardingError(
                "artifact_schema_invalid", "repository onboarding artifact has unexpected fields"
            )
        if artifact.get("schema") != ARTIFACT_SCHEMA:
            raise GitHubRepositoryOnboardingError(
                "artifact_schema_invalid", "repository onboarding artifact schema is invalid"
            )
        if (
            artifact.get("repository") != self.repository_full_name
            or artifact.get("api_origin") != self.api_origin
        ):
            raise GitHubRepositoryOnboardingError(
                "artifact_identity_mismatch", "repository onboarding artifact identity is invalid"
            )
        metadata = artifact.get("metadata")
        if not isinstance(metadata, Mapping) or set(metadata) != METADATA_KEYS:
            raise GitHubRepositoryOnboardingError(
                "artifact_schema_invalid", "repository onboarding metadata schema is invalid"
            )
        if (
            metadata.get("repository_full_name", "").casefold()
            != self.repository_full_name.casefold()
        ):
            raise GitHubRepositoryOnboardingError(
                "artifact_identity_mismatch", "repository onboarding metadata identity is invalid"
            )
        if str(metadata.get("default_branch") or "") == "":
            raise GitHubRepositoryOnboardingError(
                "artifact_schema_invalid", "repository onboarding default branch is invalid"
            )
        if metadata.get("visibility") not in {"private", "public", "internal"}:
            raise GitHubRepositoryOnboardingError(
                "artifact_schema_invalid", "repository onboarding visibility is invalid"
            )
        permissions = metadata.get("permissions")
        if (
            not isinstance(permissions, Mapping)
            or set(permissions) != {"admin", "maintain", "push"}
            or any(not isinstance(value, bool) for value in permissions.values())
        ):
            raise GitHubRepositoryOnboardingError(
                "artifact_schema_invalid", "repository onboarding permissions are invalid"
            )
        if not isinstance(metadata.get("is_empty"), bool):
            raise GitHubRepositoryOnboardingError(
                "artifact_schema_invalid", "repository onboarding empty state is invalid"
            )
        protection = metadata.get("branch_protection")
        if (
            not isinstance(protection, Mapping)
            or set(protection) != {"main"}
            or not isinstance(protection.get("main"), bool)
        ):
            raise GitHubRepositoryOnboardingError(
                "artifact_schema_invalid", "repository onboarding branch protection is invalid"
            )
        for name in ("environments", "secret_names"):
            values = metadata.get(name)
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) or not value for value in values)
                or values != sorted(set(values))
            ):
                raise GitHubRepositoryOnboardingError(
                    "artifact_schema_invalid", f"repository onboarding {name} are invalid"
                )
        marker = metadata.get("workspace_marker")
        if metadata["is_empty"] is True:
            if marker is not None:
                raise GitHubRepositoryOnboardingError(
                    "artifact_schema_invalid", "empty repository cannot contain a workspace marker"
                )
        elif (
            not isinstance(marker, Mapping)
            or set(marker) != {"workspace", "version", "skill_version", "phase", "manifest_sha256"}
            or any(not isinstance(marker.get(key), str) for key in marker)
            or not re.fullmatch(r"[0-9a-f]{64}", str(marker.get("manifest_sha256") or ""))
        ):
            raise GitHubRepositoryOnboardingError(
                "artifact_schema_invalid", "nonempty repository workspace marker is invalid"
            )
        for flag in ("authority_effect", "deploy_allowed", "production_closed"):
            if artifact.get(flag) is not False:
                raise GitHubRepositoryOnboardingError(
                    "artifact_authority_invalid",
                    "repository onboarding artifact cannot grant authority",
                )
        unsigned = {key: artifact[key] for key in TOP_LEVEL_KEYS if key != "seal_sha256"}
        seal = artifact.get("seal_sha256")
        if (
            not isinstance(seal, str)
            or not re.fullmatch(r"[0-9a-f]{64}", seal)
            or seal != _seal(unsigned)
        ):
            raise GitHubRepositoryOnboardingError(
                "artifact_seal_invalid", "repository onboarding artifact seal is invalid"
            )
        return dict(artifact)

    def collect_and_write(self, output_path: Path) -> dict[str, Any]:
        artifact = self.collect()
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.is_symlink():
            raise GitHubRepositoryOnboardingError(
                "artifact_path_unsafe", "repository onboarding artifact path cannot be a symlink"
            )
        raw = json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=output.parent,
                prefix=f".{output.name}.",
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, output)
        except OSError as exc:
            if temp_name:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise GitHubRepositoryOnboardingError(
                "artifact_write_failed", "repository onboarding artifact could not be written"
            ) from exc
        return artifact

    def load_artifact(
        self, path: Path, *, expected_seal_sha256: str
    ) -> dict[str, Any]:
        source = Path(path)
        if source.is_symlink() or not source.is_file():
            raise GitHubRepositoryOnboardingError(
                "artifact_path_unsafe", "repository onboarding artifact is missing or unsafe"
            )
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise GitHubRepositoryOnboardingError(
                "artifact_read_failed", "repository onboarding artifact could not be read"
            ) from exc
        if not raw or len(raw) > MAX_RESPONSE_BYTES:
            raise GitHubRepositoryOnboardingError(
                "artifact_size_invalid", "repository onboarding artifact size is invalid"
            )
        artifact = self._validate_artifact(_object(raw, context="artifact"))
        if artifact["seal_sha256"] != expected_seal_sha256:
            raise GitHubRepositoryOnboardingError(
                "artifact_seal_invalid",
                "repository onboarding artifact seal changed after collection",
            )
        return artifact
