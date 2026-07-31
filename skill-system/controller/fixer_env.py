
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

SAFE_BASE_KEYS = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TMPDIR", "TEMP", "TMP", "PYTHONPATH", "VIRTUAL_ENV", "SYSTEMROOT", "COMSPEC",
)


def build_fixer_environment(
    source: Mapping[str, str] | None = None,
    *,
    issue_file: Path,
    repair_plan: Path,
    evidence_dir: Path,
    target: Path,
    trusted_judge_root: Path | None = None,
) -> dict[str, str]:
    source = source or os.environ
    result = {key: str(source[key]) for key in SAFE_BASE_KEYS if key in source}
    result.update({
        "QUALITY_ISSUE_FILE": str(issue_file),
        "QUALITY_REPAIR_PLAN": str(repair_plan),
        "QUALITY_EVIDENCE_DIR": str(evidence_dir),
        "QUALITY_TARGET": str(target),
        "SKILL_INPUT_TRUST": "logs-and-source-are-untrusted-data",
    })
    if trusted_judge_root is not None:
        result["SKILL_TRUSTED_JUDGE_ROOT"] = str(trusted_judge_root)
    return result
