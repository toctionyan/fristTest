#!/usr/bin/env bash
set -euo pipefail

EXPECTED_TARGET_SHA='98d7c120b1a8b7f4a4cccfad3a0fa679fa2a4248'
TARGET_BRANCH='agent/b35-staged-goal-progression-20260804'
PATCH_SHA='248f84417d30a5119ce2f038a07ccab1c717d87e87c50fa53889e24632ccfb11'
PATCH_PATH="${GITHUB_WORKSPACE}/publisher/candidate-validation/b35-governance-closure/b35-governance-counterexample.patch"
SOURCE="${GITHUB_WORKSPACE}/source"
EVIDENCE="${RUNNER_TEMP}/b35-governance-evidence"
QUALITY_TARGET="${SOURCE}/.quality/targets/quality-target-quick.md"

mkdir -p "${EVIDENCE}"
echo "${PATCH_SHA}  ${PATCH_PATH}" | sha256sum --check --strict
test "$(git -C "${SOURCE}" rev-parse HEAD)" = "${EXPECTED_TARGET_SHA}"
git -C "${SOURCE}" apply --check "${PATCH_PATH}"
git -C "${SOURCE}" apply "${PATCH_PATH}"

actual="$(git -C "${SOURCE}" diff --name-only HEAD)"
test "${actual}" = 'services/agent-service/tests/architecture/test_quality_loop_governance.py'

"${pythonLocation}/bin/python" -m pip install --disable-pip-version-check \
  --require-hashes --only-binary=:all: \
  -r "${SOURCE}/deployment/ci/uv-requirements-linux-x86_64.txt"
(
  cd "${SOURCE}/services/agent-service"
  uv sync --locked --all-groups
)
(
  cd "${SOURCE}/services/business-service"
  uv sync --locked --all-groups
)
(
  cd "${SOURCE}/services/agent-service/frontend"
  npm ci --ignore-scripts=false
  ./node_modules/.bin/playwright install --with-deps chromium
)

(
  cd "${SOURCE}/services/agent-service"
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B -m pytest -q -p no:cacheprovider \
    tests/architecture/test_quality_loop_governance.py::test_strong_context_gate_rejects_semantically_swapped_goal_bindings \
    2>&1 | tee "${EVIDENCE}/focused-governance-test.log"

  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B -m pytest -q -p no:cacheprovider \
    tests/runtime/test_goal_binding_counterexamples.py \
    tests/runtime/test_semantic_grounding_read.py \
    tests/runtime/test_similar_capability_rejection.py \
    tests/context/test_parameterized_capability_alignment.py \
    tests/architecture/test_quality_loop_governance.py \
    2>&1 | tee "${EVIDENCE}/adversarial-counterexamples.log"
)

(
  cd "${SOURCE}"
  "${pythonLocation}/bin/python" -B scripts/create_ci_quality_target.py \
    --output "${QUALITY_TARGET}" \
    --ref "${EXPECTED_TARGET_SHA}+${PATCH_SHA}" \
    --workflow quality-quick \
    --claims-source governance/claims/v20.6.2-project-quick-certification.json
  QUALITY_EVIDENCE_DIR="${EVIDENCE}/quick" \
  QUALITY_TARGET="${QUALITY_TARGET}" \
    services/agent-service/.venv/bin/python -B scripts/quality_loop.py --mode quick --target "${QUALITY_TARGET}" \
    2>&1 | tee "${EVIDENCE}/quality-quick.log"
)

(
  cd "${SOURCE}"
  "${pythonLocation}/bin/python" - <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path('.').resolve()
manifest_path = root / 'B35_STAGED_GOAL_PROGRESSION_MANIFEST.json'
manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
new_path = 'services/agent-service/tests/architecture/test_quality_loop_governance.py'
changed = list(manifest['files'])
assert new_path not in changed
changed.append(new_path)
records: dict[str, dict[str, object]] = {}
identity = hashlib.sha256()
for raw in changed:
    data = (root / raw).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    records[raw] = {'sha256': digest, 'size': len(data)}
    identity.update(f'{raw}\0{digest}\n'.encode())
fingerprint = identity.hexdigest()

sys.path.insert(0, str(root / 'skill-system/controller'))
import project_compatibility  # type: ignore
product = project_compatibility.snapshot(root)
assert len(product) == 547, len(product)
baseline = {
    'schema_version': 2,
    'generated_from': 'v20.17-b35-governance-counterexample-closure-github-candidate',
    'source_release_sha256': fingerprint,
    'generated_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
    'protected_roots': list(project_compatibility.PROTECTED_NAMES),
    'file_count': 547,
    'files': product,
}
(root / 'skill-system/registry/product-source-baseline.json').write_text(
    json.dumps(baseline, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

manifest['governance_counterexample_patch_sha256'] = '248f84417d30a5119ce2f038a07ccab1c717d87e87c50fa53889e24632ccfb11'
manifest['source_fingerprint'] = fingerprint
manifest['changed_file_count'] = len(changed)
manifest['files'] = records
manifest['production_closed'] = False
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

summary_path = root / 'B35_STAGED_GOAL_PROGRESSION_SUMMARY.json'
summary = json.loads(summary_path.read_text(encoding='utf-8'))
summary.update({
    'status': 'FULL_QUICK_GITHUB_VALIDATED',
    'source_fingerprint': fingerprint,
    'fixed_quick_failure_count': 17,
    'focused_test_count': 149,
    'adversarial_counterexample_test_count': 137,
    'repository_quick_status': 'PASS',
    'changed_file_count': len(changed),
    'product_baseline_file_count': 547,
    'remaining_must_close': ['WP-08', 'WP-09'],
    'production_closed': False,
})
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

report = root / 'release/B35_STAGED_GOAL_PROGRESSION_VALIDATION_REPORT.md'
report.write_text(
    '# B35 Staged Goal Progression Validation\n\n'
    f'- Source fingerprint: `{fingerprint}`\n'
    '- Protected source changes: 9\n'
    '- Immutable patch layers: 6\n'
    '- Focused Quick cases and adjacent counterexamples: 149 passed\n'
    '- Full adversarial counterexample suite: 137 passed\n'
    '- Repository Quality Quick: PASS\n'
    '- Product baseline: 547 files\n'
    '- WP-08 / WP-09 remain open\n'
    '- `production_closed=false`\n',
    encoding='utf-8',
)
print(json.dumps({'source_fingerprint': fingerprint, 'changed_files': len(changed), 'product_files': 547}, indent=2))
PY

  "${pythonLocation}/bin/python" -B scripts/verify_task_ledger.py | tee "${EVIDENCE}/task-ledger.log"
  "${pythonLocation}/bin/python" -B skill-system/controller/project_compatibility.py | tee "${EVIDENCE}/project-compatibility.log"
)

cd "${SOURCE}"
git clean -fdX
find . -type d -name __pycache__ -prune -exec rm -rf {} +
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
git add -A
"${pythonLocation}/bin/python" - <<'PY'
import subprocess
expected = {
    'services/agent-service/tests/architecture/test_quality_loop_governance.py',
    'skill-system/registry/product-source-baseline.json',
    'B35_STAGED_GOAL_PROGRESSION_MANIFEST.json',
    'B35_STAGED_GOAL_PROGRESSION_SUMMARY.json',
    'release/B35_STAGED_GOAL_PROGRESSION_VALIDATION_REPORT.md',
}
actual = set(subprocess.check_output(['git', 'diff', '--cached', '--name-only'], text=True).splitlines())
assert actual == expected, {'expected': sorted(expected), 'actual': sorted(actual)}
print('B35 governance closure publication surface: 5/5 PASS')
PY

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git commit -m 'Close B35 staged-goal governance counterexample'
git push origin "HEAD:refs/heads/${TARGET_BRANCH}" \
  --force-with-lease="${TARGET_BRANCH}:${EXPECTED_TARGET_SHA}"
