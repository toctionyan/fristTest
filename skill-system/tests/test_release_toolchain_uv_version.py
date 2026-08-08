from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts" / "release_toolchain_contract.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("release_toolchain_contract_uv_regression", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_uv_version_output_normalizes_platform_suffix_without_relaxing_version_identity() -> None:
    contract = _load_module()
    assert contract._normalize_uv_version_output("uv 0.11.29") == "0.11.29"
    assert (
        contract._normalize_uv_version_output("uv 0.11.29 (x86_64-unknown-linux-gnu)")
        == "0.11.29"
    )
    assert (
        contract._normalize_uv_version_output("uv 0.11.30 (x86_64-unknown-linux-gnu)")
        == "0.11.30"
    )
    with pytest.raises(Exception, match="unexpected uv --version output"):
        contract._normalize_uv_version_output("uv 0.11.29 injected-suffix")
