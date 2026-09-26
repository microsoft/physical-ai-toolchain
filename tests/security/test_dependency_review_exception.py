from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.security.check_dependency_review_exception import ExceptionEvaluation, evaluate_exception

# cspell:ignore xrqw
_FIXTURES = Path("scripts/tests/Fixtures/DependencyReview")
_ADVISORY = "GHSA-xrqw-3rrv-vx5w"
_MANIFEST = "gpu-offload/examples/so101-real-hardware/uv.lock"
_PACKAGE = "transformers"


def _load_fixture(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _evaluate(vulnerable_changes: object) -> ExceptionEvaluation:
    return evaluate_exception(
        vulnerable_changes,
        _load_fixture("clean-invalid-license-changes.json"),
        _load_fixture("clean-denied-changes.json"),
        advisory=_ADVISORY,
        manifest=_MANIFEST,
        package=_PACKAGE,
    )


def test_accepts_only_the_exact_scoped_exception() -> None:
    evaluation = _evaluate(_load_fixture("scoped-vulnerable-changes.json"))

    assert evaluation.accepted is True
    assert evaluation.scoped_exception_count == 1
    assert evaluation.remaining_vulnerabilities == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("change_type", "updated"),
        ("ecosystem", "npm"),
        ("name", "tokenizers"),
        ("manifest", "gpu-offload/runtime/uv.lock"),
    ],
)
def test_rejects_changes_outside_the_exact_package_manifest_scope(field: str, value: str) -> None:
    changes = copy.deepcopy(_load_fixture("scoped-vulnerable-changes.json"))
    assert isinstance(changes, list)
    changes[0][field] = value

    evaluation = _evaluate(changes)

    assert evaluation.accepted is False
    assert evaluation.scoped_exception_count == 0
    assert len(evaluation.remaining_vulnerabilities) == 1


def test_rejects_a_different_advisory() -> None:
    changes = copy.deepcopy(_load_fixture("scoped-vulnerable-changes.json"))
    assert isinstance(changes, list)
    changes[0]["vulnerabilities"][0]["advisory_ghsa_id"] = "GHSA-0000-0000-0000"

    evaluation = _evaluate(changes)

    assert evaluation.accepted is False
    assert evaluation.scoped_exception_count == 0
    assert len(evaluation.remaining_vulnerabilities) == 1


def test_rejects_additional_vulnerabilities() -> None:
    changes = copy.deepcopy(_load_fixture("scoped-vulnerable-changes.json"))
    assert isinstance(changes, list)
    changes[0]["vulnerabilities"].append({"advisory_ghsa_id": "GHSA-0000-0000-0000"})

    evaluation = _evaluate(changes)

    assert evaluation.accepted is False
    assert evaluation.scoped_exception_count == 1
    assert evaluation.remaining_vulnerabilities[0]["vulnerabilities"] == [{"advisory_ghsa_id": "GHSA-0000-0000-0000"}]


def test_rejects_license_or_denied_change_violations() -> None:
    changes = _load_fixture("scoped-vulnerable-changes.json")
    evaluation = evaluate_exception(
        changes,
        {"forbidden": [{"name": "blocked"}], "unresolved": []},
        [{"name": "denied"}],
        advisory=_ADVISORY,
        manifest=_MANIFEST,
        package=_PACKAGE,
    )

    assert evaluation.accepted is False
    assert evaluation.invalid_license_count == 1
    assert evaluation.denied_change_count == 1
