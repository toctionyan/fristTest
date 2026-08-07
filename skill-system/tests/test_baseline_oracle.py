from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from quality_control import baseline_oracle as oracle  # noqa: E402
from quality_control.contracts import workspace_snapshot  # noqa: E402

BASE_IDENTITY = {
    "repository": oracle.EXPECTED_REPOSITORY,
    "commit_sha": oracle.EXPECTED_BASE_COMMIT,
}
TEST_PATH = "services/agent-service/tests/runtime/test_oracle_seed.py"
TEST_SELECTOR = f"{TEST_PATH}::test_added_by_oracle"
BASE_BYTES = b"def test_existing():\n    assert True\n"
OVERLAY_BYTES = BASE_BYTES + b"\ndef test_added_by_oracle():\n    assert False\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(payload: dict) -> str:
    normalized = dict(payload)
    normalized["overlay_file_map"] = sorted(
        [dict(item) for item in payload["overlay_file_map"]], key=lambda item: item["path"]
    )
    normalized["claim_bindings"] = sorted(
        [dict(item) for item in payload["claim_bindings"]],
        key=lambda item: (item["claim_id"], item["selector"]),
    )
    provenance = dict(normalized["provenance"])
    digest = str(provenance["artifact_digest"]).lower()
    if digest.startswith("sha256:"):
        digest = digest[7:]
    provenance["artifact_digest"] = digest
    normalized["provenance"] = provenance
    normalized.pop("canonical_fingerprint", None)
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class BaselineOracleAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="baseline-oracle-test-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.control = self.root / "control"
        self.source.mkdir()
        self.control.mkdir()
        source_file = self.source / TEST_PATH
        source_file.parent.mkdir(parents=True)
        source_file.write_bytes(BASE_BYTES)
        (self.source / "VERSION").write_text("test\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    @contextmanager
    def _source_identity(self):
        with mock.patch.object(oracle, "_current_source_identity", return_value=dict(BASE_IDENTITY)):
            yield

    def _artifact(self, members: dict[str, bytes] | None = None, *, name: str = "overlay.zip") -> Path:
        members = members or {TEST_PATH: OVERLAY_BYTES}
        path = self.control / name
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for member, data in members.items():
                archive.writestr(member, data)
        return path

    def _manifest_payload(
        self,
        artifact: Path,
        *,
        file_map: list[dict] | None = None,
        claim_bindings: list[dict] | None = None,
        overrides: dict | None = None,
    ) -> dict:
        artifact_sha = _sha(artifact.read_bytes())
        payload = {
            "schema_version": 1,
            "oracle_id": "stage5g4-c3g3-a2-unit",
            "base_source_identity": dict(BASE_IDENTITY),
            "base_workspace_fingerprint": workspace_snapshot(self.source)["fingerprint"],
            "overlay_artifact_sha256": artifact_sha,
            "overlay_file_map": file_map
            or [
                {
                    "path": TEST_PATH,
                    "base_file_sha256": _sha(BASE_BYTES),
                    "overlay_file_sha256": _sha(OVERLAY_BYTES),
                }
            ],
            "claim_bindings": claim_bindings
            or [{"claim_id": "TEST-CLAIM-001", "selector": TEST_SELECTOR}],
            "provenance": {
                "provider": "github-actions",
                "run_id": 31168041077,
                "job_id": 92833286038,
                "artifact_id": 8989792524,
                "artifact_digest": f"sha256:{artifact_sha}",
            },
            "execution_mode": "ephemeral_overlay_view",
            "canonical_fingerprint": "0" * 64,
        }
        if overrides:
            payload.update(overrides)
        payload["canonical_fingerprint"] = _canonical(payload)
        return payload

    def _manifest(self, artifact: Path, **kwargs) -> Path:
        payload = self._manifest_payload(artifact, **kwargs)
        path = self.control / "manifest.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def _validate(self, manifest: Path, artifact: Path):
        with self._source_identity():
            return oracle.load_and_validate_baseline_oracle(
                source_workspace=self.source, manifest_path=manifest, artifact_path=artifact
            )

    def _view(self, manifest: Path, artifact: Path):
        return oracle.baseline_oracle_execution_view(
            source_workspace=self.source, manifest_path=manifest, artifact_path=artifact
        )

    # BOO-P01
    def test_valid_manifest_passes_validation(self) -> None:
        artifact = self._artifact()
        manifest = self._manifest(artifact)
        identity = self._validate(manifest, artifact)
        self.assertEqual(identity.payload["execution_mode"], oracle.EXECUTION_MODE)
        self.assertEqual(identity.canonical_fingerprint, json.loads(manifest.read_text())["canonical_fingerprint"])

    # BOO-P02 + BOO-N16 defense invariant
    def test_execution_view_applies_only_overlay_and_never_mutates_source(self) -> None:
        artifact = self._artifact()
        manifest = self._manifest(artifact)
        before = workspace_snapshot(self.source)["fingerprint"]
        with self._source_identity():
            with self._view(manifest, artifact) as view:
                self.assertEqual((view.path / TEST_PATH).read_bytes(), OVERLAY_BYTES)
                self.assertEqual((self.source / TEST_PATH).read_bytes(), BASE_BYTES)
                self.assertEqual(workspace_snapshot(self.source)["fingerprint"], before)
        self.assertEqual((self.source / TEST_PATH).read_bytes(), BASE_BYTES)
        self.assertEqual(workspace_snapshot(self.source)["fingerprint"], before)

    # BOO-P03
    def test_canonical_identity_is_deterministic_across_declared_order(self) -> None:
        second_path = "services/agent-service/tests/runtime/test_oracle_second.py"
        second_base = b"def test_second_existing():\n    pass\n"
        second_overlay = second_base + b"\ndef test_second_added():\n    pass\n"
        second_source = self.source / second_path
        second_source.parent.mkdir(parents=True, exist_ok=True)
        second_source.write_bytes(second_base)
        artifact = self._artifact({TEST_PATH: OVERLAY_BYTES, second_path: second_overlay})
        file_map = [
            {"path": TEST_PATH, "base_file_sha256": _sha(BASE_BYTES), "overlay_file_sha256": _sha(OVERLAY_BYTES)},
            {"path": second_path, "base_file_sha256": _sha(second_base), "overlay_file_sha256": _sha(second_overlay)},
        ]
        bindings = [
            {"claim_id": "A", "selector": TEST_SELECTOR},
            {"claim_id": "B", "selector": f"{second_path}::test_second_added"},
        ]
        first = self._manifest_payload(artifact, file_map=file_map, claim_bindings=bindings)
        first_path = self.control / "first.json"
        first_path.write_text(json.dumps(first), encoding="utf-8")
        second = self._manifest_payload(artifact, file_map=list(reversed(file_map)), claim_bindings=list(reversed(bindings)))
        second_path_manifest = self.control / "second.json"
        second_path_manifest.write_text(json.dumps(second), encoding="utf-8")
        one = self._validate(first_path, artifact)
        two = self._validate(second_path_manifest, artifact)
        self.assertEqual(one.canonical_fingerprint, two.canonical_fingerprint)

    # BOO-P04
    def test_execution_view_is_removed_after_context_exit(self) -> None:
        artifact = self._artifact()
        manifest = self._manifest(artifact)
        with self._source_identity():
            with self._view(manifest, artifact) as view:
                path = view.path
                self.assertTrue(path.is_dir())
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())

    # BOO-N01
    def test_rejects_schema_version_mismatch(self) -> None:
        artifact = self._artifact()
        manifest = self._manifest(artifact, overrides={"schema_version": 2})
        with self.assertRaisesRegex(oracle.BaselineOracleError, "schema_version"):
            self._validate(manifest, artifact)

    # BOO-N02
    def test_rejects_bad_execution_mode(self) -> None:
        artifact = self._artifact()
        manifest = self._manifest(artifact, overrides={"execution_mode": "in_place"})
        with self.assertRaisesRegex(oracle.BaselineOracleError, "execution_mode"):
            self._validate(manifest, artifact)

    # BOO-N03 / N04
    def test_rejects_absolute_and_traversal_paths(self) -> None:
        artifact = self._artifact()
        for bad in ("/tmp/x.py", "../x.py", "a/../x.py"):
            with self.subTest(path=bad):
                file_map = [{"path": bad, "base_file_sha256": _sha(BASE_BYTES), "overlay_file_sha256": _sha(OVERLAY_BYTES)}]
                payload = self._manifest_payload(artifact, file_map=file_map, claim_bindings=[{"claim_id": "C", "selector": f"{bad}::test_x"}])
                path = self.control / f"bad-{hashlib.sha256(bad.encode()).hexdigest()[:8]}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(oracle.BaselineOracleError):
                    self._validate(path, artifact)

    # BOO-N05
    def test_rejects_duplicate_overlay_path_without_deduplication(self) -> None:
        artifact = self._artifact()
        entry = {"path": TEST_PATH, "base_file_sha256": _sha(BASE_BYTES), "overlay_file_sha256": _sha(OVERLAY_BYTES)}
        manifest = self._manifest(artifact, file_map=[dict(entry), dict(entry)])
        with self.assertRaisesRegex(oracle.BaselineOracleError, "duplicate overlay path"):
            self._validate(manifest, artifact)

    # BOO-N06
    def test_rejects_missing_source_file(self) -> None:
        artifact = self._artifact()
        manifest = self._manifest(artifact)
        (self.source / TEST_PATH).unlink()
        payload = json.loads(manifest.read_text())
        payload["base_workspace_fingerprint"] = workspace_snapshot(self.source)["fingerprint"]
        payload["canonical_fingerprint"] = _canonical(payload)
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(oracle.BaselineOracleError, "source file is missing"):
            self._validate(manifest, artifact)

    # BOO-N07
    def test_rejects_base_file_hash_mismatch(self) -> None:
        artifact = self._artifact()
        entry = {"path": TEST_PATH, "base_file_sha256": "1" * 64, "overlay_file_sha256": _sha(OVERLAY_BYTES)}
        manifest = self._manifest(artifact, file_map=[entry])
        with self.assertRaisesRegex(oracle.BaselineOracleError, "base_file_sha256 mismatch"):
            self._validate(manifest, artifact)

    # BOO-N08
    def test_rejects_base_workspace_fingerprint_mismatch(self) -> None:
        artifact = self._artifact()
        manifest = self._manifest(artifact, overrides={"base_workspace_fingerprint": "2" * 64})
        with self.assertRaisesRegex(oracle.BaselineOracleError, "base_workspace_fingerprint mismatch"):
            self._validate(manifest, artifact)

    # BOO-N09
    def test_rejects_artifact_digest_mismatch(self) -> None:
        artifact = self._artifact()
        manifest = self._manifest(artifact)
        artifact.write_bytes(artifact.read_bytes() + b"tamper")
        with self.assertRaisesRegex(oracle.BaselineOracleError, "overlay_artifact_sha256 mismatch"):
            self._validate(manifest, artifact)

    # BOO-N10
    def test_rejects_overlay_file_hash_mismatch(self) -> None:
        artifact = self._artifact()
        entry = {"path": TEST_PATH, "base_file_sha256": _sha(BASE_BYTES), "overlay_file_sha256": "3" * 64}
        manifest = self._manifest(artifact, file_map=[entry])
        with self.assertRaisesRegex(oracle.BaselineOracleError, "overlay_file_sha256 mismatch"):
            self._validate(manifest, artifact)

    # BOO-N11
    def test_rejects_undeclared_artifact_member(self) -> None:
        artifact = self._artifact({TEST_PATH: OVERLAY_BYTES, "extra.py": b"pass\n"})
        manifest = self._manifest(artifact)
        with self.assertRaisesRegex(oracle.BaselineOracleError, "member set mismatch"):
            self._validate(manifest, artifact)

    # BOO-N12
    def test_rejects_missing_artifact_member(self) -> None:
        artifact = self._artifact({"other.py": b"pass\n"})
        payload = self._manifest_payload(artifact)
        manifest = self.control / "manifest.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(oracle.BaselineOracleError, "member set mismatch"):
            self._validate(manifest, artifact)

    # BOO-N13
    def test_rejects_duplicate_claim_binding(self) -> None:
        artifact = self._artifact()
        binding = {"claim_id": "TEST-CLAIM-001", "selector": TEST_SELECTOR}
        manifest = self._manifest(artifact, claim_bindings=[dict(binding), dict(binding)])
        with self.assertRaisesRegex(oracle.BaselineOracleError, "duplicate claim binding"):
            self._validate(manifest, artifact)

    # BOO-N14
    def test_rejects_missing_provenance_field(self) -> None:
        artifact = self._artifact()
        payload = self._manifest_payload(artifact)
        del payload["provenance"]["job_id"]
        payload["canonical_fingerprint"] = _canonical(payload)
        manifest = self.control / "manifest.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(oracle.BaselineOracleError, "provenance has invalid fields"):
            self._validate(manifest, artifact)

    # BOO-N15
    def test_rejects_canonical_fingerprint_mismatch(self) -> None:
        artifact = self._artifact()
        manifest = self._manifest(artifact)
        payload = json.loads(manifest.read_text())
        payload["oracle_id"] = "tampered-after-fingerprint"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(oracle.BaselineOracleError, "canonical_fingerprint mismatch"):
            self._validate(manifest, artifact)

    # BOO-N16
    def test_malicious_artifact_traversal_cannot_write_source(self) -> None:
        before = (self.source / TEST_PATH).read_bytes()
        artifact = self._artifact({"../source/services/agent-service/tests/runtime/test_oracle_seed.py": b"pwned"})
        artifact_sha = _sha(artifact.read_bytes())
        payload = self._manifest_payload(artifact)
        payload["overlay_artifact_sha256"] = artifact_sha
        payload["provenance"]["artifact_digest"] = f"sha256:{artifact_sha}"
        payload["canonical_fingerprint"] = _canonical(payload)
        manifest = self.control / "manifest.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(oracle.BaselineOracleError):
            self._validate(manifest, artifact)
        self.assertEqual((self.source / TEST_PATH).read_bytes(), before)

    # BOO-N17
    def test_rejects_overlay_source_path_symlink_escape(self) -> None:
        external = self.root / "external.py"
        external.write_bytes(BASE_BYTES)
        source_file = self.source / TEST_PATH
        source_file.unlink()
        try:
            source_file.symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        artifact = self._artifact()
        payload = self._manifest_payload(artifact)
        payload["base_workspace_fingerprint"] = workspace_snapshot(self.source)["fingerprint"]
        payload["canonical_fingerprint"] = _canonical(payload)
        manifest = self.control / "manifest.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(oracle.BaselineOracleError, "symlinks"):
            self._validate(manifest, artifact)
        self.assertEqual(external.read_bytes(), BASE_BYTES)

    def test_rejects_selector_missing_after_overlay_before_view_is_yielded(self) -> None:
        bad_overlay = BASE_BYTES + b"\ndef some_other_test():\n    pass\n"
        artifact = self._artifact({TEST_PATH: bad_overlay})
        entry = {"path": TEST_PATH, "base_file_sha256": _sha(BASE_BYTES), "overlay_file_sha256": _sha(bad_overlay)}
        manifest = self._manifest(artifact, file_map=[entry])
        with self._source_identity():
            with self.assertRaisesRegex(oracle.BaselineOracleError, "selector is absent"):
                with self._view(manifest, artifact):
                    self.fail("invalid execution view must never be yielded")

    def test_rejects_provenance_digest_that_does_not_bind_supplied_artifact(self) -> None:
        artifact = self._artifact()
        payload = self._manifest_payload(artifact)
        payload["provenance"]["artifact_digest"] = "4" * 64
        payload["canonical_fingerprint"] = _canonical(payload)
        manifest = self.control / "manifest.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(oracle.BaselineOracleError, "provenance.artifact_digest"):
            self._validate(manifest, artifact)

    def test_source_mutation_while_view_active_is_detected_fail_closed(self) -> None:
        artifact = self._artifact()
        manifest = self._manifest(artifact)
        with self._source_identity():
            with self.assertRaisesRegex(oracle.BaselineOracleError, "source workspace mutated"):
                with self._view(manifest, artifact):
                    (self.source / "VERSION").write_text("tampered\n", encoding="utf-8")

    def test_tar_symlink_member_is_rejected(self) -> None:
        artifact = self.control / "overlay.tar"
        with tarfile.open(artifact, "w") as archive:
            info = tarfile.TarInfo(TEST_PATH)
            info.type = tarfile.SYMTYPE
            info.linkname = "/tmp/escape"
            archive.addfile(info)
        payload = self._manifest_payload(artifact)
        payload["overlay_file_map"][0]["overlay_file_sha256"] = _sha(OVERLAY_BYTES)
        payload["canonical_fingerprint"] = _canonical(payload)
        manifest = self.control / "manifest.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(oracle.BaselineOracleError, "non-regular member"):
            self._validate(manifest, artifact)


if __name__ == "__main__":
    unittest.main()
