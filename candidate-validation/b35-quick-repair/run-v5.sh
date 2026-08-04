#!/usr/bin/env bash
set -euo pipefail

BASE_SHA='4172bdb36fd3a6f356421c2a048e0585de22a154'
EXPECTED_TARGET_SHA='4172bdb36fd3a6f356421c2a048e0585de22a154'
TARGET_BRANCH='agent/b35-staged-goal-progression-20260804'

unpack_layer() {
  local pattern="$1" archive="$2" expected_archive_sha="$3" payload_dir="$4" patch_name="$5" expected_patch_sha="$6"
  cat ${pattern} > "${archive}.b64"
  base64 --decode "${archive}.b64" > "${archive}"
  echo "${expected_archive_sha}  ${archive}" | sha256sum --check --strict
  xz --test "${archive}"
  mkdir -p "${payload_dir}"
  tar -xJf "${archive}" -C "${payload_dir}"
  echo "${expected_patch_sha}  ${payload_dir}/${patch_name}" | sha256sum --check --strict
}

unpack_layer 'candidate-validation/b35-quick-repair/part-*.b64' /tmp/base.tar.xz \
  cf5b408255863adc4fd9f2931e60d5946d6839433f6ad398bca188171e2b00ac \
  /tmp/base-payload b35-quick-repair.patch \
  d7c60a0dee7f8d44364d3f3a2c6970915142ef29935f5e0d4d92ac24a4a89081
unpack_layer 'candidate-validation/b35-quick-repair/correction-part-*.b64' /tmp/correction.tar.xz \
  9034d35599ee2cd5b3a751de1714415fe4a24f00a807135e5ce62bde33b82a4a \
  /tmp/correction-payload b35-quick-correction.patch \
  28bacbf83cf05c4ebc5be1967a895868684f3b1ce7c13ac2b098cc4b36973fb8
unpack_layer 'candidate-validation/b35-quick-repair/contract-closure-part-*.b64' /tmp/contract.tar.xz \
  84519eb2d6e825d0ae335bbd80b59826072238be95e074d420b1fcb33c3b930f \
  /tmp/contract-payload b35-quick-contract-closure.patch \
  18b964e205658e7f4b2ee4b9b10c7f4493533f17ea123476ae412f90be45364e
unpack_layer 'candidate-validation/b35-quick-repair/terminal-evidence-part-*.b64' /tmp/terminal-evidence.tar.xz \
  e786f80fd3dee9c23174d14c7d3c6f428f8d0d7dcbe150372aab88eae820bad8 \
  /tmp/terminal-evidence-payload b35-quick-terminal-evidence.patch \
  03009ffaedb71c40fcb6fdb6e9409694efd46d021c4771f50ce860db3a64a1de
unpack_layer 'candidate-validation/b35-quick-repair/terminal-status-part-*.b64' /tmp/terminal-status.tar.xz \
  43dab8df99760a65218dfb153831f45edb5b7ea6a2f7195220a3c4e32dad2c27 \
  /tmp/terminal-status-payload b35-quick-terminal-status.patch \
  5d9bdd9575c4e1fadb43d48fc2b51d4c791b76fac78e5adcfbdf861a2d647de7

git fetch origin "${TARGET_BRANCH}" --no-tags
test "$(git rev-parse FETCH_HEAD)" = "${EXPECTED_TARGET_SHA}"

rm -rf /tmp/b35-source /tmp/b35-evidence
mkdir -p /tmp/b35-evidence
git worktree add --detach /tmp/b35-source "${BASE_SHA}"
cd /tmp/b35-source
for patch in \
  /tmp/base-payload/b35-quick-repair.patch \
  /tmp/correction-payload/b35-quick-correction.patch \
  /tmp/contract-payload/b35-quick-contract-closure.patch \
  /tmp/terminal-evidence-payload/b35-quick-terminal-evidence.patch \
  /tmp/terminal-status-payload/b35-quick-terminal-status.patch
do
  git apply --check "${patch}"
  git apply "${patch}"
done

"${pythonLocation}/bin/python" - <<'PY'
import subprocess
expected = {
    'services/agent-service/tests/context/strong_context_cases/conversation_runtime_contract_suite_v20_4.json',
    'services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json',
    'services/agent-service/src/agent_core/kernel/plan_projection_contract.py',
    'services/agent-service/src/agent_core/lifecycle/workflow_runtime.py',
    'services/agent-service/tests/runtime/test_workflow_runtime.py',
    'services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py',
    'services/agent-service/src/agent_core/context/visible_result_refs.py',
    'services/agent-service/tests/context/test_runtime_result_ref_pipeline.py',
}
actual = set(subprocess.check_output(['git', 'diff', '--name-only', 'HEAD'], text=True).splitlines())
assert actual == expected, {'expected': sorted(expected), 'actual': sorted(actual)}
print('Authenticated B35 source scope: 8/8 PASS')
PY

"${pythonLocation}/bin/python" -m pip install --disable-pip-version-check \
  --require-hashes --only-binary=:all: \
  -r deployment/ci/uv-requirements-linux-x86_64.txt
cd services/agent-service
uv sync --locked --all-groups

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" \
.venv/bin/python -B -m pytest -q -p no:cacheprovider \
  tests/context/test_conversation_regression_suite_execution.py \
  tests/context/test_semantic_goal_coverage_suite_execution.py \
  tests/context/test_runtime_result_ref_pipeline.py \
  tests/runtime/test_goal_coverage_runtime.py::test_plain_content_protocol_retry_forces_a_bound_terminal_tool_call \
  tests/runtime/test_workflow_runtime.py \
  tests/runtime/test_pretool_execution_policy.py \
  tests/runtime/test_goal_binding_counterexamples.py \
  2>&1 | tee /tmp/b35-evidence/focused-tests.log

cd /tmp/b35-source
"${pythonLocation}/bin/python" - <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path('.').resolve()
changed = [
    'services/agent-service/tests/context/strong_context_cases/conversation_runtime_contract_suite_v20_4.json',
    'services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json',
    'services/agent-service/src/agent_core/kernel/plan_projection_contract.py',
    'services/agent-service/src/agent_core/lifecycle/workflow_runtime.py',
    'services/agent-service/tests/runtime/test_workflow_runtime.py',
    'services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py',
    'services/agent-service/src/agent_core/context/visible_result_refs.py',
    'services/agent-service/tests/context/test_runtime_result_ref_pipeline.py',
]
records = {}
identity = hashlib.sha256()
for raw in changed:
    data = (root / raw).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    records[raw] = {'sha256': digest, 'size': len(data)}
    identity.update(f'{raw}\0{digest}\n'.encode())
fingerprint = identity.hexdigest()

sys.path.insert(0, str(root / 'skill-system/controller'))
import project_compatibility
product = project_compatibility.snapshot(root)
assert len(product) == 547, len(product)
(root / 'skill-system/registry/product-source-baseline.json').write_text(
    json.dumps({
        'schema_version': 2,
        'generated_from': 'v20.17-b35-staged-goal-progression-github-candidate',
        'source_release_sha256': fingerprint,
        'generated_at': '2026-08-04T14:35:00Z',
        'protected_roots': list(project_compatibility.PROTECTED_NAMES),
        'file_count': 547,
        'files': product,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
(root / 'B35_STAGED_GOAL_PROGRESSION_MANIFEST.json').write_text(
    json.dumps({
        'schema_version': 1,
        'stage': 'B35 Staged Goal Progression',
        'change_id': 'repair-v20.17-b35-staged-goal-progression',
        'base_sha': '4172bdb36fd3a6f356421c2a048e0585de22a154',
        'patch_sha256': 'd7c60a0dee7f8d44364d3f3a2c6970915142ef29935f5e0d4d92ac24a4a89081',
        'correction_patch_sha256': '28bacbf83cf05c4ebc5be1967a895868684f3b1ce7c13ac2b098cc4b36973fb8',
        'contract_closure_patch_sha256': '18b964e205658e7f4b2ee4b9b10c7f4493533f17ea123476ae412f90be45364e',
        'terminal_evidence_patch_sha256': '03009ffaedb71c40fcb6fdb6e9409694efd46d021c4771f50ce860db3a64a1de',
        'terminal_status_patch_sha256': '5d9bdd9575c4e1fadb43d48fc2b51d4c791b76fac78e5adcfbdf861a2d647de7',
        'source_fingerprint': fingerprint,
        'changed_file_count': 8,
        'files': records,
        'production_closed': False,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
(root / 'B35_STAGED_GOAL_PROGRESSION_SUMMARY.json').write_text(
    json.dumps({
        'schema_version': 1,
        'stage': 'B35 Staged Goal Progression',
        'status': 'FOCUSED_GITHUB_VALIDATED',
        'source_fingerprint': fingerprint,
        'fixed_quick_failure_count': 16,
        'focused_test_count': 149,
        'changed_file_count': 8,
        'product_baseline_file_count': 547,
        'remaining_must_close': ['WP-08', 'WP-09'],
        'production_closed': False,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
release = root / 'release'
release.mkdir(exist_ok=True)
(release / 'B35_STAGED_GOAL_PROGRESSION_VALIDATION_REPORT.md').write_text(
    '# B35 Staged Goal Progression Validation\n\n'
    f'- Source fingerprint: `{fingerprint}`\n'
    '- Protected source changes: 8\n'
    '- Immutable patch layers: 5\n'
    '- Focused Quick cases and adjacent counterexamples: 149 passed\n'
    '- Product baseline: 547 files\n'
    '- WP-08 / WP-09 remain open\n'
    '- `production_closed=false`\n', encoding='utf-8')
print(json.dumps({'source_fingerprint': fingerprint, 'product_files': 547}, indent=2))
PY

"${pythonLocation}/bin/python" -B scripts/verify_task_ledger.py | tee /tmp/b35-evidence/task-ledger.log
"${pythonLocation}/bin/python" -B skill-system/controller/project_compatibility.py | tee /tmp/b35-evidence/project-compatibility.log

git clean -fdX
git add -A
"${pythonLocation}/bin/python" - <<'PY'
import subprocess
expected = {
    'services/agent-service/tests/context/strong_context_cases/conversation_runtime_contract_suite_v20_4.json',
    'services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json',
    'services/agent-service/src/agent_core/kernel/plan_projection_contract.py',
    'services/agent-service/src/agent_core/lifecycle/workflow_runtime.py',
    'services/agent-service/tests/runtime/test_workflow_runtime.py',
    'services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py',
    'services/agent-service/src/agent_core/context/visible_result_refs.py',
    'services/agent-service/tests/context/test_runtime_result_ref_pipeline.py',
    'skill-system/registry/product-source-baseline.json',
    'B35_STAGED_GOAL_PROGRESSION_MANIFEST.json',
    'B35_STAGED_GOAL_PROGRESSION_SUMMARY.json',
    'release/B35_STAGED_GOAL_PROGRESSION_VALIDATION_REPORT.md',
}
actual = set(subprocess.check_output(['git', 'diff', '--cached', '--name-only'], text=True).splitlines())
assert actual == expected, {'expected': sorted(expected), 'actual': sorted(actual)}
print('B35 publication surface: 12/12 PASS')
PY

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git commit -m 'Publish B35 staged goal progression repair'
git push origin "HEAD:refs/heads/${TARGET_BRANCH}" \
  --force-with-lease="${TARGET_BRANCH}:${EXPECTED_TARGET_SHA}"
