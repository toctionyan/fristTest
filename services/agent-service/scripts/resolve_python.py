#!/usr/bin/env python3
from __future__ import annotations

"""Resolve a supported project interpreter without silently using Python 3.14.

The project baseline is Python 3.12.13.  `--baseline` is used by bootstrap and
CI to require that exact release.  Normal developer commands prefer that
baseline but may explicitly use a supported 3.12/3.13 runtime while a local
migration is in progress.  Python 3.14 is never accepted.
"""

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = (3, 12, 13)
MINIMUM = (3, 12)
MAXIMUM_EXCLUSIVE = (3, 14)


def _version(candidate: str) -> tuple[int, int, int] | None:
    try:
        proc = subprocess.run(
            [candidate, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode:
            return None
        values = tuple(int(value) for value in proc.stdout.strip().split(".")[:3])
        return values if len(values) == 3 else None
    except Exception:
        return None


def _supported(version: tuple[int, int, int] | None) -> bool:
    return bool(version and version[:2] >= MINIMUM and version[:2] < MAXIMUM_EXCLUSIVE)


def _candidate_paths() -> list[str]:
    values: list[str] = []
    if os.getenv("PYTHON_BIN"):
        values.append(os.environ["PYTHON_BIN"])
    values.extend([
        str(ROOT / ".venv" / "bin" / "python"),
        "python3.12",
        "python3",
    ])
    bundled = os.getenv("PYTHON312_BUNDLED_PATH")
    if bundled:
        values.append(bundled)
    resolved: list[str] = []
    for candidate in values:
        path = candidate if "/" in candidate else shutil.which(candidate)
        if path and path not in resolved:
            resolved.append(path)
    return resolved


def _uv_python(version_spec: str) -> str | None:
    uv = os.getenv("UV_BIN") or shutil.which("uv")
    if not uv and Path("/opt/pyvenv/bin/uv").exists():
        uv = "/opt/pyvenv/bin/uv"
    if not uv:
        return None
    try:
        proc = subprocess.run([uv, "python", "find", version_spec], capture_output=True, text=True, timeout=15)
        candidate = proc.stdout.strip() if proc.returncode == 0 else ""
        return candidate or None
    except Exception:
        return None


def resolve(*, require_baseline: bool = False) -> str | None:
    candidates = _candidate_paths()
    # First always prefer the reproducible baseline.
    for candidate in candidates:
        if _version(candidate) == BASELINE:
            return candidate
    uv_baseline = _uv_python("3.12.13")
    if uv_baseline and _version(uv_baseline) == BASELINE:
        return uv_baseline
    if require_baseline:
        return None
    # A temporary local 3.12/3.13 compatibility runtime is acceptable only
    # outside CI/bootstrap.  It is never an implicit fallback to 3.14.
    for candidate in candidates:
        if _supported(_version(candidate)):
            return candidate
    uv_supported = _uv_python("3.12") or _uv_python("3.13")
    if uv_supported and _supported(_version(uv_supported)):
        return uv_supported
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true", help="require Python 3.12.13 exactly")
    args = parser.parse_args()
    python = resolve(require_baseline=args.baseline)
    if not python:
        message = "Python 3.12.13 is required; run scripts/bootstrap.sh or set PYTHON_BIN to that interpreter."
        if not args.baseline:
            message = "No supported Python 3.12/3.13 interpreter found. Run scripts/bootstrap.sh or set PYTHON_BIN."
        print(message, file=__import__("sys").stderr)
        raise SystemExit(2)
    print(python)
