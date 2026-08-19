"""Safe checkpoint deserialization helpers.

Use ``safe_load_checkpoint`` for direct PyTorch loads and
``safe_load_framework_checkpoint`` for framework-owned loaders.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import RLock
from typing import Any, TypeVar

import torch

T = TypeVar("T")

_FORCE_WEIGHTS_ONLY_ENV = "TORCH_FORCE_WEIGHTS_ONLY_LOAD"
_DISABLE_WEIGHTS_ONLY_ENV = "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"
_FORCE_WEIGHTS_ONLY_LOCK = RLock()


# PyTorch serialization uses these messages for restricted-unpickler failures.
_WEIGHTS_ONLY_ERROR_MARKERS = (
    "Unsupported global:",
    "add_safe_globals",
    "allowlisted via",
    "Can not safely load weights when explicit pickle_module is specified",
)


def _checkpoint_load_error(path: str, error: Exception) -> ValueError:
    return ValueError(
        f"Checkpoint {path} could not be loaded under weights_only=True (safe unpickler). "
        "If it is a trusted framework checkpoint storing non-tensor objects outside model_state_dict, "
        "allowlist those types with torch.serialization.add_safe_globals([...]); do not set "
        f"weights_only=False. Underlying error: {error}"
    )


def _is_weights_only_error(error: Exception) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if any(marker in str(current) for marker in _WEIGHTS_ONLY_ERROR_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def _set_environment_value(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _restore_environment_value(name: str, expected: str | None, previous: str | None) -> None:
    if os.environ.get(name) == expected:
        _set_environment_value(name, previous)


def safe_load_checkpoint(path: str, *, map_location: str = "cpu") -> dict:
    """Load a checkpoint under ``weights_only=True``, failing with actionable guidance.

    ``weights_only=True`` runs the restricted unpickler over the whole checkpoint, so a
    trusted framework checkpoint that stores non-tensor objects (e.g. a numpy scalar in
    ``infos``) alongside ``model_state_dict`` is rejected even though only tensors are read.
    Surface that as a clear error steering the operator to allowlist the offending type
    rather than disabling the safeguard, which would reopen the pickle-RCE vector.

    Args:
        path: Path to the checkpoint file.
        map_location: Map location for torch.load.

    Returns:
        The loaded checkpoint dict.

    Raises:
        ValueError: If the safe unpickler rejects the checkpoint.
    """
    try:
        checkpoint = torch.load(path, map_location=map_location, weights_only=True)
    except Exception as error:
        if _is_weights_only_error(error):
            raise _checkpoint_load_error(path, error) from error
        raise
    if not isinstance(checkpoint, dict):
        raise ValueError(f"Checkpoint {path} must contain a dictionary, got {type(checkpoint).__name__}")
    return checkpoint


@contextmanager
def _force_weights_only_load() -> Iterator[Callable[[], bool]]:
    """Force safe PyTorch loads and report whether the loader used ``torch.load``."""
    with _FORCE_WEIGHTS_ONLY_LOCK:
        previous_force = os.environ.get(_FORCE_WEIGHTS_ONLY_ENV)
        previous_disable = os.environ.get(_DISABLE_WEIGHTS_ONLY_ENV)
        original_torch_load = torch.load
        torch_load_invoked = False

        def safe_torch_load(*args: Any, **kwargs: Any) -> Any:
            nonlocal torch_load_invoked
            torch_load_invoked = True
            kwargs["weights_only"] = True
            return original_torch_load(*args, **kwargs)

        _set_environment_value(_FORCE_WEIGHTS_ONLY_ENV, "1")
        _set_environment_value(_DISABLE_WEIGHTS_ONLY_ENV, None)
        torch.load = safe_torch_load
        try:
            yield lambda: torch_load_invoked
        finally:
            if torch.load is safe_torch_load:
                torch.load = original_torch_load
            _restore_environment_value(_FORCE_WEIGHTS_ONLY_ENV, "1", previous_force)
            _restore_environment_value(_DISABLE_WEIGHTS_ONLY_ENV, None, previous_disable)


def safe_load_framework_checkpoint(path: str, *, loader: Callable[[str], T]) -> T:  # noqa: UP047
    """Run a framework checkpoint loader with PyTorch's restricted unpickler forced on.

    The process-wide overrides apply to the framework's synchronous ``torch.load``
    call, including call sites that explicitly request ``weights_only=False``.
    Calls through this helper are serialized, and unrelated concurrent
    ``torch.load`` calls are also forced into safe mode while the loader runs.

    Args:
        path: Path to the checkpoint file.
        loader: Framework checkpoint-loading callable.

    Returns:
        The framework loader's return value.

    Raises:
        ValueError: If the safe unpickler rejects the checkpoint.
        RuntimeError: If the framework loader does not call torch.load synchronously.
    """
    try:
        with _force_weights_only_load() as was_torch_load_invoked:
            result = loader(path)
            if not was_torch_load_invoked():
                raise RuntimeError(
                    f"Framework loader for checkpoint {path} did not call torch.load synchronously; "
                    "safe deserialization could not be enforced"
                )
            return result
    except Exception as error:
        if _is_weights_only_error(error):
            raise _checkpoint_load_error(path, error) from error
        raise
