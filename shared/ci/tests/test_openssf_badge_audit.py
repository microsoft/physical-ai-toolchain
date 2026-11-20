# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Unit tests for the OpenSSF Best Practices badge audit collector."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPOSITORY_ROOT / ".github/skills/openssf-badge-audit/scripts/audit_badge.py"


def _load_audit() -> ModuleType:
    """Load the collector script as an importable module."""
    spec = importlib.util.spec_from_file_location("openssf_badge_audit", _SCRIPT_PATH)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_AUDIT = _load_audit()


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _project(**overrides: object) -> dict[str, object]:
    project: dict[str, object] = {
        "id": 12195,
        "name": "Physical AI Toolchain",
        "repo_url": "https://github.com/microsoft/physical-ai-toolchain",
        "updated_at": "2026-05-16T22:30:42.526Z",
        "badge_level": "silver",
        "tiered_percentage": 243,
        "badge_percentage_0": 100,
        "badge_percentage_1": 100,
        "badge_percentage_2": 43,
    }
    project.update({f"{criterion}_status": "Met" for criterion in _AUDIT._CRITERIA})
    project.update(overrides)
    return project


def _badge(**overrides: object) -> dict[str, object]:
    badge: dict[str, object] = {
        "id": 12195,
        "name": "Physical AI Toolchain",
        "updated_at": "2026-05-16T22:30:42.526Z",
        "badge_level": "silver",
        "tiered_percentage": 243,
    }
    badge.update(overrides)
    return badge


def _proposal(**overrides: object) -> dict[str, object]:
    proposal: dict[str, object] = {f"{criterion}_status": "Met" for criterion in _AUDIT._CRITERIA}
    proposal.update(overrides)
    return proposal


class TestFetchJson:
    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            (b"{invalid", "not valid JSON"),
            (b"\xff", "not UTF-8"),
            (b"[]", "must be a JSON object"),
        ],
    )
    def test_rejects_invalid_payloads(
        self,
        monkeypatch: pytest.MonkeyPatch,
        payload: bytes,
        message: str,
    ) -> None:
        monkeypatch.setattr(_AUDIT, "urlopen", lambda *_args, **_kwargs: _Response(payload))

        with pytest.raises(_AUDIT.AuditError, match=message):
            _AUDIT.fetch_json("https://example.invalid/project.json")


class TestReadRepositoryProposal:
    def test_rejects_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(_AUDIT.AuditError, match="Required repository proposal is missing"):
            _AUDIT.read_repository_proposal(tmp_path)

    def test_rejects_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / ".bestpractices.json").write_text("{invalid", encoding="utf-8")

        with pytest.raises(_AUDIT.AuditError, match="is not valid JSON"):
            _AUDIT.read_repository_proposal(tmp_path)


class TestValidateLiveData:
    @pytest.mark.parametrize(
        ("project_overrides", "badge_overrides", "message"),
        [
            ({"id": 999}, {}, "identity does not match"),
            ({"repo_url": "https://example.invalid/repository"}, {}, "repository URL does not match"),
            ({"id": True}, {}, "required integer field"),
            ({"badge_percentage_0": 101}, {}, "must be between 0 and 100"),
            ({}, {"updated_at": "not-a-timestamp"}, "invalid updated_at timestamp"),
        ],
    )
    def test_rejects_identity_and_shape_errors(
        self,
        project_overrides: dict[str, object],
        badge_overrides: dict[str, object],
        message: str,
    ) -> None:
        with pytest.raises(_AUDIT.AuditError, match=message):
            _AUDIT.validate_live_data(_project(**project_overrides), _badge(**badge_overrides))

    @pytest.mark.parametrize(
        ("badge_overrides", "message"),
        [
            ({"badge_level": "passing"}, "disagree on badge_level"),
            ({"tiered_percentage": 200}, "disagree on tiered_percentage"),
            ({"updated_at": "2026-05-17T00:00:00Z"}, "disagree on updated_at"),
        ],
    )
    def test_rejects_endpoint_disagreement(
        self,
        badge_overrides: dict[str, object],
        message: str,
    ) -> None:
        with pytest.raises(_AUDIT.AuditError, match=message):
            _AUDIT.validate_live_data(_project(), _badge(**badge_overrides))


class TestValidateProposalFields:
    def test_rejects_missing_criterion(self) -> None:
        proposal = _proposal()
        del proposal["crypto_random_status"]

        with pytest.raises(_AUDIT.AuditError, match=r"missing=.*crypto_random"):
            _AUDIT.validate_proposal_fields(_project(), proposal)

    def test_rejects_unexpected_criterion(self) -> None:
        proposal = _proposal(unexpected_status="Met")

        with pytest.raises(_AUDIT.AuditError, match=r"unexpected=.*unexpected"):
            _AUDIT.validate_proposal_fields(_project(), proposal)

    def test_rejects_invalid_repository_status(self) -> None:
        proposal = _proposal(crypto_random_status="Unsupported")

        with pytest.raises(_AUDIT.AuditError, match="unsupported status"):
            _AUDIT.validate_proposal_fields(_project(), proposal)

    def test_rejects_invalid_live_status(self) -> None:
        project = _project(crypto_random_status="Unsupported")

        with pytest.raises(_AUDIT.AuditError, match="unsupported status"):
            _AUDIT.validate_proposal_fields(project, _proposal())


class TestReport:
    def test_difference_order_is_deterministic(self) -> None:
        project = _project(crypto_random_status="N/A", build_status="Unmet")
        proposal = _proposal(
            crypto_random_status="Met",
            crypto_random_justification="CSPRNG evidence",
            build_status="Met",
            build_justification="Build evidence",
        )

        differences = _AUDIT.compare_proposals(project, proposal)

        assert [difference["field"] for difference in differences] == [
            "build_justification",
            "build_status",
            "crypto_random_justification",
            "crypto_random_status",
        ]

    def test_unresolved_statuses_include_unknown_on_either_side(self) -> None:
        project = _project(build_status="?", crypto_random_status="Met")
        proposal = _proposal(build_status="Met", crypto_random_status="?")

        unresolved = _AUDIT.unresolved_statuses(project, proposal)

        assert unresolved == [
            {"field": "build_status", "live": "?", "repository_proposal": "Met"},
            {"field": "crypto_random_status", "live": "Met", "repository_proposal": "?"},
        ]


class TestMain:
    def test_success_uses_mocked_http_and_filesystem_boundaries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        project = _project()
        badge = _badge()
        proposal = _proposal()
        responses = {_AUDIT._PROJECT_URL: project, _AUDIT._BADGE_URL: badge}
        monkeypatch.setattr(_AUDIT, "fetch_json", responses.__getitem__)
        monkeypatch.setattr(_AUDIT, "find_repository_root", lambda _script_path: tmp_path)
        monkeypatch.setattr(_AUDIT, "read_repository_proposal", lambda repository_root: proposal)

        assert _AUDIT.main() == 0

        report = json.loads(capsys.readouterr().out)
        assert report["live_badge"]["level"] == "silver"
        assert report["repository_proposal_differences"] == []
        assert report["unresolved_non_osps_status_fields"] == []

    def test_validation_failure_returns_stable_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        responses = {_AUDIT._PROJECT_URL: _project(id=999), _AUDIT._BADGE_URL: _badge()}
        monkeypatch.setattr(_AUDIT, "fetch_json", responses.__getitem__)
        monkeypatch.setattr(_AUDIT, "find_repository_root", lambda _script_path: tmp_path)
        monkeypatch.setattr(_AUDIT, "read_repository_proposal", lambda repository_root: _proposal())

        assert _AUDIT.main() == 1
        assert "identity does not match" in capsys.readouterr().err
