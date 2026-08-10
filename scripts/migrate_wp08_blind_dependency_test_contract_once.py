from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
A5 = ROOT / "services/agent-service/tests/runtime/test_wp08_attempt5_dependency_authority.py"
A7 = ROOT / "services/agent-service/tests/runtime/test_wp08_attempt7_final_authority_and_retry.py"
BASELINE = ROOT / "skill-system/registry/product-source-baseline.json"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


a5 = A5.read_text(encoding="utf-8")
a5 = replace_once(
    a5,
    '''    with patch("agent_core.config.get_model", return_value=object()), patch(\n        "agent_core.model_calls.invoke_model",\n        return_value=_response({\n            "verdict": "exact",\n            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],\n            "missing_spans": [],\n            "dependency_edges": edges,\n            "reason_code": "all_requested_outcomes_and_dependency_preserved",\n        }),\n    ) as invoke:\n''',
    '''    candidate = _response({\n        "verdict": "exact",\n        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],\n        "missing_spans": [],\n        "dependency_edges": edges,\n        "reason_code": "all_requested_outcomes_and_dependency_preserved",\n    })\n    blind = _response({\n        "verdict": "exact",\n        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],\n        "missing_spans": [],\n        "dependency_decisions": [{\n            "goal_a_id": "g1",\n            "goal_b_id": "g2",\n            "relation": "b_depends_on_a",\n            "basis_kind": "result_reference",\n            "basis_span": "它",\n        }],\n        "reason_code": "all_requested_outcomes_and_dependency_preserved",\n    })\n    with patch("agent_core.config.get_model", return_value=object()), patch(\n        "agent_core.model_calls.invoke_model", side_effect=[candidate, blind]\n    ) as invoke:\n''',
    label="attempt5 true dependency blind response",
)
A5.write_text(a5, encoding="utf-8")

a7 = A7.read_text(encoding="utf-8")
a7 = replace_once(
    a7,
    '''        _response({\n            "verdict": "exact",\n            "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],\n            "missing_spans": [],\n            "dependency_edges": [],\n            "reason_code": "blind_shared_scope_independent",\n        }),\n''',
    '''        _response({\n            "verdict": "exact",\n            "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],\n            "missing_spans": [],\n            "dependency_decisions": [{\n                "goal_a_id": "g1",\n                "goal_b_id": "g2",\n                "relation": "independent",\n            }],\n            "reason_code": "blind_shared_scope_independent",\n        }),\n''',
    label="attempt7 independent blind response",
)
a7 = replace_once(
    a7,
    '''    exact = _response({\n        "verdict": "exact",\n        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],\n        "missing_spans": [],\n        "dependency_edges": [true_edge],\n        "reason_code": "true_result_reference",\n    })\n\n    with patch("agent_core.config.get_model", return_value=object()), patch(\n        "agent_core.model_calls.invoke_model", side_effect=[exact, exact]\n    ) as invoke:\n''',
    '''    candidate = _response({\n        "verdict": "exact",\n        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],\n        "missing_spans": [],\n        "dependency_edges": [true_edge],\n        "reason_code": "true_result_reference",\n    })\n    blind = _response({\n        "verdict": "exact",\n        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],\n        "missing_spans": [],\n        "dependency_decisions": [{\n            "goal_a_id": "g1",\n            "goal_b_id": "g2",\n            "relation": "b_depends_on_a",\n            "basis_kind": "result_reference",\n            "basis_span": "它",\n        }],\n        "reason_code": "true_result_reference",\n    })\n\n    with patch("agent_core.config.get_model", return_value=object()), patch(\n        "agent_core.model_calls.invoke_model", side_effect=[candidate, blind]\n    ) as invoke:\n''',
    label="attempt7 preserve true dependency blind response",
)
a7 = replace_once(
    a7,
    '''        _response({\n            "verdict": "exact",\n            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],\n            "missing_spans": [],\n            "dependency_edges": [true_edge],\n            "reason_code": "blind_true_result_reference",\n        }),\n''',
    '''        _response({\n            "verdict": "exact",\n            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],\n            "missing_spans": [],\n            "dependency_decisions": [{\n                "goal_a_id": "g1",\n                "goal_b_id": "g2",\n                "relation": "b_depends_on_a",\n                "basis_kind": "result_reference",\n                "basis_span": "它",\n            }],\n            "reason_code": "blind_true_result_reference",\n        }),\n''',
    label="attempt7 detect missing dependency blind response",
)
A7.write_text(a7, encoding="utf-8")

payload = json.loads(BASELINE.read_text(encoding="utf-8"))
roots = [str(value) for value in payload.get("protected_roots") or ()]
raw = subprocess.check_output(["git", "ls-files", "-z", "--", *roots], cwd=ROOT)
tracked = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
payload["generated_from"] = "git:" + subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
payload["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
payload["file_count"] = len(tracked)
payload["files"] = {
    relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    for relative in tracked
}
BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

print(json.dumps({"status": "PATCHED", "protected_file_count": len(tracked)}, ensure_ascii=False))
