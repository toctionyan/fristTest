from __future__ import annotations

import base64
from io import BytesIO
import gzip
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import unittest


PRODUCT_PATHS = (
    "services/agent-service/src/agent_core/modules/contracts.py",
    "services/agent-service/src/agent_core/modules/registry.py",
    "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
    "services/agent-service/src/agent_core/lifecycle/protocol.py",
    "services/agent-service/src/agent_core/lifecycle/semantic_contract.py",
    "services/agent-service/src/agent_core/kernel/semantic_contract.py",
    "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
    "services/agent-service/src/agent_core/lifecycle/goal_granularity.py",
    "services/agent-service/src/agent_core/runtime/capability_effects.py",
    "services/agent-service/src/agent_modules/ecommerce/semantic_vocabulary.py",
    "services/agent-service/src/agent_modules/ecommerce/module.py",
    "services/agent-service/tests/architecture/test_semantic_single_writer_invariants.py",
    "services/agent-service/tests/runtime/test_semantic_output_coverage.py",
    "services/agent-service/tests/runtime/test_unified_semantic_planning_contract.py",
)


def _emit_candidate_archive(root: Path) -> None:
    raw = BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for path in PRODUCT_PATHS:
            data = (root / path).read_bytes()
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, BytesIO(data))
    payload = gzip.compress(raw.getvalue(), compresslevel=9, mtime=0)
    encoded = base64.b64encode(payload).decode("ascii")
    chunks = [encoded[index:index + 9000] for index in range(0, len(encoded), 9000)]
    print(f"V2018_A2B_ARCHIVE_SHA256={hashlib.sha256(payload).hexdigest()}", flush=True)
    print(f"V2018_A2B_ARCHIVE_CHUNKS={len(chunks)}", flush=True)
    for index, chunk in enumerate(chunks):
        print(f"V2018_A2B_ARCHIVE_{index:04d}={chunk}", flush=True)


class V2018SemanticVocabularyBatchTest(unittest.TestCase):
    def test_capability_independent_vocabulary_contract(self) -> None:
        root = Path(__file__).resolve().parents[2]
        src = root / "services" / "agent-service" / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))

        from agent_core.modules.registry import ModuleRegistry
        from agent_modules.ecommerce.module import EcommerceModule

        registry = ModuleRegistry([EcommerceModule()])
        snapshot = registry.semantic_vocabulary_snapshot()
        outputs = {row["output_id"]: row for row in snapshot["outputs"]}
        self.assertFalse(snapshot["availability_exposed"])
        self.assertFalse(snapshot["tool_names_exposed"])
        self.assertIn("shipment.current_status", outputs)
        self.assertIn("shipment.eta", outputs)
        self.assertIn("courier.contact.phone", outputs)
        self.assertEqual(outputs["courier.contact.phone"]["subject_type"], "courier")
        self.assertNotIn("legacy_effect_aliases", outputs["shipment.current_status"])
        rendered = repr(snapshot)
        self.assertNotIn("get_order_logistics", rendered)
        self.assertNotIn("report_unsupported_request", rendered)

        aliases = registry.legacy_semantic_output_aliases()
        self.assertEqual(
            aliases["order.query_logistics:order"],
            ("shipment.current_status", "shipment.eta", "shipment.tracking"),
        )
        self.assertFalse(any("courier.contact.phone" in values for values in aliases.values()))

    def test_build_and_validate_full_a2b_candidate(self) -> None:
        root = Path(__file__).resolve().parents[2]
        service_root = root / "services" / "agent-service"
        python = service_root / ".venv" / "bin" / "python"
        if not python.is_file():
            python = Path(sys.executable)

        subprocess.run(
            [str(python), "-B", "scripts/temp_v2018_a2b_builder.py"],
            cwd=root,
            check=True,
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join((
            str(service_root),
            str(service_root / "src"),
        ))
        subprocess.run(
            [
                str(python), "-B", "-m", "pytest", "-q",
                "tests/architecture/test_semantic_single_writer_invariants.py",
                "tests/runtime/test_semantic_output_coverage.py",
                "tests/runtime/test_unified_semantic_planning_contract.py",
                "tests/runtime/test_capability_contract_v2.py",
                "tests/runtime/test_unsupported_capability_surface_binding.py",
                "tests/runtime/test_wp08_attempt4_release_repairs.py",
                "tests/runtime/test_wp08_attempt5_dependency_authority.py",
            ],
            cwd=service_root,
            env=env,
            check=True,
        )
        _emit_candidate_archive(root)


if __name__ == "__main__":
    unittest.main()
