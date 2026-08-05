from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_REL = Path("skill-system/trusted-judge/manifest.json")

# Files that define the meaning of a quality verdict.  Product tests and source
# remain in the candidate workspace, but a repair task may not silently rewrite
# these controllers, schemas, or gate definitions and then use the rewritten
# version as its own proof.
TRUSTED_PATTERNS = (
    "scripts/quality_loop.py",
    "scripts/quality_control/*.py",
    "scripts/repair_loop.py",
    "scripts/source_paths.py",
    "scripts/verify_*.py",
    "scripts/run_*test*.py",
    "scripts/build_clean_release.py",
    "architecture-skill/scripts/*.py",
    "skill-system/controller/*.py",
    "skill-system/hooks/*.py",
    "skill-system/schemas/*.json",
    "skill-system/profiles/*.json",
    "skill-system/registry/active-*.json",
    "skill-system/registry/deprecated-rules.json",
    "governance/quality-loop-policy.json",
    "governance/evidence_schema/*.json",
    "governance/claim_schema/*.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _matching_files(root: Path, patterns: Iterable[str] = TRUSTED_PATTERNS) -> list[Path]:
    files: set[Path] = set()
    for pattern in patterns:
        files.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def build_manifest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    files = {
        path.relative_to(root).as_posix(): sha256(path)
        for path in _matching_files(root)
    }
    if "scripts/quality_loop.py" not in files:
        raise ValueError("trusted Judge source is incomplete: scripts/quality_loop.py missing")
    return {
        "schema_version": 1,
        "kind": "skill-control-plane-trusted-judge",
        "files": files,
    }


def write_manifest(root: Path) -> Path:
    path = root.resolve() / MANIFEST_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_manifest(root)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_manifest(root: Path) -> dict[str, Any]:
    path = root.resolve() / MANIFEST_REL
    if not path.is_file():
        raise ValueError(f"trusted Judge manifest missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("files"), dict):
        raise ValueError("invalid trusted Judge manifest")
    return payload


def verify_root(root: Path) -> list[str]:
    root = root.resolve()
    try:
        payload = load_manifest(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]
    errors: list[str] = []
    for rel, expected in sorted((payload.get("files") or {}).items()):
        path = root / str(rel)
        if not path.is_file():
            errors.append(f"missing:{rel}")
        elif sha256(path) != str(expected):
            errors.append(f"fingerprint_mismatch:{rel}")
    return errors


def verify_candidate(candidate_root: Path, judge_root: Path) -> list[str]:
    """Compare candidate trust-root inputs with the immutable Judge manifest."""
    candidate_root = candidate_root.resolve()
    judge_root = judge_root.resolve()
    try:
        payload = load_manifest(judge_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]
    errors: list[str] = []
    for rel, expected in sorted((payload.get("files") or {}).items()):
        path = candidate_root / str(rel)
        if not path.is_file():
            errors.append(f"candidate_missing:{rel}")
        elif sha256(path) != str(expected):
            errors.append(f"candidate_trust_root_changed:{rel}")
    return errors


def export_bundle(source_root: Path, destination: Path) -> Path:
    """Create a read-only external Judge bundle from a certified workspace."""
    source_root = source_root.resolve()
    destination = destination.resolve()
    if destination == source_root or source_root in destination.parents:
        raise ValueError("trusted Judge destination must be outside the source workspace")
    payload = build_manifest(source_root)
    if destination.exists():
        shutil.rmtree(destination)
    for rel in payload["files"]:
        source = source_root / rel
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest_path = destination / MANIFEST_REL
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path in destination.rglob("*"):
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        elif path.is_dir():
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return manifest_path


def resolve(
    workspace: Path,
    explicit: str | None = None,
    *,
    require_external: bool = False,
) -> tuple[Path, str]:
    raw = explicit or os.environ.get("SKILL_TRUSTED_JUDGE_ROOT")
    root = Path(raw).expanduser().resolve() if raw else workspace.resolve()
    mode = "external-readonly" if root != workspace.resolve() else "workspace-fallback"
    if require_external and mode != "external-readonly":
        raise ValueError("protected certification requires external SKILL_TRUSTED_JUDGE_ROOT")
    errors = verify_root(root)
    if errors:
        raise ValueError("invalid trusted Judge: " + "; ".join(errors))
    if mode == "external-readonly":
        candidate_errors = verify_candidate(workspace, root)
        if candidate_errors:
            raise ValueError("candidate changed trusted Judge inputs: " + "; ".join(candidate_errors))
    return root, mode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root")
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--require-external", action="store_true")
    parser.add_argument("--verify-layout", action="store_true")
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--export")
    parser.add_argument("--self-test-external", action="store_true")
    args = parser.parse_args()
    workspace = Path(args.workspace_root).resolve()
    try:
        if args.write_manifest:
            path = write_manifest(workspace)
            result = {"status": "PASS", "manifest": str(path)}
        elif args.self_test_external:
            with tempfile.TemporaryDirectory(prefix="skill-trusted-judge-") as raw:
                exported = Path(raw) / "judge"
                path = export_bundle(workspace, exported)
                root, mode = resolve(workspace, str(exported), require_external=True)
                result = {
                    "status": "PASS",
                    "manifest": str(path),
                    "root": str(root),
                    "trust_mode": mode,
                }
        elif args.export:
            path = export_bundle(workspace, Path(args.export))
            result = {"status": "PASS", "manifest": str(path), "trust_mode": "external-readonly"}
        else:
            root, mode = resolve(workspace, args.root, require_external=args.require_external)
            result = {"status": "PASS", "root": str(root), "trust_mode": mode}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "FAIL", "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
