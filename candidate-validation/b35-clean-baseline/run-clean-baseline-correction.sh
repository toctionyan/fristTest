#!/usr/bin/env bash
set -euo pipefail

EXPECTED_TARGET_SHA='adb93c028931981ef0d2c9bdef02554aaba8f895'
TARGET_BRANCH='agent/b35-staged-goal-progression-20260804'
EXPECTED_SOURCE_FINGERPRINT='2418db8c897cbb91b5f5b4cc0b8028cc3efec60dfc43c5114802547e9c544047'
RUNTIME_VECTOR_DB='services/agent-service/runtime/vector-store/vector_store.db'
SOURCE="${GITHUB_WORKSPACE}/source"
EVIDENCE="${RUNNER_TEMP}/b35-clean-baseline-evidence"
QUALITY_TARGET="${SOURCE}/.quality/targets/quality-target-quick.md"
GENERATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export EXPECTED_SOURCE_FINGERPRINT GENERATED_AT

mkdir -p "${EVIDENCE}"
test "$(git -C "${SOURCE}" rev-parse HEAD)" = "${EXPECTED_TARGET_SHA}"

cd "${SOURCE}"
"${pythonLocation}/bin/python" -B - <<'PY'
import json
import os
from pathlib import Path

root = Path('.').resolve()
baseline = json.loads((root / 'skill-system/registry/product-source-baseline.json').read_text(encoding='utf-8'))
manifest = json.loads((root / 'B35_STAGED_GOAL_PROGRESSION_MANIFEST.json').read_text(encoding='utf-8'))
summary = json.loads((root / 'B35_STAGED_GOAL_PROGRESSION_SUMMARY.json').read_text(encoding='utf-8'))
runtime_path = 'services/agent-service/runtime/vector-store/vector_store.db'
assert baseline['file_count'] == 548, baseline['file_count']
assert runtime_path in baseline['files'], runtime_path
assert not (root / runtime_path).exists(), runtime_path
assert manifest['source_fingerprint'] == os.environ['EXPECTED_SOURCE_FINGERPRINT']
assert summary['product_baseline_file_count'] == 548
print(json.dumps({
    'status': 'STALE_RUNTIME_ARTIFACT_BASELINE_CONFIRMED',
    'baseline_file_count': baseline['file_count'],
    'missing_runtime_artifact': runtime_path,
}, indent=2))
PY

# Start from the actual tracked tree, not a test-populated workspace.
git clean -fdx

write_clean_baseline() {
  PYTHONDONTWRITEBYTECODE=1 "${pythonLocation}/bin/python" -B - <<'PY'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

root = Path('.').resolve()
runtime_path = root / 'services/agent-service/runtime/vector-store/vector_store.db'
assert not runtime_path.exists(), runtime_path

manifest = json.loads((root / 'B35_STAGED_GOAL_PROGRESSION_MANIFEST.json').read_text(encoding='utf-8'))
assert manifest['source_fingerprint'] == os.environ['EXPECTED_SOURCE_FINGERPRINT']

sys.path.insert(0, str(root / 'skill-system/controller'))
import project_compatibility  # type: ignore
product = project_compatibility.snapshot(root)
assert len(product) == 547, len(product)
assert 'services/agent-service/runtime/vector-store/vector_store.db' not in product

baseline = {
    'schema_version': 2,
    'generated_from': 'v20.17-b35-clean-tree-product-baseline-candidate',
    'source_release_sha256': os.environ['EXPECTED_SOURCE_FINGERPRINT'],
    'generated_at': os.environ['GENERATED_AT'],
    'protected_roots': list(project_compatibility.PROTECTED_NAMES),
    'file_count': 547,
    'files': product,
}
(root / 'skill-system/registry/product-source-baseline.json').write_text(
    json.dumps(baseline, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

summary_path = root / 'B35_STAGED_GOAL_PROGRESSION_SUMMARY.json'
summary = json.loads(summary_path.read_text(encoding='utf-8'))
summary['product_baseline_file_count'] = 547
summary['clean_tree_product_baseline_status'] = 'PASS'
summary['production_closed'] = False
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

report_path = root / 'release/B35_STAGED_GOAL_PROGRESSION_VALIDATION_REPORT.md'
report = report_path.read_text(encoding='utf-8')
assert report.count('- Product baseline: 548 files') == 1
report = report.replace('- Product baseline: 548 files', '- Product baseline: 547 files')
if '- Clean-tree product baseline (runtime artifacts excluded): PASS' not in report:
    report = report.replace(
        '- Product baseline: 547 files\n',
        '- Product baseline: 547 files\n- Clean-tree product baseline (runtime artifacts excluded): PASS\n',
    )
report_path.write_text(report, encoding='utf-8')
print(json.dumps({'clean_product_files': 547, 'source_fingerprint': os.environ['EXPECTED_SOURCE_FINGERPRINT']}, indent=2))
PY
}

write_clean_baseline
PYTHONDONTWRITEBYTECODE=1 "${pythonLocation}/bin/python" -B skill-system/controller/project_compatibility.py \
  | tee "${EVIDENCE}/pre-quick-project-compatibility.log"

"${pythonLocation}/bin/python" -m pip install --disable-pip-version-check \
  --require-hashes --only-binary=:all: \
  -r deployment/ci/uv-requirements-linux-x86_64.txt
(
  cd services/agent-service
  uv sync --locked --all-groups
)
(
  cd services/business-service
  uv sync --locked --all-groups
)
(
  cd services/agent-service/frontend
  npm ci --ignore-scripts=false
  ./node_modules/.bin/playwright install --with-deps chromium
)

"${pythonLocation}/bin/python" -B scripts/create_ci_quality_target.py \
  --output "${QUALITY_TARGET}" \
  --ref 'b35-clean-baseline-2418db8c897cbb91' \
  --workflow quality-quick \
  --claims-source governance/claims/v20.6.2-project-quick-certification.json
QUALITY_EVIDENCE_DIR="${EVIDENCE}/quick" \
QUALITY_TARGET="${QUALITY_TARGET}" \
  services/agent-service/.venv/bin/python -B scripts/quality_loop.py --mode quick --target "${QUALITY_TARGET}" \
  2>&1 | tee "${EVIDENCE}/quality-quick.log"

# Remove all ignored and untracked test/runtime output, then rebuild the baseline
# from the exact tree that will be committed.
git clean -fdx
write_clean_baseline

PYTHONDONTWRITEBYTECODE=1 "${pythonLocation}/bin/python" -B scripts/verify_task_ledger.py \
  | tee "${EVIDENCE}/task-ledger.log"
PYTHONDONTWRITEBYTECODE=1 "${pythonLocation}/bin/python" -B skill-system/controller/project_compatibility.py \
  | tee "${EVIDENCE}/final-project-compatibility.log"

test ! -e "${RUNTIME_VECTOR_DB}"
git add -A
"${pythonLocation}/bin/python" -B - <<'PY'
import subprocess
expected = {
    'skill-system/registry/product-source-baseline.json',
    'B35_STAGED_GOAL_PROGRESSION_SUMMARY.json',
    'release/B35_STAGED_GOAL_PROGRESSION_VALIDATION_REPORT.md',
}
actual = set(subprocess.check_output(['git', 'diff', '--cached', '--name-only'], text=True).splitlines())
assert actual == expected, {'expected': sorted(expected), 'actual': sorted(actual)}
print('B35 clean baseline publication surface: 3/3 PASS')
PY

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git commit -m 'Correct B35 clean-tree product baseline'
git push origin "HEAD:refs/heads/${TARGET_BRANCH}" \
  --force-with-lease="${TARGET_BRANCH}:${EXPECTED_TARGET_SHA}"
