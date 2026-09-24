"""Behavior tests for exclusive operator host ownership."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.api.operator.host_lease import HostLeaseError, OperatorHostLease


def test_given_two_process_owners_when_acquiring_same_lease_then_second_is_rejected(tmp_path: Path) -> None:
    # Arrange
    first = OperatorHostLease(tmp_path / "operator.lock")
    second = OperatorHostLease(tmp_path / "operator.lock")

    # Act
    first.acquire()

    # Assert
    try:
        with pytest.raises(HostLeaseError, match="already held"):
            second.acquire()
    finally:
        first.release()


def test_given_released_lease_when_reacquired_then_new_owner_succeeds(tmp_path: Path) -> None:
    # Arrange
    first = OperatorHostLease(tmp_path / "operator.lock")
    second = OperatorHostLease(tmp_path / "operator.lock")

    # Act
    first.acquire()
    first.release()
    second.acquire()

    # Assert
    try:
        assert second.fd is not None
    finally:
        second.release()
