#!/usr/bin/env bash
set -u

out_dir="${1:?evidence output directory required}"
mkdir -p "$out_dir/isolated" "$out_dir/source-snapshots"

nodes=(
  'tests/context/test_model_paraphrase_benchmark.py::ModelParaphraseBenchmarkTests::test_benchmark_calls_model_client_for_each_phase'
  'tests/test_dialogue_model_modes.py::DialogueModelModeTests::test_real_model_mode_requires_credentials'
  'tests/test_goal_binding_validator.py::GoalBindingValidatorTests::test_effect_scope_is_enforced'
  'tests/test_kernel_contracts.py::KernelContractTests::test_module_registry_matches_execution_tool_registry'
  'tests/test_runtime_config.py::RuntimeConfigTests::test_from_env_rejects_missing_real_model_credentials'
)

: > "$out_dir/isolated/summary.txt"
index=0
for node in "${nodes[@]}"; do
  index=$((index + 1))
  log="$out_dir/isolated/test-${index}.log"
  set +e
  uv run pytest -vv "$node" >"$log" 2>&1
  rc=$?
  set -e
  printf '%s\trc=%s\t%s\n' "$index" "$rc" "$node" >> "$out_dir/isolated/summary.txt"
done

set +e
uv run pytest -vv "${nodes[@]}" > "$out_dir/isolated/combined.log" 2>&1
combined_rc=$?
set -e
printf 'combined_rc=%s\n' "$combined_rc" >> "$out_dir/isolated/summary.txt"

for file in \
  tests/context/test_model_paraphrase_benchmark.py \
  tests/test_dialogue_model_modes.py \
  tests/test_goal_binding_validator.py \
  tests/test_kernel_contracts.py \
  tests/test_runtime_config.py; do
  if [[ -f "$file" ]]; then
    cp "$file" "$out_dir/source-snapshots/$(echo "$file" | tr '/' '_')"
  fi
done

grep -RIn --exclude-dir='.venv' --exclude-dir='__pycache__' \
  -E 'class RuntimeConfig|def from_env|RuntimeConfigError|GOAL_EFFECT_SCOPE_MISMATCH|module_registry_matches_execution_tool_registry|model_paraphrase|calls\["count"\]' \
  src tests > "$out_dir/source-snapshots/relevant-symbols.txt" 2>&1 || true

uv run python - <<'PY' > "$out_dir/isolated/environment-presence.txt"
import os
for key in sorted(os.environ):
    upper = key.upper()
    if any(token in upper for token in ('MODEL', 'OPENAI', 'DEEPSEEK', 'EMBEDDING', 'API_KEY', 'PROVIDER')):
        print(f"{key}=<set>" if os.environ.get(key) else f"{key}=<empty>")
PY

exit 0
