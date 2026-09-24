"""Bus-only torque-off recovery after an unexpected worker exit."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .resources import ArmResource, CleanupReport, ResourceSafetyError, ResourceTransaction


def deenergize_arms(resources: Sequence[ArmResource]) -> CleanupReport:
    """Acquire arm buses with torque disabled, then release in reverse order."""
    transaction = ResourceTransaction(resources)
    transaction.acquire_all()
    report = transaction.release_all()
    torque_verified_off = all(resource.torque_verified_off for resource in resources)
    return CleanupReport(
        cleanup_complete=report.cleanup_complete and torque_verified_off,
        released=report.released,
        errors=report.errors,
        torque_verified_off=torque_verified_off,
    )


def deenergize_profile(
    profile: Any,
    *,
    resource_factory: Callable[[Any], Sequence[ArmResource]] | None = None,
) -> CleanupReport:
    """Build bus-only resources from an authorized profile and deenergize them."""
    if resource_factory is None:
        raise ResourceSafetyError("Profile deenergization requires an arm resource factory")
    return deenergize_arms(resource_factory(profile))
