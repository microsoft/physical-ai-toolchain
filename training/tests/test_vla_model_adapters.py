"""Tests for VLA model-adapter resolution."""

from __future__ import annotations

import pytest

from training.vla.scripts.model_adapters import AdapterError, AdapterRequest, get_adapter


@pytest.mark.parametrize("train_expert_only", [False, True])
def test_given_pi_trainable_scope_when_resolved_then_value_is_preserved(train_expert_only: bool) -> None:
    resolution = get_adapter("lerobot-pi").resolve(
        AdapterRequest(policy_type="pi05", train_expert_only=train_expert_only)
    )

    assert resolution.environment["TRAIN_EXPERT_ONLY"] == str(train_expert_only).lower()


def test_given_smolvla_scope_omitted_when_resolved_then_expert_only_default_is_used() -> None:
    resolution = get_adapter("lerobot-smolvla").resolve(AdapterRequest(policy_type="smolvla"))

    assert resolution.environment["TRAIN_EXPERT_ONLY"] == "true"


def test_given_smolvla_policy_dtype_when_resolved_then_request_is_rejected() -> None:
    with pytest.raises(AdapterError, match="does not expose the PI policy dtype"):
        get_adapter("lerobot-smolvla").resolve(AdapterRequest(policy_type="smolvla", policy_dtype="bfloat16"))
