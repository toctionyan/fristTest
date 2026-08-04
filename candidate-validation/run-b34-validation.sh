#!/usr/bin/env bash
set -euo pipefail

EVIDENCE_DIR="${RUNNER_TEMP}/b34-evidence"
mkdir -p "$EVIDENCE_DIR"

record_binding() {
  {
    echo "repository=${GITHUB_REPOSITORY:-unknown}"
    echo "commit=${GITHUB_SHA:-unknown}"
    echo "run_attempt=${GITHUB_RUN_ATTEMPT:-unknown}"
    echo "base_ref=${GITHUB_BASE_REF:-unknown}"
    echo "head_ref=${GITHUB_HEAD_REF:-unknown}"
    echo "b33_patch_sha256=af58c9e2262eaaf945b9c893624bc726ac619f832cb962e8209e61da1ca7bd89"
    echo "b34_overlay_sha256=1685bf02375e9f583fc107408fd88e132d5366d3c8932c68ab342865eb9974fc"
    echo "capability_gate_merged_sha256=ac55da9e9395971da39ba9d92b799dacc70f24430d889e430a48dc0bdd589c6b"
    echo "app_profile=${APP_PROFILE:-unset}"
    echo "pythonpath=${PYTHONPATH:-unset}"
    echo "production_closed=false"
  } > "$EVIDENCE_DIR/run-binding.txt"
}
trap record_binding EXIT

target='services/agent-service/src/agent_core/runtime/capability_gate.py'
known_whitespace='services/agent-service/tests/runtime/test_stage1_known_architecture_gaps.py'

cat \
  candidate-validation/b33-runtime-patch/part-000.b64 \
  candidate-validation/b33-runtime-patch/part-001.b64 \
  candidate-validation/b33-runtime-patch/part-002.b64 \
  candidate-validation/b33-runtime-patch/part-003.b64 \
  candidate-validation/b33-runtime-patch/part-004.b64 \
  candidate-validation/b33-runtime-patch/part-005.b64 \
  candidate-validation/b33-runtime-patch/part-006-seg-000.b64 \
  candidate-validation/b33-runtime-patch/part-006-seg-001.b64 \
  candidate-validation/b33-runtime-patch/part-006-seg-002.b64 \
  candidate-validation/b33-runtime-patch/part-006-seg-003.b64 \
  candidate-validation/b33-runtime-patch/part-007.b64 \
  candidate-validation/b33-runtime-patch/part-008.b64 \
  | base64 --decode | gzip -dc > "${RUNNER_TEMP}/b33-runtime.patch"
echo "af58c9e2262eaaf945b9c893624bc726ac619f832cb962e8209e61da1ca7bd89  ${RUNNER_TEMP}/b33-runtime.patch" \
  | sha256sum --check --strict

set +e
git apply --3way "${RUNNER_TEMP}/b33-runtime.patch"
apply_rc=$?
set -e
unmerged="$(git diff --name-only --diff-filter=U)"
if [[ -n "$unmerged" ]]; then
  [[ "$unmerged" == "$target" ]]
  TARGET="$target" python - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["TARGET"])
text = path.read_text(encoding="utf-8")
conflict = '''<<<<<<< ours
        len(latest_release_scope_keys) > 1
        and mode in {"collection", "artifact"}
=======
        len(latest_handles) > 1
        and (mode in {"collection", "artifact"} or (mode == "pipeline" and str(target.get("source_kind") or "") == "collection"))
>>>>>>> theirs'''
resolved = '''        len(latest_release_scope_keys) > 1
        and (mode in {"collection", "artifact"} or (mode == "pipeline" and str(target.get("source_kind") or "") == "collection"))'''
if text.count(conflict) != 1:
    raise SystemExit("unexpected capability_gate conflict shape")
path.write_text(text.replace(conflict, resolved, 1), encoding="utf-8")
PY
  git add "$target"
  [[ -z "$(git diff --name-only --diff-filter=U)" ]]
  {
    echo 'status=explicitly_resolved'
    echo "path=${target}"
    echo "initial_apply_rc=${apply_rc}"
    echo 'resolution=repository_release_scope_plus_b33_pipeline_frontier'
  } > "$EVIDENCE_DIR/merge-status.txt"
else
  [[ "$apply_rc" -eq 0 ]]
  echo 'status=clean_three_way_apply' > "$EVIDENCE_DIR/merge-status.txt"
fi
echo "ac55da9e9395971da39ba9d92b799dacc70f24430d889e430a48dc0bdd589c6b  ${target}" \
  | sha256sum --check --strict

TARGET="$known_whitespace" python - <<'PY'
import os
from pathlib import Path
path = Path(os.environ["TARGET"])
path.write_bytes(path.read_bytes().rstrip() + b"\n")
PY
echo "fa1204cdb888aede72d73544eaf38dd5742ef16fb82686d96526ce82674944c6  ${known_whitespace}" \
  | sha256sum --check --strict
git add "$known_whitespace"

cat \
  candidate-validation/b34-regression-overlay/part-000.b64 \
  candidate-validation/b34-regression-overlay/part-001.b64 \
  | base64 --decode | gzip -dc > "${RUNNER_TEMP}/b34-overlay.patch"
echo "1685bf02375e9f583fc107408fd88e132d5366d3c8932c68ab342865eb9974fc  ${RUNNER_TEMP}/b34-overlay.patch" \
  | sha256sum --check --strict
git apply --check "${RUNNER_TEMP}/b34-overlay.patch"
git apply "${RUNNER_TEMP}/b34-overlay.patch"

git diff --name-only HEAD -- services/agent-service/src services/agent-service/tests \
  | LC_ALL=C sort > "$EVIDENCE_DIR/changed-paths.txt"
[[ "$(wc -l < "$EVIDENCE_DIR/changed-paths.txt")" -eq 52 ]]
git diff --check HEAD

python -m pip install --disable-pip-version-check --require-hashes --only-binary=:all: \
  -r deployment/ci/uv-requirements-linux-x86_64.txt

pushd services/agent-service >/dev/null
uv sync --locked --all-groups
uv run pytest -q \
  --junitxml="$EVIDENCE_DIR/focused-junit.xml" \
  tests/runtime/test_stage1_known_architecture_gaps.py \
  tests/runtime/test_stage2_p0_safety_boundaries.py \
  tests/runtime/test_stage3_entity_authority.py \
  tests/runtime/test_stage4_goal_output_refs.py \
  tests/runtime/test_stage5_controlled_target_dsl.py \
  tests/runtime/test_pretool_execution_policy.py \
  tests/runtime/test_capability_target_schema.py \
  tests/runtime/test_goal_binding_counterexamples.py::test_provider_tool_projection_is_compact_but_runtime_schema_stays_strict
uv run pytest -q \
  -m "not integration and not preprod" \
  --junitxml="$EVIDENCE_DIR/agent-regression-junit.xml"
popd >/dev/null

python -B scripts/verify_architecture.py
python -B scripts/verify_module_closure.py
python -B scripts/verify_version_consistency.py
services/agent-service/.venv/bin/python -m compileall -q services/agent-service/src

echo 'status=passed' > "$EVIDENCE_DIR/final-status.txt"
