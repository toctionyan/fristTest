from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BASELINE = ROOT / "skill-system/registry/product-source-baseline.json"
COMPATIBILITY = ROOT / "skill-system/controller/project_compatibility.py"
COMPATIBILITY_TEST = ROOT / "skill-system/tests/test_project_compatibility.py"

EXPECTED_BEFORE = {
    BASELINE: "794fccb32df38fff87ec0472217de014acd923471083c1da328060d497a845ac",
    COMPATIBILITY: "326da05da9d1772d0c23f4c201554b7a2ce772bcc9d7c6c5cb12d17802446645",
    COMPATIBILITY_TEST: "feea3da00a69cd327645a2970939d7fe61ac980a60ce91386bd632fa00d6f570",
}
EXPECTED_AFTER = {
    BASELINE: "250ed571ca73a1338d232a070fc2442b1dbe263318db4f0e3e700c7a5dc0a419",
    COMPATIBILITY: "28c6509301b74c54eba9fcf1a2f62c5e435ed58531ef4595e748bdcd5544e4a0",
    COMPATIBILITY_TEST: "3d2064a0139660ff9f779d69b94912a141e0eabf91288c57620630e7c8202f57",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(expected: dict[Path, str], phase: str) -> None:
    errors = [
        f"{phase} hash mismatch: {path.relative_to(ROOT)} expected={digest} actual={sha256(path)}"
        for path, digest in expected.items()
        if not path.is_file() or sha256(path) != digest
    ]
    if errors:
        raise SystemExit("\n".join(errors))


def repair_baseline() -> None:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, dict) or payload.get("file_count") != 537 or len(files) != 537:
        raise SystemExit("unexpected B28 source baseline shape")
    for generated in (
        "services/agent-service/runtime/sqlite/app.db",
        "services/business-service/runtime/business-service/business.db",
    ):
        if generated not in files:
            raise SystemExit(f"missing reviewed generated-state entry: {generated}")
        files.pop(generated)
    payload["file_count"] = len(files)
    if payload["file_count"] != 535:
        raise SystemExit("corrected source baseline must contain 535 files")
    BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def repair_compatibility_runtime() -> None:
    source = COMPATIBILITY.read_text(encoding="utf-8")
    replacements = (
        (
            'IGNORED_PARTS = {".venv", "node_modules", "__pycache__"}\n',
            'IGNORED_PARTS = {".venv", "node_modules", "__pycache__"}\n'
            'GENERATED_STATE_PREFIXES = (\n'
            '    "services/agent-service/runtime/",\n'
            '    "services/business-service/runtime/",\n'
            ')\n\n\n'
            'def _is_generated_state_path(path: str) -> bool:\n'
            '    return path.startswith(GENERATED_STATE_PREFIXES)\n',
        ),
        (
            '            if any(part in IGNORED_PARTS for part in path.parts):\n'
            '                continue\n'
            '            rows[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()\n',
            '            if any(part in IGNORED_PARTS for part in path.parts):\n'
            '                continue\n'
            '            relative = path.relative_to(root).as_posix()\n'
            '            if _is_generated_state_path(relative):\n'
            '                continue\n'
            '            rows[relative] = hashlib.sha256(path.read_bytes()).hexdigest()\n',
        ),
        (
            '        if not path.startswith(prefixes):\n'
            '            continue\n',
            '        if not path.startswith(prefixes) or _is_generated_state_path(path):\n'
            '            continue\n',
        ),
    )
    for old, new in replacements:
        if source.count(old) != 1:
            raise SystemExit("project compatibility preimage changed")
        source = source.replace(old, new, 1)
    COMPATIBILITY.write_text(source, encoding="utf-8")


def repair_compatibility_tests() -> None:
    tests = COMPATIBILITY_TEST.read_text(encoding="utf-8")
    marker = '\n\n\nif __name__ == "__main__":\n'
    addition = "\n".join(
        (
            "    def test_generated_service_runtime_state_is_not_product_source(self) -> None:",
            '        write(self.root / "services/agent-service/runtime/sqlite/app.db", "runtime state\\n")',
            "        write(",
            '            self.root / "services/business-service/runtime/business-service/business.db",',
            '            "runtime state\\n",',
            "        )",
            "        result = evaluate(self.root)",
            '        self.assertEqual(result["status"], "PASS")',
            '        self.assertEqual(result["protected_file_count"], 1)',
            "",
            "    def test_source_runtime_code_remains_protected(self) -> None:",
            "        write(",
            '            self.root / "services/agent-service/src/agent_core/runtime/guard.py",',
            '            "ENFORCED = True\\n",',
            "        )",
            "        result = evaluate(self.root)",
            '        self.assertEqual(result["status"], "FAIL")',
            "        self.assertTrue(",
            "            any(",
            '                "product_source_changed:services/agent-service/src/agent_core/runtime/guard.py"',
            "                in item",
            '                for item in result["errors"]',
            "            )",
            "        )",
        )
    ) + "\n"
    if tests.count(marker) != 1:
        raise SystemExit("test module tail marker changed")
    COMPATIBILITY_TEST.write_text(
        tests.replace(marker, "\n\n" + addition + "\n\nif __name__ == \"__main__\":\n", 1),
        encoding="utf-8",
    )


def main() -> None:
    verify(EXPECTED_BEFORE, "before")
    repair_baseline()
    repair_compatibility_runtime()
    repair_compatibility_tests()
    verify(EXPECTED_AFTER, "after")
    print(json.dumps({"status": "PASS", "corrected_files": 3}, sort_keys=True))


if __name__ == "__main__":
    main()
