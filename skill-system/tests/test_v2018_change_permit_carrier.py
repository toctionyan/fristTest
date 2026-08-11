from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


CARRIER_PATH = Path("skill-system/tests/test_v2018_change_permit_carrier.py")
EXPECTED_CHANGE_ID = "migration-v20.18-semantic-single-writer-output-coverage"
CASE_DIR = Path("governance/repair-cases") / EXPECTED_CHANGE_ID


def _emit_payload(label: str, data: bytes) -> None:
    compressed = gzip.compress(data, compresslevel=9, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    chunk_size = 8000
    chunks = [encoded[index:index + chunk_size] for index in range(0, len(encoded), chunk_size)]
    print(f"V2018_{label}_SHA256={hashlib.sha256(data).hexdigest()}", flush=True)
    print(f"V2018_{label}_GZB64_CHUNKS={len(chunks)}", flush=True)
    for index, chunk in enumerate(chunks):
        print(f"V2018_{label}_GZB64_{index:04d}={chunk}", flush=True)


class V2018TrustedChangePermitCarrierTest(unittest.TestCase):
    def test_generate_trusted_permit_and_begin_evidence_from_exact_parent_tree(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="v2018-permit-") as raw_tmp:
            temp_root = Path(raw_tmp)
            archive = temp_root / "carrier-tree.tar"
            workspace = temp_root / "workspace"
            workspace.mkdir()

            subprocess.run(
                ["git", "archive", "--format=tar", "HEAD", "-o", str(archive)],
                cwd=root,
                check=True,
            )
            with tarfile.open(archive, mode="r") as handle:
                handle.extractall(workspace, filter="data")

            carrier_file = workspace / CARRIER_PATH
            self.assertTrue(carrier_file.is_file())
            carrier_file.unlink()

            contract_path = workspace / "governance/active-change.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            self.assertEqual(contract.get("change_id"), EXPECTED_CHANGE_ID)
            self.assertEqual(contract.get("status"), "approved")
            self.assertEqual(contract.get("result"), "PENDING")

            permit_code = r'''
import json
import sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "skill-system" / "controller"))
from repair_governance import create_permit
contract = json.loads((root / "governance" / "active-change.json").read_text(encoding="utf-8"))
path = create_permit(root, contract)
print(path.relative_to(root).as_posix())
'''
            created = subprocess.run(
                [sys.executable, "-B", "-c", permit_code, str(workspace)],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("change-permit.json", created.stdout)

            begun = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "skill-system/controller/change_contract_cli.py",
                    "begin",
                ],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("governance/active-change.json", begun.stdout.replace("\\", "/"))

            baseline_path = workspace / CASE_DIR / "baseline-manifest.json"
            permit_path = workspace / CASE_DIR / "change-permit.json"
            active_path = workspace / "governance/active-change.json"
            self.assertTrue(baseline_path.is_file())
            self.assertTrue(permit_path.is_file())
            self.assertTrue(active_path.is_file())

            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            permit = json.loads(permit_path.read_text(encoding="utf-8"))
            active = json.loads(active_path.read_text(encoding="utf-8"))
            self.assertEqual(baseline.get("record_type"), "baseline-manifest")
            self.assertGreater(len(baseline.get("workspace_files") or {}), 100)
            self.assertEqual(permit.get("record_type"), "change-permit")
            self.assertEqual(permit.get("status"), "ACTIVE")
            self.assertEqual(active.get("status"), "implementing")
            self.assertEqual(active.get("repair_governance_permit_digest"), permit.get("permit_digest"))

            _emit_payload("BASELINE", baseline_path.read_bytes())
            _emit_payload("PERMIT", permit_path.read_bytes())
            _emit_payload("ACTIVE_CHANGE", active_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
