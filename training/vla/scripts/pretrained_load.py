"""Fail closed when LeRobot returns a pretrained policy without its loaded weights."""

from __future__ import annotations

import contextlib
import weakref
from collections.abc import Iterator
from types import ModuleType
from typing import Any


class PretrainedLoadError(RuntimeError):
    """Raised when a pretrained policy is returned without completing its state-dict load."""


@contextlib.contextmanager
def require_complete_pretrained_load(training_module: ModuleType) -> Iterator[None]:
    """Require each pretrained policy built by ``training_module.make_policy`` to load its weights.

    LeRobot's PI policy loaders catch weight-loading errors, print a warning, and return
    the randomly initialized policy. While a pretrained policy is created, this guard
    records ``load_state_dict`` outcomes and raises unless the returned policy completed one.

    Args:
        training_module: Imported ``lerobot.scripts.lerobot_train`` module whose
            ``make_policy`` reference is wrapped for the duration of the context.

    Raises:
        PretrainedLoadError: The policy skipped or failed its state-dict load.
    """
    original_make_policy = training_module.make_policy

    def guarded_make_policy(*args: Any, **kwargs: Any) -> Any:
        policy_config = kwargs.get("cfg", args[0] if args else None)
        pretrained_path = getattr(policy_config, "pretrained_path", None)
        if not pretrained_path:
            return original_make_policy(*args, **kwargs)

        import torch

        outcomes: weakref.WeakKeyDictionary[torch.nn.Module, Exception | None] = weakref.WeakKeyDictionary()
        original_load_state_dict = torch.nn.Module.load_state_dict

        def recording_load_state_dict(module: torch.nn.Module, *load_args: Any, **load_kwargs: Any) -> Any:
            try:
                result = original_load_state_dict(module, *load_args, **load_kwargs)
            except Exception as error:
                outcomes[module] = error
                raise
            outcomes[module] = None
            return result

        torch.nn.Module.load_state_dict = recording_load_state_dict
        try:
            policy = original_make_policy(*args, **kwargs)
        finally:
            torch.nn.Module.load_state_dict = original_load_state_dict

        get_base_model = getattr(policy, "get_base_model", None)
        loaded_policy = get_base_model() if callable(get_base_model) else policy
        policy_name = type(loaded_policy).__name__
        if loaded_policy not in outcomes:
            raise PretrainedLoadError(
                f"{policy_name} was returned without loading pretrained weights from {pretrained_path}"
            )
        load_error = outcomes[loaded_policy]
        if load_error is not None:
            raise PretrainedLoadError(
                f"{policy_name} failed to load pretrained weights from {pretrained_path}: {load_error}"
            ) from load_error
        return policy

    training_module.make_policy = guarded_make_policy
    try:
        yield
    finally:
        training_module.make_policy = original_make_policy
