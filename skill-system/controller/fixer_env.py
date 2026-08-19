
from __future__ import annotations

import os
import sysconfig
from pathlib import Path
from typing import Mapping

SAFE_BASE_KEYS = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TMPDIR", "TEMP", "TMP", "PYTHONPATH", "VIRTUAL_ENV", "SYSTEMROOT", "COMSPEC",
)


def _trusted_python_library_path() -> str | None:
    """Return this interpreter's trusted shared-library directory, if one exists.

    GitHub's setup-python action can provide CPython from a mounted toolcache whose
    executable needs its adjacent libpython directory at runtime.  Fixer processes
    intentionally do not inherit caller-controlled LD_LIBRARY_PATH, so derive the
    one loader path we need from the already-running trusted interpreter instead.
    """
    if os.name != "posix":
        return None
    raw = str(sysconfig.get_config_var("LIBDIR") or "").strip()
    if not raw:
        return None
    try:
        path = Path(raw).resolve(strict=True)
    except OSError:
        return None
    return str(path) if path.is_dir() else None


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
    trusted_python_library_path = _trusted_python_library_path()
    if trusted_python_library_path:
        result["LD_LIBRARY_PATH"] = trusted_python_library_path
    if trusted_judge_root is not None:
        result["SKILL_TRUSTED_JUDGE_ROOT"] = str(trusted_judge_root)
    return result
