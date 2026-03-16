"""Isaac Sim 4.x shutdown workaround for containerized training.

See docs/gpu-configuration.md § "Isaac Sim 4.x Shutdown Fix" for background.
"""

from __future__ import annotations

import logging
import os
import signal

_LOGGER = logging.getLogger(__name__)

_SHUTDOWN_TIMEOUT_SEC = 30


def prepare_for_shutdown(timeout: int = _SHUTDOWN_TIMEOUT_SEC) -> None:
    """Neutralize the stop-handle callback and arm a watchdog before env.close()."""
    _disable_stop_handler()
    _unsubscribe_stop_callback()
    _start_shutdown_watchdog(timeout)


def _disable_stop_handler() -> None:
    """Set ``_disable_app_control_on_stop_handle`` on the active SimulationContext."""
    try:
        from isaaclab.sim import SimulationContext

        sim = SimulationContext.instance()
        if sim is not None:
            sim._disable_app_control_on_stop_handle = True
            _LOGGER.info("Disabled app_control_on_stop_handle for clean shutdown")
        else:
            _LOGGER.warning("SimulationContext.instance() returned None; stop-handler flag not set")
    except Exception:
        _LOGGER.warning("Failed to access SimulationContext; stop-handler flag not set", exc_info=True)


def _unsubscribe_stop_callback() -> None:
    """Unsubscribe the ``_app_control_on_stop_handle`` timeline callback."""
    try:
        from isaaclab.sim import SimulationContext

        sim = SimulationContext.instance()
        if sim is None:
            return
        handle = getattr(sim, "_app_control_on_stop_handle", None)
        if handle is not None:
            handle.unsubscribe()
            sim._app_control_on_stop_handle = None
            _LOGGER.info("Unsubscribed _app_control_on_stop_handle callback")
        else:
            _LOGGER.debug("_app_control_on_stop_handle was already None")
    except Exception:
        _LOGGER.warning("Failed to unsubscribe stop callback", exc_info=True)


def _start_shutdown_watchdog(timeout: int) -> None:
    """Fork a watchdog process that sends SIGKILL after ``timeout`` seconds."""
    parent_pid = os.getpid()

    child_pid = os.fork()
    if child_pid == 0:
        import time

        time.sleep(timeout)
        try:
            os.kill(parent_pid, signal.SIGKILL)
        except ProcessLookupError:
            # Parent process already exited; nothing left to kill
            _LOGGER.debug("Parent process %d already exited", parent_pid)
        os._exit(0)
    else:
        _LOGGER.info("Shutdown watchdog forked (pid=%d, %ds timeout)", child_pid, timeout)
