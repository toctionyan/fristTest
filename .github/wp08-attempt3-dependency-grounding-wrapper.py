from __future__ import annotations

import site
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / ".github/wp08-attempt3-dependency-grounding-fix.py"
DIALOGUE = ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
SMOKE = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


completed = subprocess.run(
    [sys.executable, str(CORE)],
    cwd=ROOT,
    text=True,
    capture_output=True,
    check=False,
)
if completed.stdout:
    print(completed.stdout, end="")
if completed.stderr:
    print(completed.stderr, end="", file=sys.stderr)
known_partial = "dialogue-structured-dependency-rule: expected exactly one anchor, found 0"
if completed.returncode not in {0, 1}:
    raise SystemExit(completed.returncode)
if completed.returncode == 1 and known_partial not in (completed.stdout + completed.stderr):
    raise SystemExit("dependency core failed before the known prompt-only anchor")

# Prove the core structural changes were written before accepting the known
# prompt-anchor miss. This wrapper is temporary carrier plumbing only.
for path, needle in (
    (ROOT / "services/agent-service/src/agent_core/lifecycle/protocol.py", "same_turn_literal_scope"),
    (ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py", "_normalize_current_turn_dependency_grounding"),
    (ROOT / "services/agent-service/src/agent_core/lifecycle/goal_granularity.py", "_effective_blind_dependency_edges"),
    (ROOT / "services/agent-service/src/agent_core/lifecycle/semantic_contract.py", '"dependency_bindings"'),
):
    if needle not in path.read_text(encoding="utf-8"):
        raise SystemExit(f"dependency core proof missing: {path}:{needle}")

if completed.returncode == 1:
    dialogue = DIALOGUE.read_text(encoding="utf-8")
    anchor = (
        "- 一句话有多个目标、查询后再查询、查询后再动作、多个动作或长流程时，先按用户可以独立判断是否完成的业务效果拆 Goal；不要按接口或 Tool 数量拆 Goal。"
        "筛选、排序、数量、原因、权限检查、政策读取、Draft 与展示步骤都不是独立 Goal，除非用户明确把它们作为可单独验收的业务结果。"
        "一个 Goal 可以由多个 Tool 完成，多个 Goal 也可由一个综合 Tool 完成；每个 Goal 用开放 requested_effect 表达，系统没有能力时仍保留原 Goal。\n"
    )
    addition = (
        "- 当前轮 Goal 的 depends_on 只表示真实的当前轮结果依赖，不表示执行顺序。每个 Goal 都必须填写 dependency_bindings；没有 depends_on 时为空数组。"
        "对象就在本 Goal 局部 evidence_span 内时用 target_binding.source=local_literal；本 Goal 省略对象、但同轮兄弟 Goal 的局部 evidence_span 已逐字写出可复用对象/范围时，用 same_turn_literal_scope 并引用该 source_goal_id，这表示复用原文字面范围而不是依赖兄弟 Goal 的执行结果。"
        "只有本 Goal 局部原文真正指向前一个尚未完成 Goal 的未来结果时才用 current_turn_goal_output，并与 depends_on 对齐。执行时为把已明示描述解析成 ID/artifact handle 所做的读取只是支持数据流，不能制造语义依赖；不要伪造 input/completion dependency binding。\n"
    )
    dialogue = replace_once(dialogue, anchor, anchor + addition, label="dialogue-structured-dependency-rule-v2")
    DIALOGUE.write_text(dialogue, encoding="utf-8")

    smoke = SMOKE.read_text(encoding="utf-8")
    old = (
        '            "同一当前轮中后续目标依赖前一目标时只用 depends_on；reference_expression 只用于已经在更早轮次向客户展示的历史结果，"\n'
        '            "不能引用本轮尚未执行目标的未来结果。"\n'
    )
    new = (
        '            "同一当前轮中后续目标依赖前一目标时只用 depends_on；reference_expression 只用于已经在更早轮次向客户展示的历史结果，"\n'
        '            "不能引用本轮尚未执行目标的未来结果。每个 Goal 必须填写 dependency_bindings；没有 depends_on 时为空数组。"\n'
        '            "对象就在本 Goal 局部原文时 target_binding 用 local_literal；本 Goal 省略对象、但同轮兄弟 Goal 的局部原文已逐字写出复用对象/范围时用 same_turn_literal_scope，并引用该 source_goal_id，不能因此填写 depends_on；"\n'
        '            "只有本 Goal 的局部原文真正指向前一 Goal 尚未产生的未来结果时才用 current_turn_goal_output，并与 depends_on/target dependency binding 对齐。"\n'
    )
    smoke = replace_once(smoke, old, new, label="smoke-structured-dependency-rule-v2")
    SMOKE.write_text(smoke, encoding="utf-8")

# The registered Quality job launches focused pytest from repository root.
# Existing Agent tests assume the service root and src tree are importable.
# Persist only these exact repository-local paths into the temporary carrier
# virtualenv so subsequent test subprocesses use the same import topology as
# normal Agent test invocations. This .pth file is runner-local, never product.
agent_root = ROOT / "services" / "agent-service"
site_dirs = [Path(value) for value in site.getsitepackages() if value]
if not site_dirs:
    raise SystemExit("unable to resolve carrier virtualenv site-packages")
pth = site_dirs[0] / "wp08_attempt3_stage1_agent_paths.pth"
pth.write_text(f"{agent_root}\n{agent_root / 'src'}\n", encoding="utf-8")
print(f"Stage1 test import bootstrap: {pth}")

print("Attempt-3 dependency grounding core + prompt finish applied")
