from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load_module():
    path = ROOT / "skill-system" / "controller" / "bounded_batch.py"
    spec = importlib.util.spec_from_file_location("bounded_remote_read_batch_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _request(module, key: str, *, source: str = "github", ref: str = "abc123", depends_on=()):
    return module.ReadRequest(
        key=key,
        source=source,
        ref=ref,
        path=f"src/{key}.py",
        depends_on=tuple(depends_on),
    )


def test_independent_reads_are_chunked_to_hard_parallel_cap() -> None:
    module = _load_module()
    requests = [_request(module, f"r{index}") for index in range(7)]

    plan = module.plan_read_batches(requests, max_parallel=4)

    assert [batch.size for batch in plan] == [4, 3]
    assert module.plan_metrics(plan) == {
        "batch_count": 2,
        "request_count": 7,
        "max_parallel_width": 4,
    }


def test_dependency_descendant_is_never_batched_with_its_prerequisite() -> None:
    module = _load_module()
    requests = [
        _request(module, "a"),
        _request(module, "b"),
        _request(module, "c", depends_on=("a",)),
        _request(module, "d", depends_on=("b",)),
    ]

    plan = module.plan_read_batches(requests, max_parallel=4)

    assert [batch.keys for batch in plan] == [("a", "b"), ("c", "d")]


def test_different_connector_or_ref_boundaries_do_not_share_batch() -> None:
    module = _load_module()
    requests = [
        _request(module, "a", source="github", ref="sha-1"),
        _request(module, "b", source="github", ref="sha-2"),
        _request(module, "c", source="drive", ref="doc-v1"),
    ]

    plan = module.plan_read_batches(requests, max_parallel=4)

    assert [(batch.source, batch.ref, batch.keys) for batch in plan] == [
        ("github", "sha-1", ("a",)),
        ("github", "sha-2", ("b",)),
        ("drive", "doc-v1", ("c",)),
    ]


def test_parallel_limit_cannot_exceed_controller_safety_cap() -> None:
    module = _load_module()

    with pytest.raises(module.BatchPlanError):
        module.plan_read_batches([_request(module, "a")], max_parallel=5)
    with pytest.raises(module.BatchPlanError):
        module.plan_read_batches([_request(module, "a")], max_parallel=0)


def test_unknown_dependency_fails_closed() -> None:
    module = _load_module()
    requests = [_request(module, "a", depends_on=("missing",))]

    with pytest.raises(module.BatchPlanError, match="unknown requests"):
        module.plan_read_batches(requests)


def test_dependency_cycle_fails_closed() -> None:
    module = _load_module()
    requests = [
        _request(module, "a", depends_on=("b",)),
        _request(module, "b", depends_on=("a",)),
    ]

    with pytest.raises(module.BatchPlanError, match="cyclic or unsatisfied"):
        module.plan_read_batches(requests)


def test_duplicate_request_keys_fail_closed() -> None:
    module = _load_module()
    requests = [_request(module, "a"), _request(module, "a")]

    with pytest.raises(module.BatchPlanError, match="must be unique"):
        module.plan_read_batches(requests)
