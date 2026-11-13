"""Tests for the fail-closed pretrained policy load guard."""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from training.vla.scripts.pretrained_load import PretrainedLoadError, require_complete_pretrained_load

torch = pytest.importorskip("torch")


class _Policy(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.expert = torch.nn.Linear(2, 2)


class _PeftWrapper:
    def __init__(self, base_model: torch.nn.Module) -> None:
        self._base_model = base_model

    def get_base_model(self) -> torch.nn.Module:
        return self._base_model


def _weights(input_width: int = 2) -> dict[str, torch.Tensor]:
    return {"expert.weight": torch.ones(2, input_width), "expert.bias": torch.zeros(2)}


def _pi_loader(state_dict: dict[str, torch.Tensor]) -> _Policy:
    """Mirror LeRobot PI loaders, which print and continue when a state-dict load fails."""
    policy = _Policy()
    try:
        policy.load_state_dict(state_dict, strict=True)
    except RuntimeError as error:
        print(f"Warning: Could not load state dict: {error}")
    return policy


def _skipped_load(cfg: Any, **_: Any) -> _Policy:
    print("Returning model without loading pretrained weights")
    return _Policy()


def _training_module(make_policy: Callable[..., Any]) -> ModuleType:
    module = ModuleType("lerobot_train")
    module.make_policy = make_policy
    return module


def _pretrained(path: str | None = "/models/pi05_base") -> SimpleNamespace:
    return SimpleNamespace(pretrained_path=path)


@pytest.mark.parametrize(
    "make_policy",
    [
        pytest.param(lambda cfg, **_: _pi_loader(_weights()), id="policy"),
        pytest.param(lambda cfg, **_: _PeftWrapper(_pi_loader(_weights())), id="peft-wrapped-policy"),
    ],
)
def test_given_matching_weights_when_policy_built_then_it_is_returned(make_policy: Callable[..., Any]) -> None:
    module = _training_module(make_policy)

    with require_complete_pretrained_load(module):
        policy = module.make_policy(cfg=_pretrained(), ds_meta=None)

    loaded = policy.get_base_model() if isinstance(policy, _PeftWrapper) else policy
    assert torch.equal(loaded.expert.weight, torch.ones(2, 2))
    assert module.make_policy is make_policy


@pytest.mark.parametrize(
    ("make_policy", "message"),
    [
        pytest.param(
            lambda cfg, **_: _pi_loader(_weights(input_width=3)),
            "failed to load pretrained weights",
            id="state-dict-error-swallowed",
        ),
        pytest.param(_skipped_load, "without loading pretrained weights", id="load-skipped"),
        pytest.param(
            lambda cfg, **_: _PeftWrapper(_Policy()),
            "without loading pretrained weights",
            id="peft-base-not-loaded",
        ),
    ],
)
def test_given_policy_without_its_pretrained_weights_when_built_then_training_stops(
    make_policy: Callable[..., Any], message: str
) -> None:
    module = _training_module(make_policy)

    with require_complete_pretrained_load(module), pytest.raises(PretrainedLoadError, match=message):
        module.make_policy(cfg=_pretrained(), ds_meta=None)

    assert module.make_policy is make_policy


def test_given_swallowed_state_dict_error_when_rejected_then_original_error_is_preserved() -> None:
    module = _training_module(lambda cfg, **_: _pi_loader(_weights(input_width=3)))

    with require_complete_pretrained_load(module), pytest.raises(PretrainedLoadError) as caught:
        module.make_policy(_pretrained())

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "size mismatch" in str(caught.value.__cause__)


def test_given_only_a_submodule_loaded_when_policy_built_then_policy_load_is_still_required() -> None:
    def make_policy(cfg: Any, **_: Any) -> _Policy:
        policy = _Policy()
        policy.expert.load_state_dict({"weight": torch.ones(2, 2), "bias": torch.zeros(2)})
        return policy

    module = _training_module(make_policy)

    with require_complete_pretrained_load(module), pytest.raises(PretrainedLoadError, match="without loading"):
        module.make_policy(cfg=_pretrained())


@pytest.mark.parametrize("pretrained_path", [None, ""])
def test_given_policy_trained_from_scratch_when_built_then_no_load_is_required(pretrained_path: str | None) -> None:
    module = _training_module(lambda cfg, **_: _Policy())

    with require_complete_pretrained_load(module):
        policy = module.make_policy(_pretrained(pretrained_path))

    assert isinstance(policy, _Policy)


def test_given_policy_construction_error_when_guarded_then_error_propagates_and_hooks_are_restored() -> None:
    original_load_state_dict = torch.nn.Module.load_state_dict

    def make_policy(cfg: Any, **_: Any) -> _Policy:
        raise ValueError("invalid policy configuration")

    module = _training_module(make_policy)

    with require_complete_pretrained_load(module), pytest.raises(ValueError, match="invalid policy configuration"):
        module.make_policy(cfg=_pretrained())

    assert torch.nn.Module.load_state_dict is original_load_state_dict
    assert module.make_policy is make_policy
