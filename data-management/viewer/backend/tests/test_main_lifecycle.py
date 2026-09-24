"""FastAPI lifecycle composition tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from src.api import main


def _operator_config(mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        operator_adapter_mode=mode,
        operator_host_lease_path="/tmp/operator.lock",
        data_path="/tmp/datasets",
        operator_worker_executable="/tmp/operator-worker",
        operator_policy_python="/opt/operator/python",
        operator_policy_checkpoint="/opt/operator/checkpoint",
        operator_policy_cuda_visible_devices="2",
        operator_command_timeout_s=5.0,
        operator_startup_timeout_s=30.0,
        operator_stop_timeout_s=5.0,
        operator_recovery_timeout_s=10.0,
    )


def _patch_lifecycle_factories(monkeypatch: pytest.MonkeyPatch, *, mode: str) -> SimpleNamespace:
    processor = MagicMock()
    processor.start = AsyncMock()
    processor.stop = AsyncMock()
    dataset_service = MagicMock()
    operator_service = MagicMock()
    operator_service.shutdown = AsyncMock()
    operator_service.status.return_value = SimpleNamespace(cleanup_unconfirmed=False)
    lease = MagicMock(fd=17)

    monkeypatch.setattr(main, "_config", _operator_config(mode))
    monkeypatch.setattr("src.api.release.processor.get_release_processor", lambda: processor)
    monkeypatch.setattr("src.api.operator.profiles.load_operator_profiles", lambda *, environ: {})
    host_lease_factory = MagicMock(return_value=lease)
    preflight_runner_factory = MagicMock()
    preflight_service_factory = MagicMock()
    operator_service_factory = MagicMock(return_value=operator_service)
    monkeypatch.setattr("src.api.operator.host_lease.OperatorHostLease", host_lease_factory)
    monkeypatch.setattr("src.api.operator.preflight.OperatorPreflightRunner", preflight_runner_factory)
    monkeypatch.setattr(
        "src.api.services.operator_preflight_service.OperatorPreflightService",
        preflight_service_factory,
    )
    monkeypatch.setattr("src.api.services.operator_service.OperatorService", operator_service_factory)
    monkeypatch.setattr("src.api.services.dataset_service.get_dataset_service", lambda: dataset_service)
    return SimpleNamespace(
        processor=processor,
        dataset_service=dataset_service,
        operator_service=operator_service,
        lease=lease,
        host_lease_factory=host_lease_factory,
        preflight_runner_factory=preflight_runner_factory,
        preflight_service_factory=preflight_service_factory,
        operator_service_factory=operator_service_factory,
    )


async def test_given_operator_setup_failure_when_lifespan_starts_then_acquired_resources_are_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    lifecycle = _patch_lifecycle_factories(monkeypatch, mode="lerobot")
    lifecycle.preflight_service_factory.side_effect = RuntimeError("preflight setup failed")

    # Act & Assert
    with pytest.raises(RuntimeError, match="preflight setup failed"):
        async with main.lifespan(FastAPI()):
            pytest.fail("Lifespan started after operator setup failed")

    lifecycle.processor.start.assert_awaited_once_with()
    lifecycle.lease.acquire.assert_called_once_with()
    lifecycle.lease.release.assert_called_once_with()
    lifecycle.processor.stop.assert_awaited_once_with()


async def test_given_operator_shutdown_failure_when_lifespan_stops_then_all_cleanup_is_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    lifecycle = _patch_lifecycle_factories(monkeypatch, mode="lerobot")
    cleanup_order = []

    async def fail_operator_shutdown() -> None:
        cleanup_order.append("operator")
        raise RuntimeError("operator shutdown failed")

    async def stop_processor() -> None:
        cleanup_order.append("processor")

    lifecycle.operator_service.shutdown.side_effect = fail_operator_shutdown
    lifecycle.lease.release.side_effect = lambda: cleanup_order.append("lease")
    lifecycle.processor.stop.side_effect = stop_processor
    lifecycle.dataset_service.cleanup_temp_dirs.side_effect = lambda: cleanup_order.append("dataset")

    # Act & Assert
    with pytest.raises(RuntimeError, match="operator shutdown failed"):
        async with main.lifespan(FastAPI()):
            pass

    lifecycle.lease.release.assert_called_once_with()
    lifecycle.processor.stop.assert_awaited_once_with()
    lifecycle.dataset_service.cleanup_temp_dirs.assert_called_once_with()
    assert cleanup_order == ["operator", "lease", "processor", "dataset"]


async def test_given_dataset_cleanup_failure_when_lifespan_stops_then_shutdown_remains_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    lifecycle = _patch_lifecycle_factories(monkeypatch, mode="disabled")
    lifecycle.dataset_service.cleanup_temp_dirs.side_effect = RuntimeError("dataset cleanup failed")

    # Act
    async with main.lifespan(FastAPI()):
        pass

    # Assert
    lifecycle.operator_service.shutdown.assert_awaited_once_with()
    lifecycle.processor.stop.assert_awaited_once_with()
    lifecycle.dataset_service.cleanup_temp_dirs.assert_called_once_with()


@pytest.mark.parametrize("mode", ["disabled", "simulated"])
async def test_given_non_physical_mode_when_lifespan_runs_then_no_lease_or_preflight_is_created(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    # Arrange
    lifecycle = _patch_lifecycle_factories(monkeypatch, mode=mode)
    lifecycle.operator_service.shutdown.side_effect = lambda: setattr(
        lifecycle.operator_service.status.return_value,
        "cleanup_unconfirmed",
        True,
    )
    app = FastAPI()

    # Act
    async with main.lifespan(app):
        assert app.state.operator_service is lifecycle.operator_service

    # Assert
    lifecycle.host_lease_factory.assert_not_called()
    lifecycle.preflight_runner_factory.assert_not_called()
    lifecycle.preflight_service_factory.assert_not_called()
    lifecycle.operator_service_factory.assert_called_once_with(
        adapter_mode=mode,
        preflight_service=None,
        worker_executable="/tmp/operator-worker",
        host_lease_fd=None,
        command_timeout_s=5.0,
        startup_timeout_s=30.0,
        stop_timeout_s=5.0,
        recovery_timeout_s=10.0,
        data_root=main.Path("/tmp/datasets"),
        policy_python="/opt/operator/python",
        policy_checkpoint="/opt/operator/checkpoint",
        policy_cuda_visible_devices="2",
    )
    lifecycle.processor.start.assert_awaited_once_with()
    lifecycle.operator_service.shutdown.assert_awaited_once_with()
    lifecycle.processor.stop.assert_awaited_once_with()
    lifecycle.dataset_service.cleanup_temp_dirs.assert_called_once_with()
    assert app.state.operator_service.status().cleanup_unconfirmed is True
