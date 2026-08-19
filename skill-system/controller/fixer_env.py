
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

SAFE_BASE_KEYS = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TMPDIR", "TEMP", "TMP", "PYTHONPATH", "VIRTUAL_ENV", "SYSTEMROOT", "COMSPEC",
)


def _trusted_python_library_path() -> str | None:
    """Return this interpreter's verified adjacent shared-library directory.

    GitHub's setup-python action can provide CPython from a mounted toolcache whose
    executable needs its adjacent libpython directory at runtime. Fixer processes
    intentionally do not inherit caller-controlled LD_LIBRARY_PATH, so resolve the
    already-running trusted interpreter through any virtual-environment symlink and
    admit only its own existing libpython directory.
    """
    if os.name != "posix":
        return None
    try:
        executable = Path(sys.executable).resolve(strict=True)
    except OSError:
        return None
    if not executable.name.startswith("python") or executable.parent.name != "bin":
        return None
    try:
        library_path = (executable.parent.parent / "lib").resolve(strict=True)
    except OSError:
        return None
    if not library_path.is_dir():
        return None
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if not any(library_path.glob(f"libpython{version}.so*")):
        return None
    return str(library_path)


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
