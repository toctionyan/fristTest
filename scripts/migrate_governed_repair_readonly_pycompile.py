#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts/github_agent_fixer.py"


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    old = '''        if suffix == ".py":\n            passed, output = _run([sys.executable, "-m", "py_compile", str(absolute)], root)\n'''
    new = '''        if suffix == ".py":\n            # Syntax verification must be observationally read-only. Running\n            # ``python -m py_compile`` against the candidate path creates an\n            # untracked __pycache__ entry beside governed source, which dirties\n            # the workspace and can make the independent Stage-3 commit fail for\n            # reasons caused by the verifier itself. Compile to an isolated\n            # temporary cfile instead.\n            with tempfile.TemporaryDirectory(prefix="governed-repair-pycompile-") as temp:\n                pyc = Path(temp) / "candidate.pyc"\n                passed, output = _run(\n                    [\n                        sys.executable,\n                        "-c",\n                        (\n                            "import py_compile,sys; "\n                            "py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)"\n                        ),\n                        str(absolute),\n                        str(pyc),\n                    ],\n                    root,\n                )\n'''
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one py_compile verifier block, found {count}")
    TARGET.write_text(text.replace(old, new, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
