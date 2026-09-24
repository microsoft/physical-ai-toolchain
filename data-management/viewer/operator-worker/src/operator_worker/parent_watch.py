"""Linux parent-death handling for the operator worker."""

from __future__ import annotations

import ctypes
import os
import signal

_PR_SET_PDEATHSIG = 1


def arm_parent_death_signal(expected_parent_pid: int) -> None:
    """Request SIGTERM on parent loss and close the registration race."""
    if expected_parent_pid <= 0:
        raise ValueError("Expected parent PID must be positive")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    if os.getppid() != expected_parent_pid:
        os.kill(os.getpid(), signal.SIGTERM)
