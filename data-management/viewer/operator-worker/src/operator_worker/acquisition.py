"""Transactional resource acquisition and reverse-order cleanup."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol


class ManagedResource(Protocol):
    """Resource contract used by the acquisition transaction."""

    name: str

    def acquire(self) -> None: ...

    def release(self) -> None: ...


class AcquisitionError(RuntimeError):
    """Raised when resource acquisition fails after rollback."""


@dataclass(frozen=True)
class CleanupReport:
    """Per-transaction cleanup result."""

    cleanup_complete: bool
    released: tuple[str, ...]
    errors: tuple[str, ...]
    torque_verified_off: bool = False


class ResourceTransaction:
    """Acquire resources in order and release them in reverse order."""

    def __init__(
        self,
        resources: Sequence[ManagedResource],
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        self._resources = list(resources)
        self._cancel_requested = cancel_requested or (lambda: False)
        self._acquired: list[ManagedResource] = []
        self._last_cleanup: CleanupReport | None = None

    def acquire_all(self) -> None:
        try:
            for resource in self._resources:
                if self._cancel_requested():
                    raise RuntimeError("Resource acquisition cancelled")
                self._acquired.append(resource)
                resource.acquire()
                if self._cancel_requested():
                    raise RuntimeError("Resource acquisition cancelled")
        except Exception as error:
            report = self.release_all()
            detail = f"Resource acquisition failed: {error}"
            if report.errors:
                detail += f"; cleanup errors: {report.errors}"
            raise AcquisitionError(detail) from error

    def release_all(self) -> CleanupReport:
        if not self._acquired and self._last_cleanup is not None:
            return self._last_cleanup
        released: list[str] = []
        errors: list[str] = []
        while self._acquired:
            resource = self._acquired.pop()
            try:
                resource.release()
                released.append(resource.name)
            except Exception as error:
                errors.append(f"{resource.name}: {error}")
        self._last_cleanup = CleanupReport(
            cleanup_complete=not errors,
            released=tuple(released),
            errors=tuple(errors),
        )
        return self._last_cleanup
