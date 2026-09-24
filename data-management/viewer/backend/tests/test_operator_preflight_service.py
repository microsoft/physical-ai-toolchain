"""Behavior tests for bounded preflight lifecycle resources."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.api.operator.models import (
    OperatorMode,
    PreflightCheck,
    PreflightCheckOutcome,
    PreflightLifecycle,
    PreflightRequest,
)
from src.api.services.operator_preflight_service import OperatorPreflightService


class _Runner:
    def run(self, _profile: object, *, mode: OperatorMode, upload_requested: bool) -> SimpleNamespace:
        return SimpleNamespace(
            checks=[PreflightCheck(name="worker_contract", outcome=PreflightCheckOutcome.PASSED, detail="verified")],
            resource_fingerprint=f"resource-{mode.value}-{upload_requested}",
            ownership_complete=True,
            start_eligible=True,
        )


def test_given_same_preflight_command_when_replayed_then_result_is_idempotent() -> None:
    # Arrange
    profile = SimpleNamespace(name="so101", fingerprint="profile-fingerprint")
    service = OperatorPreflightService(profiles={"so101": profile}, runner=_Runner())
    request = PreflightRequest(command_id="preflight-1", profile="so101", mode=OperatorMode.RECORD)

    # Act
    first = service.create(request)
    replay = service.create(request)

    # Assert
    assert replay == first


def test_given_expired_preflight_when_fetched_then_start_is_no_longer_eligible() -> None:
    # Arrange
    now = datetime(2026, 9, 24, tzinfo=UTC)
    profile = SimpleNamespace(name="so101", fingerprint="profile-fingerprint")
    service = OperatorPreflightService(
        profiles={"so101": profile},
        runner=_Runner(),
        now=lambda: now,
        ttl=timedelta(seconds=-1),
    )
    created = service.create(PreflightRequest(command_id="preflight-1", profile="so101", mode=OperatorMode.RECORD))

    # Act
    result = service.get(created.preflight_id)

    # Assert
    assert (result.lifecycle, result.start_eligible) == (PreflightLifecycle.EXPIRED, False)
