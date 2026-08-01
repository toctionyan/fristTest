#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    Path("services/agent-service/tests/runtime/test_b15b1_real_model_semantic_identity_boundary.py"),
    Path("services/agent-service/tests/runtime/test_b15b2_real_model_lifecycle_attestation_boundary.py"),
)


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path.relative_to(ROOT)} expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    semantic = ROOT / TARGETS[0]
    lifecycle = ROOT / TARGETS[1]

    semantic_old = '''    monkeypatch.delenv("OPENAI_API_KEY", raising=False)\n    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")\n    monkeypatch.delenv("OPENAI_API_BASE", raising=False)\n'''
    semantic_new = '''    # This test owns an OpenAI missing-key scenario and must not inherit the\n    # protected Release provider (for example DeepSeek) from the parent job.\n    monkeypatch.setenv("REAL_MODEL_CERTIFICATION_PROVIDER", "openai")\n    monkeypatch.delenv("OPENAI_API_KEY", raising=False)\n    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")\n    monkeypatch.delenv("OPENAI_API_BASE", raising=False)\n'''
    replace_once(semantic, semantic_old, semantic_new)

    lifecycle_old = '''    monkeypatch.delenv("OPENAI_API_KEY", raising=False)\n    monkeypatch.delenv("OPENAI_API_BASE", raising=False)\n    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")\n'''
    lifecycle_new = '''    # Isolate the intended OpenAI missing-key scenario from production\n    # Workflow variables inherited by the pytest process.\n    monkeypatch.setenv("REAL_MODEL_CERTIFICATION_PROVIDER", "openai")\n    monkeypatch.delenv("OPENAI_API_KEY", raising=False)\n    monkeypatch.delenv("OPENAI_API_BASE", raising=False)\n    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")\n'''
    replace_once(lifecycle, lifecycle_old, lifecycle_new)

    manifest_path = ROOT / "PHASE_CANDIDATE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {str(row.get("path")): row for row in manifest.get("files", []) if isinstance(row, dict)}
    for relative in TARGETS:
        path = ROOT / relative
        row = entries.get(relative.as_posix())
        if row is None:
            raise RuntimeError(f"manifest entry missing: {relative.as_posix()}")
        row["size"] = path.stat().st_size
        row["sha256"] = sha256(path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    self_path = Path(__file__).resolve()
    self_path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
