from __future__ import annotations

from pathlib import Path
import py_compile
import sys


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: tmp_wp08_release_retirement_fixture_fix.py <source-root>")
    root = Path(sys.argv[1]).resolve()
    path = root / "skill-system" / "tests" / "test_wp08_release_recovery.py"
    text = path.read_text(encoding="utf-8")
    old = '''    def dispatch_wp08(self, *, candidate_sha: str) -> int:\n        self.dispatched.append(candidate_sha)\n        return 43\n'''
    new = '''    def dispatch_wp08(\n        self,\n        *,\n        candidate_sha: str,\n        resume_run_id: int | None = None,\n        resume_run_attempt: int = 1,\n    ) -> int:\n        self.dispatched.append(candidate_sha)\n        return 43\n'''
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one stale dispatch_wp08 fixture, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    py_compile.compile(str(path), doraise=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
