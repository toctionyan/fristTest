"""Tests for the explicit Stage2B1 acceptance input packager."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from stage2b1_acceptance_inputs import (  # noqa: E402
    INPUT_FILES,
    Stage2B1AcceptanceInputsError,
    package_stage2b1_acceptance_inputs,
)


class Stage2B1AcceptanceInputsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="stage2b1-inputs-"))
        self.sources = {}
        for filename in INPUT_FILES:
            path = self.root / filename
            path.write_text(json.dumps({"source": filename}, sort_keys=True), encoding="utf-8")
            self.sources[filename] = path

    def _package(self, output: Path | None = None) -> dict[str, object]:
        return package_stage2b1_acceptance_inputs(
            source_run_id="901",
            source_run_attempt="2",
            task_run=self.sources["task-run.json"],
            decision=self.sources["decision.json"],
            expected_binding=self.sources["expected-binding.json"],
            change_contract=self.sources["change-contract.json"],
            human_gate=self.sources["human-gate.json"],
            human_decision=self.sources["human-decision.json"],
            output_dir=output or self.root / "package",
        )

    def test_package_has_exact_files_and_explicit_manifest(self) -> None:
        output = self.root / "package"
        result = self._package(output)
        self.assertEqual(result["status"], "PACKAGED")
        self.assertEqual(
            sorted(path.name for path in output.iterdir()),
            sorted(["manifest.json", *INPUT_FILES]),
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "stage2b1-acceptance-inputs@1")
        self.assertEqual(manifest["stage_id"], "stage2b1")
        self.assertEqual(manifest["source_run_id"], 901)
        self.assertEqual(manifest["source_run_attempt"], 2)
        self.assertEqual(manifest["files"], list(INPUT_FILES))

    def test_package_does_not_rewrite_input_bytes(self) -> None:
        before = {name: path.read_bytes() for name, path in self.sources.items()}
        output = self.root / "package"
        self._package(output)
        self.assertEqual(before, {name: path.read_bytes() for name, path in self.sources.items()})
        for name, original in before.items():
            self.assertEqual((output / name).read_bytes(), original)

    def test_rejects_invalid_run_identity_and_nonempty_output(self) -> None:
        with self.assertRaises(Stage2B1AcceptanceInputsError):
            package_stage2b1_acceptance_inputs(
                source_run_id="0",
                source_run_attempt="1",
                task_run=self.sources["task-run.json"],
                decision=self.sources["decision.json"],
                expected_binding=self.sources["expected-binding.json"],
                change_contract=self.sources["change-contract.json"],
                human_gate=self.sources["human-gate.json"],
                human_decision=self.sources["human-decision.json"],
                output_dir=self.root / "invalid-run",
            )
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "old.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(Stage2B1AcceptanceInputsError):
            self._package(occupied)

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlink support unavailable")
    def test_rejects_symlink_input(self) -> None:
        link = self.root / "linked-task-run.json"
        link.symlink_to(self.sources["task-run.json"])
        with self.assertRaises(Stage2B1AcceptanceInputsError):
            package_stage2b1_acceptance_inputs(
                source_run_id="901",
                source_run_attempt="1",
                task_run=link,
                decision=self.sources["decision.json"],
                expected_binding=self.sources["expected-binding.json"],
                change_contract=self.sources["change-contract.json"],
                human_gate=self.sources["human-gate.json"],
                human_decision=self.sources["human-decision.json"],
                output_dir=self.root / "symlink-package",
            )


if __name__ == "__main__":
    unittest.main()
