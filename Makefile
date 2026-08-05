.PHONY: skill-quality skill-release product-baseline product-verify product-status quality quality-quick quality-integration quality-integration-managed release-check quality-baseline doctor bootstrap repair-loop local-first-init local-first-run local-first-status

QUALITY_PYTHON ?= $(shell services/agent-service/scripts/resolve_python.py)
SETUP_PYTHON ?= python3
TARGET ?=
BASELINE_EVIDENCE ?=
RERUN_FROM ?=
EVIDENCE_DIR ?=
STATE_DIR ?=
BASELINE_MODE ?= static
REPAIR_MODE ?= quick
FIX_COMMAND ?=

TARGET_ARG = --target "$(TARGET)"
BASELINE_EVIDENCE_ARG = --baseline-evidence "$(BASELINE_EVIDENCE)"
RERUN_ARG = $(if $(strip $(RERUN_FROM)),--rerun-from "$(RERUN_FROM)")
EVIDENCE_DIR_ARG = $(if $(strip $(EVIDENCE_DIR)),--evidence-dir "$(EVIDENCE_DIR)")
STATE_DIR_ARG = $(if $(strip $(STATE_DIR)),--state-dir "$(STATE_DIR)")
OPTIONAL_CONTROLLER_ARGS = $(RERUN_ARG) $(EVIDENCE_DIR_ARG) $(STATE_DIR_ARG)

REQUIRE_TARGET = $(if $(strip $(TARGET)),,$(error TARGET=/absolute/path/to/quality-loop-target.md is required))
REQUIRE_BASELINE_EVIDENCE = $(if $(strip $(BASELINE_EVIDENCE)),,$(error BASELINE_EVIDENCE=/absolute/path/to/baseline-evidence is required; run make quality-baseline first))
REQUIRE_FIX_COMMAND = $(if $(strip $(FIX_COMMAND)),,$(error FIX_COMMAND='executable arg...' is required for repair-loop))

skill-quality:
	$(SETUP_PYTHON) -B skill-system/controller/profile_runner.py skill-control-plane

skill-release:
	$(SETUP_PYTHON) -B skill-system/controller/profile_runner.py skill-release

product-baseline:
	$(SETUP_PYTHON) -B skillctl.py product-baseline

product-verify:
	$(SETUP_PYTHON) -B skillctl.py product-verify

product-status:
	$(SETUP_PYTHON) -B skillctl.py status

quality:
	@: $(REQUIRE_TARGET) $(REQUIRE_BASELINE_EVIDENCE)
	$(QUALITY_PYTHON) -B scripts/quality_loop.py --mode static $(TARGET_ARG) $(BASELINE_EVIDENCE_ARG) $(OPTIONAL_CONTROLLER_ARGS)

quality-quick:
	@: $(REQUIRE_TARGET) $(REQUIRE_BASELINE_EVIDENCE)
	$(QUALITY_PYTHON) -B scripts/quality_loop.py --mode quick $(TARGET_ARG) $(BASELINE_EVIDENCE_ARG) $(OPTIONAL_CONTROLLER_ARGS)

quality-integration:
	@: $(REQUIRE_TARGET) $(REQUIRE_BASELINE_EVIDENCE)
	$(QUALITY_PYTHON) -B scripts/quality_loop.py --mode integration $(TARGET_ARG) $(BASELINE_EVIDENCE_ARG) $(OPTIONAL_CONTROLLER_ARGS)

# Local end-to-end Integration owns disposable pgvector + Agent + Business
# processes, so missing shell URLs cannot silently turn product checks into
# skipped evidence. Real configured-model Gates remain real and still fail on
# provider/auth/quota errors.
quality-integration-managed:
	@: $(REQUIRE_TARGET) $(REQUIRE_BASELINE_EVIDENCE)
	$(QUALITY_PYTHON) -B scripts/run_managed_quality_integration.py \
		$(TARGET_ARG) $(BASELINE_EVIDENCE_ARG) $(OPTIONAL_CONTROLLER_ARGS)

release-check:
	@: $(REQUIRE_TARGET) $(REQUIRE_BASELINE_EVIDENCE)
	$(QUALITY_PYTHON) -B scripts/quality_loop.py --mode release $(TARGET_ARG) $(BASELINE_EVIDENCE_ARG) $(OPTIONAL_CONTROLLER_ARGS)

# A baseline is the only local command that does not consume baseline evidence:
# it creates baseline-record.json for the TARGET's first repair round.
quality-baseline:
	@: $(REQUIRE_TARGET)
	$(QUALITY_PYTHON) -B scripts/quality_loop.py --mode $(BASELINE_MODE) --baseline $(TARGET_ARG) $(EVIDENCE_DIR_ARG) $(STATE_DIR_ARG)

doctor:
	$(SETUP_PYTHON) -B scripts/workspace_doctor.py --workspace-root .

bootstrap:
	$(SETUP_PYTHON) -B scripts/bootstrap_workspace.py

repair-loop:
	@: $(REQUIRE_TARGET) $(REQUIRE_BASELINE_EVIDENCE) $(REQUIRE_FIX_COMMAND)
	$(QUALITY_PYTHON) -B scripts/repair_loop.py --mode $(REPAIR_MODE) \
		$(TARGET_ARG) $(BASELINE_EVIDENCE_ARG) $(STATE_DIR_ARG) \
		--fix-command $(FIX_COMMAND)
LOCAL_FIRST_SPEC ?=
LOCAL_FIRST_STATE ?= .quality/task-runs/local-first.json

REQUIRE_LOCAL_FIRST_SPEC = $(if $(strip $(LOCAL_FIRST_SPEC)),,$(error LOCAL_FIRST_SPEC=/absolute/path/to/local-first-task.json is required))

local-first-init:
	@: $(REQUIRE_LOCAL_FIRST_SPEC)
	$(SETUP_PYTHON) -B scripts/local_first_loop.py init --spec "$(LOCAL_FIRST_SPEC)" --state "$(LOCAL_FIRST_STATE)"

local-first-run:
	@: $(REQUIRE_LOCAL_FIRST_SPEC)
	$(SETUP_PYTHON) -B scripts/local_first_loop.py run-local --workspace . --spec "$(LOCAL_FIRST_SPEC)" --state "$(LOCAL_FIRST_STATE)"

local-first-status:
	$(SETUP_PYTHON) -B scripts/local_first_loop.py status --state "$(LOCAL_FIRST_STATE)"
