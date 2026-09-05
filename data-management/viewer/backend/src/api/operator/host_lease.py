"""Cooperative Linux host lease for operator hardware ownership."""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path


class HostLeaseError(RuntimeError):
    """Raised when the operator host lease cannot be acquired safely."""


class OperatorHostLease:
    """Retain an exclusive flock for the application lifetime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    @property
    def fd(self) -> int | None:
        """Return the retained lease descriptor after acquisition."""
        return self._fd

    def acquire(self) -> None:
        if self.path.is_symlink():
            raise HostLeaseError("Operator lease path must not be a symlink")
        if not self.path.parent.is_dir():
            raise HostLeaseError("Operator lease parent does not exist")
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise HostLeaseError("Operator lease path must be a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as error:
            os.close(fd)
            raise HostLeaseError("Operator host lease is already held") from error
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None
