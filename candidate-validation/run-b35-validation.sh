#!/usr/bin/env bash
set -euo pipefail

source_script='candidate-validation/run-b34-validation.sh'
rendered="${RUNNER_TEMP}/run-b35-validation.sh"
cp "$source_script" "$rendered"

RENDERED="$rendered" python - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["RENDERED"])
text = path.read_text(encoding="utf-8")
text = text.replace('b34-evidence', 'b35-evidence')
text = text.replace(
    '    echo "b34_overlay_sha256=1685bf02375e9f583fc107408fd88e132d5366d3c8932c68ab342865eb9974fc"\n',
    '    echo "b34_overlay_sha256=1685bf02375e9f583fc107408fd88e132d5366d3c8932c68ab342865eb9974fc"\n'
    '    echo "b35_overlay_sha256=8e2b09a7d33c67557c1ada1bf9e1d8728213bfe09bfdd3de2e94e3fb081cff5e"\n',
    1,
)
needle = '''git apply "${RUNNER_TEMP}/b34-overlay.patch"

git diff --name-only HEAD -- services/agent-service/src services/agent-service/tests \\
'''
replacement = '''git apply "${RUNNER_TEMP}/b34-overlay.patch"

cat \\
  candidate-validation/b35-staged-goal-progression/segment-000.b64 \\
  candidate-validation/b35-staged-goal-progression/segment-001.b64 \\
  candidate-validation/b35-staged-goal-progression/segment-002.b64 \\
  candidate-validation/b35-staged-goal-progression/segment-003.b64 \\
  candidate-validation/b35-staged-goal-progression/segment-004.b64 \\
  candidate-validation/b35-staged-goal-progression/segment-005.b64 \\
  candidate-validation/b35-staged-goal-progression/segment-006.b64 \\
  | base64 --decode | gzip -dc > "${RUNNER_TEMP}/b35-overlay.patch"
echo "8e2b09a7d33c67557c1ada1bf9e1d8728213bfe09bfdd3de2e94e3fb081cff5e  ${RUNNER_TEMP}/b35-overlay.patch" \\
  | sha256sum --check --strict
git apply --check "${RUNNER_TEMP}/b35-overlay.patch"
git apply "${RUNNER_TEMP}/b35-overlay.patch"

git diff --name-only HEAD -- services/agent-service/src services/agent-service/tests \\
'''
if text.count(needle) != 1:
    raise SystemExit("unexpected B34 runner overlay insertion point")
text = text.replace(needle, replacement, 1)
text = text.replace(
    '[[ "$(wc -l < "$EVIDENCE_DIR/changed-paths.txt")" -eq 52 ]]',
    '[[ "$(wc -l < "$EVIDENCE_DIR/changed-paths.txt")" -eq 54 ]]',
    1,
)
focused = '  tests/runtime/test_goal_binding_counterexamples.py::test_provider_tool_projection_is_compact_but_runtime_schema_stays_strict\n'
if text.count(focused) != 1:
    raise SystemExit("unexpected focused test insertion point")
replacement_focused = (
    '  tests/runtime/test_goal_binding_counterexamples.py::test_provider_tool_projection_is_compact_but_runtime_schema_stays_strict \\\n'
    '  tests/runtime/test_workflow_runtime.py\n'
)
text = text.replace(focused, replacement_focused, 1)
path.write_text(text, encoding="utf-8")
PY

exec bash "$rendered"
