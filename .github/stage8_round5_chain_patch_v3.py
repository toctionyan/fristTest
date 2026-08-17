from __future__ import annotations

from pathlib import Path

helper = Path(__file__).with_name("stage8_round5_chain_patch_v2.py")
source = helper.read_text(encoding="utf-8")
old = '''for repository_path in (\n    "services/agent-service/src/agent_core/persistence/action_lifecycle_store.py",\n    "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py",\n):\n    replace_once(\n        repository_path,\n        '("Draft_missing" in reservation_reason)',\n        'reservation_reason.startswith("reservation_canonical_Draft_")',\n        label=f"{repository_path} stale canonical Draft Grant revocation",\n    )\n'''
new = '''replace_once(\n    "services/agent-service/src/agent_core/persistence/action_lifecycle_store.py",\n    '("Draft_missing" in reservation_reason)',\n    'reservation_reason.startswith("reservation_canonical_Draft_")',\n    label="sqlite stale canonical Draft Grant revocation",\n)\nreplace_once(\n    "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py",\n    '"Draft_missing" in reservation_reason',\n    'reservation_reason.startswith("reservation_canonical_Draft_")',\n    label="sqlalchemy stale canonical Draft Grant revocation",\n)\n'''
if source.count(old) != 1:
    raise SystemExit(f"v2 repository-variant block mismatch: {source.count(old)}")
source = source.replace(old, new)
namespace = {"__name__": "__main__", "__file__": str(helper)}
exec(compile(source, str(helper), "exec"), namespace, namespace)
