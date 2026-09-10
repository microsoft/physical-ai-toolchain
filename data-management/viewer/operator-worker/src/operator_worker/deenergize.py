"""Bus-only torque-off recovery after an unexpected worker exit."""

from __future__ import annotations

from .acquisition import CleanupReport, ResourceTransaction
from .calibration import JOINTS, validate_calibration_file
from .config import WorkerProfile
from .identity import verify_tty_identity
from .resources import ArmResource


def deenergize_arms(resources: list[ArmResource]) -> CleanupReport:
    """Acquire arm buses with torque off and release them in reverse order."""
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


def deenergize_profile(profile: WorkerProfile) -> CleanupReport:
    """Construct only SO-101 buses and verify torque off on both arms."""
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig  # type: ignore[import-untyped]
    from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig  # type: ignore[import-untyped]

    leader = SO101Leader(
        SO101LeaderConfig(
            port=str(profile.leader.port),
            id=profile.leader.logical_id,
            calibration_dir=profile.leader.calibration_file.parent,
        )
    )
    follower = SO101Follower(
        SO101FollowerConfig(
            port=str(profile.follower.port),
            id=profile.follower.logical_id,
            calibration_dir=profile.follower.calibration_file.parent,
            cameras={},
            max_relative_target=profile.max_relative_target,
        )
    )

    def no_configuration(_bus: object) -> None:
        return

    return deenergize_arms(
        [
            ArmResource(
                "leader",
                leader,
                calibration_file=profile.leader.calibration_file,
                validate_calibration=validate_calibration_file,
                motion_capable=False,
                configure_torque_off=no_configuration,
                verify_identity=lambda: verify_tty_identity(
                    profile.leader.port,
                    (
                        profile.leader.usb_vendor_id,
                        profile.leader.usb_product_id,
                        profile.leader.usb_serial,
                    ),
                ),
                expected_motor_names=set(JOINTS),
            ),
            ArmResource(
                "follower",
                follower,
                calibration_file=profile.follower.calibration_file,
                validate_calibration=validate_calibration_file,
                motion_capable=False,
                configure_torque_off=no_configuration,
                verify_identity=lambda: verify_tty_identity(
                    profile.follower.port,
                    (
                        profile.follower.usb_vendor_id,
                        profile.follower.usb_product_id,
                        profile.follower.usb_serial,
                    ),
                ),
                expected_motor_names=set(JOINTS),
            ),
        ]
    )
