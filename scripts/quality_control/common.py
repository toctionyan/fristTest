from __future__ import annotations

import ast
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
from pathlib import Path
from typing import Any

from .constants import TARGET_HEADINGS, TARGET_PLACEHOLDERS

def _now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()

def _safe_run_id() -> str:
    return dt.datetime.now(dt.UTC).strftime("run-%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""

def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"policy must be an object: {path}")
    return payload

def _evidence_signing_key(workspace: Path) -> bytes:
    """Return a stable local key or a protected CI-provided key.

    The key is intentionally kept outside evidence directories.  Local keys
    protect against accidental/manual evidence edits; protected CI must inject
    QUALITY_EVIDENCE_SIGNING_KEY and retain evidence as an immutable artifact.
    """
    configured = os.getenv("QUALITY_EVIDENCE_SIGNING_KEY")
    if configured:
        key = configured.encode("utf-8")
        if len(key) < 32:
            raise ValueError("QUALITY_EVIDENCE_SIGNING_KEY must contain at least 32 bytes")
        return key
    key_path = workspace / ".quality" / "quality-evidence.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.is_file():
        key_path.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
    key = key_path.read_text(encoding="utf-8").strip().encode("utf-8")
    if len(key) < 32:
        raise ValueError("local quality evidence signing key is invalid")
    return key

def _evidence_file_hashes(evidence_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(evidence_dir.rglob("*")):
        if not path.is_file() or path.name == "evidence-attestation.json":
            continue
        relative = path.relative_to(evidence_dir).as_posix()
        files[relative] = _sha256_file(path)
    return files

def _write_evidence_attestation(workspace: Path, evidence_dir: Path) -> str:
    files = _evidence_file_hashes(evidence_dir)
    canonical = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest_fingerprint = _sha256_text(canonical)
    key = _evidence_signing_key(workspace)
    signature = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    payload = {
        "schema_version": 1,
        "algorithm": "hmac-sha256",
        "key_id": hashlib.sha256(key).hexdigest()[:16],
        "manifest_fingerprint": manifest_fingerprint,
        "files": files,
        "signature": signature,
    }
    filename = "evidence-attestation.json"
    (evidence_dir / filename).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return filename

def _verify_evidence_attestation(workspace: Path, evidence_dir: Path) -> str | None:
    path = evidence_dir / "evidence-attestation.json"
    if not path.is_file():
        return "evidence does not contain evidence-attestation.json"
    try:
        payload = _load_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"evidence attestation is unreadable: {exc}"
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        return "evidence attestation has no file manifest"
    expected_files = {str(name): str(digest) for name, digest in files.items()}
    actual_files = _evidence_file_hashes(evidence_dir)
    if expected_files != actual_files:
        return "evidence files were added, removed, or modified after attestation"
    canonical = json.dumps(expected_files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if payload.get("manifest_fingerprint") != _sha256_text(canonical):
        return "evidence manifest fingerprint is invalid"
    try:
        key = _evidence_signing_key(workspace)
    except ValueError as exc:
        return str(exc)
    expected_signature = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(payload.get("signature") or ""), expected_signature):
        return "evidence signature is invalid for this workspace/CI trust key"
    return None

def verify_evidence_attestation(workspace: Path, evidence_dir: Path) -> None:
    """Verify that every evidence file is covered by the workspace/CI trust key."""
    error = _verify_evidence_attestation(workspace.resolve(), evidence_dir.resolve())
    if error is not None:
        raise ValueError(error)

def _clean_text(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    half = limit // 2
    return value[:half] + "\n... <truncated> ...\n" + value[-half:]

def _npm_executable(workspace: Path) -> Path | None:
    """Return a reproducible npm executable without assuming a global alias.

    CI normally supplies npm on PATH.  Local quality runs may instead use the
    checked/managed Node runtime under `.quality/tools`; its sibling `node`
    must be put on PATH before npm is invoked because npm's launcher uses
    `/usr/bin/env node`.
    """
    system_npm = shutil.which("npm")
    if system_npm:
        return Path(system_npm).resolve()
    tools_root = workspace / ".quality" / "tools"
    if not tools_root.is_dir():
        return None
    candidates = sorted(
        (
            path
            for path in tools_root.glob("node-*/bin/npm")
            if path.is_file() and os.access(path, os.X_OK) and (path.parent / "node").is_file()
        ),
        key=lambda path: str(path),
    )
    # Do not resolve npm's symlink.  Official Node distributions expose
    # `bin/npm -> ../lib/node_modules/.../npm-cli.js`; resolving it would make
    # `npm.parent` point at npm's JavaScript directory instead of `bin/`, so
    # `/usr/bin/env node` could no longer find the sibling Node executable.
    return candidates[-1].absolute() if candidates else None

def _interpolate(value: str, *, workspace: Path, evidence_dir: Path, mode: str) -> str:
    npm = _npm_executable(workspace)
    return value.format(
        workspace=str(workspace),
        python=sys.executable,
        npm=str(npm) if npm else "npm",
        evidence_dir=str(evidence_dir),
        mode=mode,
    )

def _target_section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    following = [
        text.find(next_heading, start)
        for next_heading in TARGET_HEADINGS
        if next_heading != heading and text.find(next_heading, start) >= 0
    ]
    end = min(following) if following else len(text)
    return text[start:end].strip()

def _target_metadata(section: str, key: str) -> str | None:
    match = re.search(rf"(?m)^\s*[-*]?\s*{re.escape(key)}\s*[:：]\s*(.+?)\s*$", section)
    return match.group(1).strip() if match else None

def _target_fingerprint(text: str) -> str:
    # Advancing the round is the only intentional mutable field in a target.
    normalized = re.sub(r"(当前轮次\s*[:：]\s*)\d+", r"\1<round>", text)
    normalized = re.sub(r"\r\n?", "\n", normalized).strip() + "\n"
    return _sha256_text(normalized)

def _is_target_placeholder(value: str | None) -> bool:
    normalized = (value or "").strip().strip("`").lower()
    if not normalized or normalized in TARGET_PLACEHOLDERS:
        return True
    return any(marker in normalized for marker in ("yyyy", "<", ">", "{", "}"))

def _python_selector_exists(path: Path, selector: str) -> bool:
    """Return whether a pytest-style Python selector exists in the file AST."""
    clean_parts = [part.split("[", 1)[0] for part in selector.split("::") if part]
    if not clean_parts:
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    current: list[ast.stmt] = list(tree.body)
    for index, part in enumerate(clean_parts):
        node = next(
            (
                item
                for item in current
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and item.name == part
            ),
            None,
        )
        if node is None:
            return False
        if index < len(clean_parts) - 1:
            if not isinstance(node, ast.ClassDef):
                return False
            current = list(node.body)
    return True

def _safe_workspace_relative_json(workspace: Path, raw: str, *, field: str) -> Path:
    value = raw.strip().strip("`")
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.lower() != ".json"
    ):
        raise ValueError(f"{field} must be a safe workspace-relative .json path")
    resolved = (workspace / path).resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"{field} must stay inside the workspace") from exc
    return resolved

def _canonical_json_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256_text(canonical)

