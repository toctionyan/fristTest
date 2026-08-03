from __future__ import annotations

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED_BY_ENVIRONMENT"
UPSTREAM_SKIPPED = "SKIPPED_UPSTREAM_FAILURE"
MODES = ("static", "quick", "integration", "release")
MODE_RANK = {mode: index for index, mode in enumerate(MODES)}
TARGET_HEADINGS = ("# 目标", "## 允许范围", "## 禁止范围", "## 验收条件", "## 基线", "## 修复轮次")
TARGET_CONTEXTS = {"local-change", "ci"}
TARGET_KINDS = {"diagnosis", "design", "oracle-review", "repair", "migration", "revert", "certification"}
TRANSITION_TARGET_KINDS = {"repair", "migration", "revert"}
NO_CHANGE_TARGET_KINDS = {"diagnosis", "design", "oracle-review", "certification"}
BLOCKING_LEVELS = {"required", "release"}
EVIDENCE_SCHEMA_VERSION = 6
MAX_REPAIR_ROUNDS = 8
STAGNATION_LIMIT = 2
EVIDENCE_REQUIRED_FIELDS = (
    "schema_version", "workspace_version", "mode", "run_kind", "decision", "loop_status",
    "generated_at", "evidence_dir", "target", "target_identity",
    "target_minimum_mode_declared", "target_minimum_mode_derived", "target_minimum_mode_effective",
    "replan_predecessor", "claim_manifest", "claim_manifest_fingerprint",
    "claim_manifest_evidence_file", "claim_results", "unverified_claim_ids", "policy_fingerprint",
    "rerun_from", "prior_evidence", "reused_prerequisites", "missing_prerequisites",
    "workspace_snapshot_start_fingerprint", "workspace_snapshot_fingerprint", "workspace_snapshot_file",
    "selected_gate_ids", "required_gate_ids", "gate_contract_fingerprints", "completion_eligible",
    "evidence_attestation_file", "results",
)
TARGET_PLACEHOLDERS = {
    "change-yyyymmdd-short-name", "change-yyyy-mm-dd-short-name", "example-target",
    "sample-target", "todo", "tbd", "待填写", "unknown",
}
SNAPSHOT_IGNORED_PARTS = {
    ".git", ".quality", ".venv", "__pycache__", ".pytest_cache", ".run-locks",
    "node_modules", "coverage",
}
SNAPSHOT_IGNORED_NAMES = {".coverage", ".DS_Store", ".env"}
SNAPSHOT_IGNORED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pyc"}
PRODUCTION_SOURCE_PREFIXES = (
    "services/agent-service/src/", "services/agent-service/app/",
    "services/business-service/business_service/",
)
ABSTRACTION_RECORD_MARKERS = ("新增项", "唯一职责", "替换或删除项", "删除证据", "验证")
RERUN_CONTRACT = "dependency_closure_then_downstream"
CLAIM_SCHEMA_VERSION = 1
CLAIM_RISKS = {"P0", "P1", "P2", "P3"}
CLAIM_CLOSURE_REQUIREMENTS = {"regression-transition", "current-pass"}
CLAIM_EVIDENCE_KINDS = {"static-contract", "counterexample", "integration", "release-provenance"}
