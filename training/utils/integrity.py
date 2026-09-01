"""Safe checkpoint deserialization helpers."""

from __future__ import annotations

import logging
import pickle
from typing import Any

import torch

_LOGGER = logging.getLogger(__name__)


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
        if isinstance(current, pickle.UnpicklingError) and str(current).startswith("Weights only load failed."):
            return True
        if any(marker in str(current) for marker in _WEIGHTS_ONLY_ERROR_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def safe_load_checkpoint(path: str, *, map_location: str | torch.device = "cpu") -> dict:
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


def safe_load_skrl_checkpoint(path: str, *, agent: Any) -> None:
    """Load an SKRL agent checkpoint without invoking its unsafe file loader.

    Args:
        path: Path to the checkpoint file.
        agent: SKRL agent whose checkpoint modules receive the loaded state.
    """
    modules = safe_load_checkpoint(path, map_location=agent.device)
    for name, data in modules.items():
        module = agent.checkpoint_modules.get(name)
        if module is None:
            _LOGGER.warning("Skipping checkpoint module %s because the SKRL agent has no matching instance", name)
            continue
        if not hasattr(module, "load_state_dict"):
            raise NotImplementedError(f"SKRL checkpoint module {name} does not support load_state_dict")
        module.load_state_dict(data)
        if hasattr(module, "eval"):
            module.eval()


def safe_load_rsl_rl_checkpoint(
    path: str,
    *,
    runner: Any,
    load_cfg: dict[str, bool] | None = None,
    strict: bool = True,
) -> Any:
    """Load an RSL-RL runner checkpoint without invoking its unsafe file loader.

    Args:
        path: Path to the checkpoint file.
        runner: RSL-RL runner whose algorithm receives the loaded state.
        load_cfg: Optional RSL-RL model and state selection.
        strict: Whether model state loading is strict.

    Returns:
        The checkpoint ``infos`` value.
    """
    checkpoint = safe_load_checkpoint(path, map_location=runner.device)
    if runner.alg.load(checkpoint, load_cfg, strict):
        runner.current_learning_iteration = checkpoint["iter"]
    return checkpoint["infos"]
