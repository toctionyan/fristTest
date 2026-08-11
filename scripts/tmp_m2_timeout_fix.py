#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "skill-system" / "controller" / "execution_runtime.py"
text = path.read_text(encoding="utf-8")
tree = ast.parse(text)
node = next(row for row in tree.body if isinstance(row, ast.FunctionDef) and row.name == "_terminate_process")
assert node.end_lineno is not None
replacement = textwrap.dedent(r'''
def _terminate_process(process: subprocess.Popen[str]) -> None:
    """Terminate the command group without waiting for every descendant to vanish."""
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
    else:  # pragma: no cover - Windows fallback
        process.terminate()
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return
    else:  # pragma: no cover - Windows fallback
        process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
''').lstrip()
lines = text.splitlines(keepends=True)
lines[node.lineno - 1:node.end_lineno] = [replacement + "\n"]
path.write_text("".join(lines), encoding="utf-8")
ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print("M2 timeout cleanup semantics fixed")
