from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

RUNNER_TEMP = Path(os.environ["RUNNER_TEMP"])
WORKSPACE = Path(os.environ["GITHUB_WORKSPACE"])
ROOT = Path("/tmp/v2018-baseline")
SOURCE_COMMIT = "b1c71a6ed37e85cf7ff834368787511a6efd1f9f"
OLD_WORKFLOW = ".github/workflows/tmp-v20.18-governance-recovery-diff-review.yml"
BASELINE_SHA = "aeb9a445001d4922e13a032e4cccc12f8ff34e9a"
CANDIDATE_SHA = "55403a01f957257fbbefead32bcde21b7d866001"
PERF_SHA = "7e6bd3bf65718c13f1bcbc28011cb4071b8c96a8"
SUCCESSOR = "migration-v20.18-semantic-single-writer-output-coverage-r1"
PERF_PATH = "services/agent-service/src/agent_core/runtime/capability_effects.py"
EXPECTED_CHANGED = sorted([
    "services/agent-service/src/agent_core/kernel/semantic_contract.py",
    "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
    "services/agent-service/src/agent_core/lifecycle/goal_granularity.py",
    "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
    "services/agent-service/src/agent_core/lifecycle/protocol.py",
    "services/agent-service/src/agent_core/lifecycle/semantic_contract.py",
    "services/agent-service/src/agent_core/modules/contracts.py",
    "services/agent-service/src/agent_core/modules/registry.py",
    "services/agent-service/src/agent_core/runtime/capability_effects.py",
    "services/agent-service/src/agent_modules/ecommerce/module.py",
    "services/agent-service/src/agent_modules/ecommerce/semantic_vocabulary.py",
    "services/agent-service/tests/architecture/test_semantic_single_writer_invariants.py",
    "services/agent-service/tests/runtime/test_semantic_output_coverage.py",
    "services/agent-service/tests/runtime/test_unified_semantic_planning_contract.py",
])


def git_show(spec: str) -> str:
    return subprocess.check_output(["git", "show", spec], cwd=WORKSPACE, text=True)


TEXT = git_show(f"{SOURCE_COMMIT}:{OLD_WORKFLOW}")
RECORDS: list[dict[str, object]] = []


def extract_run(name: str) -> str:
    marker = f"      - name: {name}\n"
    start = TEXT.index(marker)
    end = TEXT.find("\n      - name:", start + len(marker))
    if end < 0:
        end = len(TEXT)
    block = TEXT[start:end]
    run_marker = "        run: |\n"
    pos = block.index(run_marker) + len(run_marker)
    raw = block[pos:]
    lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith("          "):
            lines.append(line[10:])
        elif not line.strip():
            lines.append("")
        else:
            raise AssertionError(f"unexpected run indentation in {name}: {line!r}")
    return "\n".join(lines).rstrip() + "\n"


def persist() -> None:
    (RUNNER_TEMP / "recovery-h2-execution.json").write_text(
        json.dumps(
            {
                "baseline_sha": BASELINE_SHA,
                "candidate_sha": CANDIDATE_SHA,
                "performance_repair_sha": PERF_SHA,
                "steps": RECORDS,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def run_script(index: int, name: str, cwd: Path, script: str) -> None:
    path = RUNNER_TEMP / f"recovery-h2-{index:02d}.sh"
    path.write_text(script, encoding="utf-8")
    print(f"::group::Recovery-H2 {index:02d} {name}", flush=True)
    started = time.monotonic()
    proc = subprocess.run(["bash", str(path)], cwd=cwd, env=os.environ.copy())
    duration_ms = int((time.monotonic() - started) * 1000)
    print("::endgroup::", flush=True)
    RECORDS.append(
        {
            "index": index,
            "name": name,
            "cwd": str(cwd),
            "exit_code": proc.returncode,
            "duration_ms": duration_ms,
        }
    )
    persist()
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def run_command(index: int, name: str, cwd: Path, argv: list[str], log: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    print(f"::group::Recovery-H2 {index:02d} {name}", flush=True)
    started = time.monotonic()
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env or os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    if log is not None:
        log.write_text(proc.stdout or "", encoding="utf-8", errors="replace")
    print("::endgroup::", flush=True)
    RECORDS.append(
        {
            "index": index,
            "name": name,
            "cwd": str(cwd),
            "exit_code": proc.returncode,
            "duration_ms": duration_ms,
        }
    )
    persist()
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)
    return proc


INITIAL = [
    ("Reconstruct exact predecessor, pre-write baseline, and verified replan controller", WORKSPACE),
    ("Replan invalid predecessor and restore successor authority onto exact baseline", Path("/tmp/v2018-predecessor")),
    ("Restore immutable PR551 RED oracle into the common test view", WORKSPACE),
    ("Generate successor Target, Claim and Decision and initialize successor Contract", ROOT),
    ("Bootstrap and install locked validation environments", ROOT),
    ("Reproduce successor machine RED baseline and exact failed Claim", ROOT),
    ("Create and validate successor repair-governance case", ROOT),
    ("Issue fresh successor ChangePermit and enter implementing state", ROOT),
]

for index, (name, cwd) in enumerate(INITIAL, start=1):
    script = extract_run(name)
    if name == "Create and validate successor repair-governance case":
        old_expected = "failure['reproduction']['expected']='Historical A2/B candidate 55403 must turn the same oracle green without widening product scope, weakening tests, or changing execution authority.'"
        new_expected = "failure['reproduction']['expected']='Historical A2/B candidate 55403 plus independently validated bounded performance repair 7e6bd3 must turn the same oracle green without widening product scope, weakening tests, or changing execution authority.'"
        assert old_expected in script
        script = script.replace(old_expected, new_expected, 1)
        old_strategy = "plan['strategy']='Replay the already-approved A2/B atomic migration under a machine-valid successor governance identity: exact predecessor 17-path product scope, exact aeb9 RED source, exact historical 55403 candidate; no new product design or production activation.'"
        new_strategy = "plan['strategy']='Replay the already-approved A2/B atomic migration under a machine-valid successor governance identity: exact predecessor 17-path product scope, exact aeb9 RED source, exact historical 55403 candidate plus bounded performance repair 7e6bd3 on already-permitted capability_effects.py; no new product design or production activation.'"
        assert old_strategy in script
        script = script.replace(old_strategy, new_strategy, 1)
    run_script(index, name, cwd, script)

# The first product baseline proves the transition RED. The repair-governance
# plan and permit are now stable, while product source is still untouched.
# Re-freeze the official product baseline at this exact point so later Quality
# sees only permitted product changes rather than control-plane records.
PY = ROOT / "services/agent-service/.venv/bin/python"
run_command(
    9,
    "Refreeze still-RED product baseline after repair-governance permit",
    ROOT,
    [str(PY), "-B", "skillctl.py", "product-baseline", "--force"],
    log=RUNNER_TEMP / "successor-product-baseline-refreeze.log",
)
summary = json.loads((ROOT / f".quality/product-code/{SUCCESSOR}/baseline/run-summary.json").read_text())
record = json.loads((ROOT / f".quality/product-code/{SUCCESSOR}/baseline/baseline-record.json").read_text())
assert summary["run_kind"] == "baseline", summary
assert summary["loop_status"] == "BASELINE_RECORDED", summary
assert summary["decision"] == "FAIL", summary
claims = {row["id"]: row for row in summary.get("claim_results", [])}
claim = claims["V2018.A2B.SINGLE_WRITER_EXACT_OUTPUT"]
assert claim["status"] == "FAILED", claim
assert record["decision"] == "FAIL", record
(RUNNER_TEMP / "recovery-h2-refrozen-baseline.json").write_text(
    json.dumps(
        {
            "status": "REFROZEN_RED",
            "workspace_snapshot_fingerprint": record["workspace_snapshot_fingerprint"],
            "claim_status": claim["status"],
        },
        indent=2,
    )
    + "\n"
)

# Apply exactly the predecessor-approved 17-path candidate and then replace only
# the already-permitted capability_effects.py with the historical bounded repair.
overlay_name = "Overlay exact historical A2B candidate only inside successor permit"
overlay = extract_run(overlay_name)
overlay += f"\nperf_sha='{PERF_SHA}'\nperf_path='{PERF_PATH}'\nprintf '%s\\n' \"${{allowed[@]}}\" | grep -Fxq \"$perf_path\"\ngit show \"${{perf_sha}}:${{perf_path}}\" > \"/tmp/v2018-baseline/${{perf_path}}\"\ntest \"$(git show \"${{perf_sha}}:${{perf_path}}\" | sha256sum | awk '{{print $1}}')\" = \"$(sha256sum \"/tmp/v2018-baseline/${{perf_path}}\" | awk '{{print $1}}')\"\nprintf '%s\\n' \"$perf_sha\" > \"${{RUNNER_TEMP}}/performance-repair-sha.txt\"\n"
run_script(10, "Overlay 17-path candidate plus bounded performance repair", WORKSPACE, overlay)

# Deterministic diff proof is intentionally read-only before Product Quick.
print("::group::Recovery-H2 11 Read-only permit-bounded diff proof", flush=True)
started = time.monotonic()
sys.path.insert(0, str(ROOT / "skill-system/controller"))
from repair_governance import capture_workspace_manifest, load_chain  # type: ignore

contract = json.loads((ROOT / "governance/active-change.json").read_text())
chain = load_chain(ROOT, contract, include_diff=False, include_closure=False)
baseline_files = chain.baseline["workspace_files"]
current_files = capture_workspace_manifest(ROOT)
changed_all = sorted(
    path
    for path in set(baseline_files) | set(current_files)
    if baseline_files.get(path) != current_files.get(path)
)
assert changed_all == EXPECTED_CHANGED, (changed_all, EXPECTED_CHANGED)
assert set(changed_all).issubset(set(chain.permit["allowed_paths"])), changed_all
proof = {
    "status": "PASS",
    "changed_paths": changed_all,
    "out_of_scope_paths": [],
    "permit_digest": chain.permit_digest,
    "performance_repair_sha": PERF_SHA,
    "baseline_source_fingerprint": chain.baseline["source_fingerprint"],
}
(RUNNER_TEMP / "recovery-h2-readonly-diff-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
duration_ms = int((time.monotonic() - started) * 1000)
RECORDS.append({"index": 11, "name": "Read-only permit-bounded diff proof", "cwd": str(ROOT), "exit_code": 0, "duration_ms": duration_ms})
persist()
print(json.dumps(proof, indent=2))
print("::endgroup::", flush=True)

# Run the same real focused suites, but keep receipts under RUNNER_TEMP until
# Product Quick has converged. This leaves the frozen workspace unchanged.
focused_dir = RUNNER_TEMP / "recovery-h2-focused"
focused_dir.mkdir(parents=True, exist_ok=True)
env = os.environ.copy()
env["PYTHONPATH"] = f"{ROOT}/services/agent-service/src:{ROOT}/services/agent-service" + (f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else "")
focused_specs = {
    "focused_tests": [
        "services/agent-service/tests/architecture/test_v2018_single_writer_recovery_oracle.py",
        "services/agent-service/tests/architecture/test_semantic_single_writer_invariants.py",
        "services/agent-service/tests/runtime/test_semantic_output_coverage.py",
        "services/agent-service/tests/runtime/test_unified_semantic_planning_contract.py",
    ],
    "counterexamples": [
        "services/agent-service/tests/architecture/test_v2018_single_writer_recovery_oracle.py",
        "services/agent-service/tests/runtime/test_semantic_output_coverage.py",
    ],
    "negative_paths": [
        "services/agent-service/tests/runtime/test_semantic_output_coverage.py",
        "services/agent-service/tests/runtime/test_unified_semantic_planning_contract.py",
    ],
    "authority_boundary": [
        "services/agent-service/tests/architecture/test_semantic_single_writer_invariants.py",
        "services/agent-service/tests/architecture/test_v2018_single_writer_recovery_oracle.py",
    ],
    "runtime_trace": [
        "services/agent-service/tests/runtime/test_unified_semantic_planning_contract.py",
    ],
}
print("::group::Recovery-H2 12 Focused tests without workspace mutation", flush=True)
started = time.monotonic()
for dim, paths in focused_specs.items():
    command = [str(PY), "-B", "-m", "pytest", "-q", *paths]
    proc = subprocess.run(command, cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (focused_dir / f"{dim}.log").write_text(proc.stdout or "", encoding="utf-8", errors="replace")
    receipt = {
        "schema_version": 1,
        "dimension": dim,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "exit_code": proc.returncode,
        "baseline_sha": BASELINE_SHA,
        "candidate_sha": CANDIDATE_SHA,
        "performance_repair_sha": PERF_SHA,
        "log_tail": (proc.stdout or "")[-16000:],
    }
    (focused_dir / f"{dim}.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if proc.returncode != 0:
        print((proc.stdout or "")[-16000:])
        raise SystemExit(proc.returncode)
duration_ms = int((time.monotonic() - started) * 1000)
RECORDS.append({"index": 12, "name": "Focused tests without workspace mutation", "cwd": str(ROOT), "exit_code": 0, "duration_ms": duration_ms})
persist()
print("::endgroup::", flush=True)

# Formal Product Quick is the only completion decision in Recovery-H2.
quick_name = "Run full successor Quick and prove RED-to-GREEN Claim transition"
run_script(13, quick_name, ROOT, extract_run(quick_name))

quick_pointer = RUNNER_TEMP / "quick-summary-path.txt"
assert quick_pointer.is_file(), "formal Quick summary pointer missing"
quick_path = ROOT / quick_pointer.read_text().strip()
quick = json.loads(quick_path.read_text())
assert quick["decision"] == "PASS", quick
assert quick["loop_status"] == "CONVERGED", quick
assert quick["completion_eligible"] is True, quick
assert quick.get("missing_prerequisites") == [], quick
assert quick.get("unverified_claim_ids") == [], quick
assert quick.get("baseline_transition_unverified_claim_ids") == [], quick
claims = {row["id"]: row for row in quick.get("claim_results", [])}
claim = claims["V2018.A2B.SINGLE_WRITER_EXACT_OUTPUT"]
assert claim["gate_statuses"] == {"python-test-suites": "PASS"}, claim
assert claim["status"] != "FAILED", claim
print(
    json.dumps(
        {
            "recovery_h2": "PASS",
            "quick_summary": str(quick_path),
            "decision": quick["decision"],
            "loop_status": quick["loop_status"],
            "completion_eligible": quick["completion_eligible"],
            "claim_status": claim["status"],
        },
        indent=2,
    )
)
