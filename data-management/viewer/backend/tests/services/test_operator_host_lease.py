"""Behavior tests for cooperative host-level operator ownership."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.api.operator.host_lease import HostLeaseError, OperatorHostLease


def test_second_owner_is_rejected_until_release(tmp_path: Path) -> None:
    path = tmp_path / "operator.lock"
    first = OperatorHostLease(path)
    second = OperatorHostLease(path)

    first.acquire()
    with pytest.raises(HostLeaseError, match="already held"):
        second.acquire()
    first.release()
    second.acquire()
    second.release()


def test_symlink_lease_path_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.lock"
    target.touch()
    link = tmp_path / "operator.lock"
    link.symlink_to(target)

    with pytest.raises(HostLeaseError, match="symlink"):
        OperatorHostLease(link).acquire()
