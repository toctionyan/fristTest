#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
MANIFEST = ROOT / "PHASE_CANDIDATE_MANIFEST.json"


def replace_exact(path: Path, old: str, new: str, *, expected: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{path.relative_to(ROOT)} expected {expected} occurrences, found {count}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch_env_example() -> None:
    path = ROOT / "services/agent-service/.env.example"
    replacements = [
        ("OPENAI_MODEL=gpt-4o-mini", "OPENAI_MODEL=deepseek-v4-flash"),
        ("OPENAI_API_BASE=", "OPENAI_API_BASE=https://api.deepseek.com"),
        ("REAL_MODEL_CERTIFICATION_PROVIDER=", "REAL_MODEL_CERTIFICATION_PROVIDER=deepseek"),
        ("EMBEDDING_PROVIDER=local_sparse", "EMBEDDING_PROVIDER=openai_compatible"),
        ("EMBEDDING_MODEL=", "EMBEDDING_MODEL=text-embedding-v4"),
        ("EMBEDDING_API_BASE=", "EMBEDDING_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("EMBEDDING_BASE_URL=", "EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("EMBEDDING_BATCH_SIZE=100", "EMBEDDING_BATCH_SIZE=10"),
        ("EMBEDDING_DIM=1536", "EMBEDDING_DIM=1024"),
    ]
    for old, new in replacements:
        replace_exact(path, old, new)


def patch_embedding_provider_defaults() -> None:
    path = ROOT / "services/agent-service/src/agent_core/rag/embedding_providers/openai_provider.py"
    replace_exact(
        path,
        'self.model = model or os.getenv("EMBEDDING_MODEL") or "text-embedding-3-small"',
        'self.model = model or os.getenv("EMBEDDING_MODEL") or "text-embedding-v4"',
    )
    replace_exact(
        path,
        'self.batch_size = max(1, int(os.getenv("EMBEDDING_BATCH_SIZE", "100")))',
        'self.batch_size = max(1, int(os.getenv("EMBEDDING_BATCH_SIZE", "10")))',
    )


def patch_docs() -> None:
    runbook = ROOT / "docs/operations/B17I_FINAL_PRODUCTION_EXECUTION_RUNBOOK.md"
    replace_exact(
        runbook,
        "- `PRODUCTION_EMBEDDING_API_BASE`：默认 `https://api.openai.com/v1`。",
        "- `PRODUCTION_EMBEDDING_API_BASE`：默认阿里百炼北京地域 `https://dashscope.aliyuncs.com/compatible-mode/v1`；新加坡或业务空间专属 Key 必须覆盖为对应地域地址。",
    )
    replace_exact(runbook, "- `embedding_model`: `text-embedding-3-small`", "- `embedding_model`: `text-embedding-v4`")
    replace_exact(runbook, "- `embedding_dimension`: `1536`", "- `embedding_dimension`: `1024`")

    handoff = ROOT / "docs/operations/B17H_PROTECTED_RELEASE_HANDOFF.md"
    replace_exact(
        handoff,
        "- Optional variable `PRODUCTION_EMBEDDING_API_BASE`; when absent the workflow uses the official OpenAI API base",
        "- Optional variable `PRODUCTION_EMBEDDING_API_BASE`; when absent the workflow uses Alibaba Model Studio Beijing OpenAI-compatible base, while Singapore or workspace-specific keys must override it",
    )

    config = ROOT / "docs/operations/CONFIGURATION.md"
    replace_exact(
        config,
        "| `OPENAI_MODEL` | 主对话、RAG、语义核验共享模型名 | `gpt-4o-mini` |",
        "| `OPENAI_MODEL` | 主对话、规划与语义核验模型名 | `deepseek-v4-flash` |",
    )
    replace_exact(
        config,
        "| `OPENAI_API_BASE` | OpenAI 兼容 Base URL | 空，使用 SDK 默认值 |",
        "| `OPENAI_API_BASE` | OpenAI 兼容 Base URL | `https://api.deepseek.com` |",
    )


def patch_tests() -> None:
    b17d = ROOT / "services/agent-service/tests/runtime/test_b17d_protected_browser_runtime_authority.py"
    replacements = [
        ('monkeypatch.setenv("OPENAI_API_BASE", "https://api.openai.com/v1")', 'monkeypatch.setenv("OPENAI_API_BASE", "https://api.deepseek.com")'),
        ('monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")', 'monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")'),
        ('monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")', 'monkeypatch.setenv("EMBEDDING_PROVIDER", "openai_compatible")'),
        ('monkeypatch.setenv("EMBEDDING_API_BASE", "https://api.openai.com/v1")', 'monkeypatch.setenv("EMBEDDING_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")'),
        ('monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")', 'monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v4")'),
        ('monkeypatch.setenv("EMBEDDING_DIM", "1536")', 'monkeypatch.setenv("EMBEDDING_DIM", "1024")'),
        ('assert "EMBEDDING_PROVIDER: openai" in workflow', 'assert "EMBEDDING_PROVIDER: openai_compatible" in workflow'),
    ]
    for old, new in replacements:
        replace_exact(b17d, old, new)
    marker = '    assert "EMBEDDING_DIM: ${{ inputs.embedding_dimension }}" in workflow\n'
    addition = marker + '''    assert "default: deepseek-v4-flash" in workflow\n    assert "default: text-embedding-v4" in workflow\n    assert "default: '1024'" in workflow\n    assert "https://dashscope.aliyuncs.com/compatible-mode/v1" in workflow\n    assert "EMBEDDING_BATCH_SIZE: '10'" in workflow\n'''
    replace_exact(b17d, marker, addition)

    b17i = ROOT / "services/agent-service/tests/runtime/test_b17i_production_execution_handoff.py"
    replace_exact(b17i, '"RELEASE_INPUT_EMBEDDING_MODEL": "text-embedding-3-small"', '"RELEASE_INPUT_EMBEDDING_MODEL": "text-embedding-v4"')
    replace_exact(b17i, '"RELEASE_INPUT_EMBEDDING_DIMENSION": "1536"', '"RELEASE_INPUT_EMBEDDING_DIMENSION": "1024"')

    b17g = ROOT / "services/agent-service/tests/runtime/test_b17g_production_execution_readiness.py"
    replace_exact(b17g, '"RELEASE_INPUT_MODEL": "deepseek-chat"', '"RELEASE_INPUT_MODEL": "deepseek-v4-flash"')
    replace_exact(b17g, '"RELEASE_INPUT_EMBEDDING_MODEL": "text-embedding-3-small"', '"RELEASE_INPUT_EMBEDDING_MODEL": "text-embedding-v4"')
    replace_exact(b17g, '"RELEASE_INPUT_EMBEDDING_DIMENSION": "1536"', '"RELEASE_INPUT_EMBEDDING_DIMENSION": "1024"')
    replace_exact(b17g, 'assert result["embedding_dimension"] == 1536', 'assert result["embedding_dimension"] == 1024')

    b17h = ROOT / "services/agent-service/tests/runtime/test_b17h_protected_environment_preflight.py"
    replace_exact(b17h, '"EMBEDDING_PROVIDER": "openai"', '"EMBEDDING_PROVIDER": "openai_compatible"')
    replace_exact(b17h, '"EMBEDDING_API_BASE": "https://api.openai.com/v1"', '"EMBEDDING_API_BASE": "https://dashscope.aliyuncs.com/compatible-mode/v1"')
    replace_exact(b17h, '"EMBEDDING_MODEL": "text-embedding-3-small"', '"EMBEDDING_MODEL": "text-embedding-v4"')
    replace_exact(b17h, '"EMBEDDING_DIM": "1536"', '"EMBEDDING_DIM": "1024"')


def update_manifest() -> int:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("phase manifest files list missing")
    for entry in files:
        path = ROOT / str(entry["path"])
        if not path.is_file():
            raise RuntimeError(f"managed file missing: {entry['path']}")
        data = path.read_bytes()
        entry["size"] = len(data)
        entry["sha256"] = hashlib.sha256(data).hexdigest()
    payload["file_count"] = len(files)
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(files)


def main() -> int:
    patch_env_example()
    patch_embedding_provider_defaults()
    patch_docs()
    patch_tests()
    file_count = update_manifest()
    SELF.unlink()
    print(json.dumps({
        "status": "PASS",
        "chat_provider": "deepseek",
        "chat_model": "deepseek-v4-flash",
        "embedding_provider": "alibaba-model-studio-openai-compatible",
        "embedding_model": "text-embedding-v4",
        "embedding_dimension": 1024,
        "embedding_batch_size": 10,
        "embedding_default_region": "beijing",
        "manifest_file_count": file_count,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
