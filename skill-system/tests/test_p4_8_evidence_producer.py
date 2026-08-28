"""Tests for the real P4.8 GitHub platform-attestation producer."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
import inspect
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "scripts", ROOT / "skill-system" / "controller"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from p4_8_evidence_producer import (  # noqa: E402
    PAYLOAD_FILES,
    PREDICATE_TYPE,
    SIGNER_WORKFLOW,
    EvidenceProducerBlocked,
    _server_artifact,
    finalize_bundle,
    load_run_identity,
    produce_payload,
)
from stage2b1_provenance import verify_artifact_provenance  # noqa: E402


ARCHIVE_BYTES = b"server-downloaded-payload-archive"
ARCHIVE_DIGEST = "sha256:" + hashlib.sha256(ARCHIVE_BYTES).hexdigest()


def context() -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": "toctionyan/fristTest",
        "GITHUB_WORKFLOW": "p4-8-evidence-producer",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "901",
        "GITHUB_RUN_ATTEMPT": "2",
    }


def run_document() -> dict[str, object]:
    return {
        "id": 901,
        "run_attempt": 2,
        "workflow_id": 77,
        "name": "p4-8-evidence-producer",
        "path": ".github/workflows/p4-8-evidence-producer.yml",
        "event": "workflow_dispatch",
        "head_sha": "a" * 40,
        "head_branch": "main",
        "status": "in_progress",
        "repository": {"full_name": "toctionyan/fristTest"},
    }


def baseline() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 3,
        "snapshot_format": "protected-git-tree@1",
        "product_source_ref": "git-commit-sha1:" + "b" * 40,
        "protected_roots": ["contracts", "services", "web"],
        "entry_count": 0,
        "entries": {},
    }
    value["protected_snapshot_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(
            {
                "entries": {},
                "protected_roots": ["contracts", "services", "web"],
                "snapshot_format": "protected-git-tree@1",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return value


def artifact_document(digest: str = ARCHIVE_DIGEST) -> dict[str, object]:
    return {
        "id": 7701,
        "name": "p4-8-evidence-payload-901-2",
        "digest": digest,
        "expired": False,
        "workflow_run": {
            "id": 901,
            "run_attempt": 2,
            "head_branch": "main",
            "head_sha": "a" * 40,
        },
    }


class P48EvidenceProducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="p4-8-real-producer-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.payload = self.root / "payload"
        self.bundle = self.root / "bundle"
        registry = self.root / "skill-system/registry/product-source-baseline.json"
        registry.parent.mkdir(parents=True)
        registry.write_text(json.dumps(baseline()), encoding="utf-8")
        (self.root / "run.json").write_text(json.dumps(run_document()), encoding="utf-8")
        (self.root / "artifact.json").write_text(json.dumps(artifact_document()), encoding="utf-8")
        self.downloaded = self.root / "payload.zip"

    def _write_archive(
        self,
        *,
        entries: list[tuple[str, bytes, int]] | None = None,
    ) -> None:
        if entries is None:
            entries = [
                (
                    filename,
                    (self.payload / filename).read_bytes(),
                    stat.S_IMODE((self.payload / filename).stat().st_mode),
                )
                for filename in PAYLOAD_FILES
            ]
        with zipfile.ZipFile(self.downloaded, "w", zipfile.ZIP_STORED) as archive:
            for filename, content, permissions in entries:
                info = zipfile.ZipInfo(filename)
                info.create_system = 3
                info.external_attr = ((stat.S_IFREG | permissions) << 16)
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, content)

    def _archive_digest(self) -> str:
        if not zipfile.is_zipfile(self.downloaded):
            self._write_archive()
        return "sha256:" + hashlib.sha256(self.downloaded.read_bytes()).hexdigest()

    def produce(self, **kwargs: object) -> dict[str, object]:
        with patch(
            "p4_8_evidence_producer._server_run",
            return_value=run_document(),
        ):
            result = produce_payload(**kwargs)  # type: ignore[arg-type]
        self._write_archive()
        return result

    def finalize(self, **kwargs: object) -> dict[str, object]:
        with patch(
            "p4_8_evidence_producer._server_run",
            return_value=run_document(),
        ), patch(
            "p4_8_evidence_producer._server_artifact",
            return_value=artifact_document(digest=self._archive_digest()),
        ):
            return finalize_bundle(**kwargs)  # type: ignore[arg-type]

    def test_requires_protected_main_and_exact_server_run(self) -> None:
        identity = load_run_identity(context(), run_document())
        self.assertEqual(identity["run_id"], 901)
        with self.assertRaisesRegex(EvidenceProducerBlocked, "ref_must_be_protected"):
            load_run_identity({**context(), "GITHUB_REF_PROTECTED": "false"}, run_document())
        with self.assertRaisesRegex(EvidenceProducerBlocked, "workflow_run_path_mismatch"):
            load_run_identity(context(), {**run_document(), "path": ".github/workflows/other.yml"})

    def test_server_metadata_is_not_a_public_producer_input(self) -> None:
        self.assertNotIn("run_document", inspect.signature(produce_payload).parameters)
        self.assertNotIn("artifact_document", inspect.signature(finalize_bundle).parameters)

    def test_payload_is_unsigned_and_does_not_write_governance_state(self) -> None:
        result = self.produce(
            workspace=self.root,
            output=self.payload,
            environ=context(),
        )
        self.assertEqual(result["status"], "READY_FOR_EXTERNAL_VERIFICATION")
        self.assertTrue(result["unsigned_payload"])
        self.assertEqual(sorted(path.name for path in self.payload.iterdir()), sorted(PAYLOAD_FILES))
        self.assertFalse((self.root / "TaskRun").exists())
        self.assertFalse((self.root / "governance/active-change.json").exists())

    def test_finalize_binds_api_digest_and_refuses_mismatch(self) -> None:
        self.produce(workspace=self.root, output=self.payload, environ=context())
        manifest = self.finalize(
            payload=self.payload,
            output=self.bundle,
            environ=context(),
            artifact_id="7701",
            upload_artifact_digest=self._archive_digest(),
            downloaded_artifact=self.downloaded,
        )
        self.assertEqual(manifest["status"], "READY_FOR_EXTERNAL_VERIFICATION")
        self.assertEqual(manifest["artifact"]["digest"], self._archive_digest())
        self.assertTrue(manifest["attestation_verification_request"]["platform_proof_required"])
        self.assertFalse(manifest["attestation_verification_request"]["self_generated_attestation_accepted"])
        provenance = json.loads((self.bundle / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["artifact"]["archive_digest"], self._archive_digest())
        verified = verify_artifact_provenance(
            provenance,
            expected={
                "receipt_id": "receipt-p4-8",
                "artifact_id": "7701",
                "artifact_name": "p4-8-evidence-payload-901-2",
                "artifact_digest": self._archive_digest(),
                "content_digest": provenance["artifact"]["content_digest"],
                "repository": "toctionyan/fristTest",
                "workflow_path": ".github/workflows/p4-8-evidence-producer.yml",
                "workflow_id": 77,
                "event": "workflow_dispatch",
                "ref": "refs/heads/main",
                "head_sha": "a" * 40,
                "run_id": 901,
                "run_attempt": 2,
            },
        )
        self.assertTrue(verified.proof_ref.startswith("provenance:sha256:"))
        request = manifest["attestation_verification_request"]
        self.assertEqual(
            set(request),
            {
                "platform_proof_required",
                "repository",
                "signer_workflow",
                "subject_digest",
                "predicate_type",
                "source_digest",
                "source_ref",
                "self_generated_attestation_accepted",
            },
        )
        self.assertEqual(request["subject_digest"], self._archive_digest())
        self.assertEqual(request["source_digest"], context()["GITHUB_SHA"])
        with self.assertRaisesRegex(EvidenceProducerBlocked, "upload_artifact_digest_mismatch"):
            self.finalize(
                payload=self.payload,
                output=self.root / "bad-bundle",
                environ=context(),
                artifact_id="7701",
                upload_artifact_digest="sha256:" + "e" * 64,
                downloaded_artifact=self.downloaded,
            )

    def test_downloaded_archive_digest_is_required(self) -> None:
        self.produce(workspace=self.root, output=self.payload, environ=context())
        bad = self.root / "bad-payload.zip"
        bad.write_bytes(b"different-server-response")
        archive_digest = self._archive_digest()
        with self.assertRaisesRegex(EvidenceProducerBlocked, "downloaded_artifact_digest_mismatch"):
            self.finalize(
                payload=self.payload,
                output=self.root / "bad-download-bundle",
                environ=context(),
                artifact_id="7701",
                upload_artifact_digest=archive_digest,
                downloaded_artifact=bad,
            )

    def test_v2_baseline_and_untrusted_artifact_are_blocked(self) -> None:
        bad_baseline = self.root / "skill-system/registry/product-source-baseline.json"
        bad_baseline.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
        with self.assertRaisesRegex(EvidenceProducerBlocked, "product_source_baseline_invalid"):
            self.produce(workspace=self.root, output=self.payload, environ=context())
        bad_baseline.write_text(json.dumps(baseline()), encoding="utf-8")
        with self.assertRaisesRegex(EvidenceProducerBlocked, "artifact_digest_invalid"):
            from p4_8_evidence_producer import _artifact
            _artifact({**artifact_document(), "digest": "local"}, {"run_id": 901, "run_attempt": 2})

    def test_canonical_policy_import_is_available_to_script(self) -> None:
        import p4_8_evidence_producer as producer_module
        import product_source_baseline_policy  # noqa: F401

        self.assertEqual(
            Path(producer_module.__file__).resolve().parents[1] / "skill-system" / "controller",
            Path(product_source_baseline_policy.__file__).resolve().parent,
        )

    def test_artifact_must_bind_to_exact_attempt_and_head(self) -> None:
        from p4_8_evidence_producer import _artifact

        for override in (
            {"run_attempt": 1},
            {"head_sha": "f" * 40},
        ):
            with self.subTest(override=override):
                artifact = dict(artifact_document())
                artifact["workflow_run"] = {
                    **artifact_document()["workflow_run"],  # type: ignore[arg-type]
                    **override,
                }
                with self.assertRaisesRegex(EvidenceProducerBlocked, "workflow_run_mismatch"):
                    _artifact(artifact, {"run_id": 901, "run_attempt": 2, "head_sha": "a" * 40})

    def test_payload_symlink_is_blocked_before_bundle_copy(self) -> None:
        self.produce(workspace=self.root, output=self.payload, environ=context())
        target = self.payload / "producer-run.json"
        replacement = self.root / "replacement.json"
        replacement.write_text("{}", encoding="utf-8")
        target.unlink()
        target.symlink_to(replacement)
        with self.assertRaisesRegex(EvidenceProducerBlocked, "payload_file_unsafe:producer-run.json"):
            self.finalize(
                payload=self.payload,
                output=self.root / "unsafe-bundle",
                environ=context(),
                artifact_id="7701",
                upload_artifact_digest=self._archive_digest(),
                downloaded_artifact=self.downloaded,
            )

    def test_uploaded_archive_must_match_local_payload_bytes(self) -> None:
        self.produce(workspace=self.root, output=self.payload, environ=context())
        archive_digest = self._archive_digest()
        (self.payload / "producer-run.json").write_bytes(b"mutated-after-upload")
        with self.assertRaisesRegex(EvidenceProducerBlocked, "uploaded_artifact_payload_mismatch"):
            self.finalize(
                payload=self.payload,
                output=self.root / "mutated-bundle",
                environ=context(),
                artifact_id="7701",
                upload_artifact_digest=archive_digest,
                downloaded_artifact=self.downloaded,
            )

    def test_uploaded_archive_rejects_extra_or_traversal_entries(self) -> None:
        self.produce(workspace=self.root, output=self.payload, environ=context())
        self._write_archive(
            entries=[
                (filename, (self.payload / filename).read_bytes(), 0o644)
                for filename in PAYLOAD_FILES
            ],
        )
        with zipfile.ZipFile(self.downloaded, "a", zipfile.ZIP_STORED) as archive:
            archive.writestr("../escape", b"bad")
        with self.assertRaisesRegex(EvidenceProducerBlocked, "zip_entry_path_traversal"):
            self.finalize(
                payload=self.payload,
                output=self.root / "unsafe-bundle",
                environ=context(),
                artifact_id="7701",
                upload_artifact_digest=self._archive_digest(),
                downloaded_artifact=self.downloaded,
            )

    def test_uploaded_archive_rejects_absolute_entries(self) -> None:
        self.produce(workspace=self.root, output=self.payload, environ=context())
        entries = [
            (filename, (self.payload / filename).read_bytes(), 0o644)
            for filename in PAYLOAD_FILES
        ]
        entries[-1] = ("/absolute-escape", b"bad", 0o644)
        self._write_archive(entries=entries)
        with self.assertRaisesRegex(EvidenceProducerBlocked, "zip_entry_absolute_path"):
            self.finalize(
                payload=self.payload,
                output=self.root / "absolute-bundle",
                environ=context(),
                artifact_id="7701",
                upload_artifact_digest=self._archive_digest(),
                downloaded_artifact=self.downloaded,
            )

    def test_uploaded_archive_content_tampering_is_rejected(self) -> None:
        self.produce(workspace=self.root, output=self.payload, environ=context())
        entries = [
            (
                filename,
                b"tampered" if filename == "producer-run.json" else (self.payload / filename).read_bytes(),
                0o644,
            )
            for filename in PAYLOAD_FILES
        ]
        self._write_archive(entries=entries)
        with self.assertRaisesRegex(EvidenceProducerBlocked, "uploaded_artifact_payload_mismatch"):
            self.finalize(
                payload=self.payload,
                output=self.root / "tampered-bundle",
                environ=context(),
                artifact_id="7701",
                upload_artifact_digest=self._archive_digest(),
                downloaded_artifact=self.downloaded,
            )

    def test_uploaded_archive_duplicate_path_is_rejected(self) -> None:
        self.produce(workspace=self.root, output=self.payload, environ=context())
        entries = [
            (filename, (self.payload / filename).read_bytes(), 0o644)
            for filename in PAYLOAD_FILES
        ]
        entries.append(("producer-run.json", b"duplicate", 0o644))
        self._write_archive(entries=entries)
        with self.assertRaisesRegex(EvidenceProducerBlocked, "zip_entry_duplicate_path"):
            self.finalize(
                payload=self.payload,
                output=self.root / "duplicate-bundle",
                environ=context(),
                artifact_id="7701",
                upload_artifact_digest=self._archive_digest(),
                downloaded_artifact=self.downloaded,
            )

    def test_uploaded_archive_symlink_and_special_entries_are_rejected(self) -> None:
        self.produce(workspace=self.root, output=self.payload, environ=context())
        for entry_type, mode, expected in (
            ("symlink", stat.S_IFLNK | 0o777, "zip_entry_special_or_link"),
            ("special", stat.S_IFIFO | 0o644, "zip_entry_special_or_link"),
        ):
            with self.subTest(entry_type=entry_type):
                self._write_archive(
                    entries=[
                        *(
                            (filename, (self.payload / filename).read_bytes(), 0o644)
                            for filename in PAYLOAD_FILES
                            if filename != "producer-run.json"
                        ),
                        ("producer-run.json", b"unsafe", mode),
                    ]
                )
                with self.assertRaisesRegex(EvidenceProducerBlocked, expected):
                    self.finalize(
                        payload=self.payload,
                        output=self.root / f"{entry_type}-bundle",
                        environ=context(),
                        artifact_id="7701",
                        upload_artifact_digest=self._archive_digest(),
                        downloaded_artifact=self.downloaded,
                    )

    def test_local_payload_hardlink_is_rejected(self) -> None:
        self.produce(workspace=self.root, output=self.payload, environ=context())
        archive_digest = self._archive_digest()
        original = self.payload / "producer-run.json"
        hardlink_target = self.root / "hardlink-target"
        hardlink_target.write_bytes(original.read_bytes())
        original.unlink()
        os.link(hardlink_target, original)
        with self.assertRaisesRegex(EvidenceProducerBlocked, "payload_file:producer-run.json_hardlink"):
            self.finalize(
                payload=self.payload,
                output=self.root / "hardlink-bundle",
                environ=context(),
                artifact_id="7701",
                upload_artifact_digest=archive_digest,
                downloaded_artifact=self.downloaded,
            )

    def test_mode_is_part_of_uploaded_payload_proof(self) -> None:
        self.produce(workspace=self.root, output=self.payload, environ=context())
        self._write_archive(
            entries=[
                (
                    filename,
                    (self.payload / filename).read_bytes(),
                    0o755 if filename == "producer-run.json" else 0o644,
                )
                for filename in PAYLOAD_FILES
            ]
        )
        with self.assertRaisesRegex(EvidenceProducerBlocked, "uploaded_artifact_payload_mismatch"):
            self.finalize(
                payload=self.payload,
                output=self.root / "mode-bundle",
                environ=context(),
                artifact_id="7701",
                upload_artifact_digest=self._archive_digest(),
                downloaded_artifact=self.downloaded,
            )

    def test_regular_file_mode_is_compatible_and_preserved(self) -> None:
        self.produce(workspace=self.root, output=self.payload, environ=context())
        os.chmod(self.payload / "producer-run.json", 0o755)
        self._write_archive()
        self.finalize(
            payload=self.payload,
            output=self.root / "executable-bundle",
            environ=context(),
            artifact_id="7701",
            upload_artifact_digest=self._archive_digest(),
            downloaded_artifact=self.downloaded,
        )
        self.assertEqual(
            stat.S_IMODE((self.root / "executable-bundle" / "producer-run.json").stat().st_mode),
            0o755,
        )

    def test_short_nonfinal_artifact_page_is_fail_closed(self) -> None:
        selected = artifact_document()
        with self.assertRaisesRegex(EvidenceProducerBlocked, "artifact_list_pagination_gap"):
            with patch(
                "p4_8_evidence_producer._run_gh_json",
                side_effect=[
                    selected,
                    {"total_count": 150, "artifacts": [selected]},
                ],
            ):
                _server_artifact(
                    identity={
                        "run_id": 901,
                        "run_attempt": 2,
                        "head_sha": "a" * 40,
                    },
                    artifact_id="7701",
                )


class P48WorkflowContractTests(unittest.TestCase):
    source = (ROOT / ".github/workflows/p4-8-evidence-producer.yml").read_text(encoding="utf-8")

    def test_workflow_uses_platform_attestation_on_upload_digest(self) -> None:
        for fragment in (
            "workflow_dispatch:",
            "github.ref_protected == true",
            "id-token: write",
            "attestations: write",
            "artifact-metadata: write",
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6",
            "subject-digest: sha256:${{ steps.payload.outputs.artifact-digest }}",
            "PAYLOAD_ARTIFACT_DIGEST_HEX",
            "subject-name:",
            "actions/artifacts/${PAYLOAD_ARTIFACT_ID}",
            "actions/artifacts/${PAYLOAD_ARTIFACT_ID}/zip",
        ):
            self.assertIn(fragment, self.source)
        self.assertNotIn("github.event.inputs", self.source)
        self.assertNotIn("TaskRun", self.source)
        self.assertNotIn("active-change.json", self.source)
        self.assertNotIn("gh attestation verify", self.source)

    def test_workflow_retries_eventually_visible_artifact_before_finalize(self) -> None:
        self.assertIn("downloaded=false", self.source)
        self.assertIn("finalized=false", self.source)
        self.assertGreaterEqual(self.source.count("for retry in 1 2 3 4 5"), 2)
        self.assertIn("sleep $((retry * 5))", self.source)

    def test_workflow_is_not_an_acceptance_writer(self) -> None:
        self.assertIn("contents: read", self.source)
        self.assertIn("actions: read", self.source)
        self.assertNotIn(SIGNER_WORKFLOW, self.source)
        self.assertIn(PREDICATE_TYPE, (ROOT / "scripts/p4_8_evidence_producer.py").read_text(encoding="utf-8"))
        self.assertEqual(self.source.count("uses: actions/upload-artifact@"), 2)
        self.assertIn("p4-8-evidence-payload-${{ github.run_id }}-${{ github.run_attempt }}", self.source)
        self.assertIn("p4-8-evidence-bundle-${{ github.run_id }}-${{ github.run_attempt }}", self.source)
        self.assertNotIn("stage2b1-acceptance-inputs", self.source)
        self.assertNotIn("decision.json", self.source)
        self.assertNotIn("human-gate.json", self.source)
        for forbidden in ("contents: write", "governance/active-change.json", "git push", "gh pr merge"):
            self.assertNotIn(forbidden, self.source)


if __name__ == "__main__":
    unittest.main()
