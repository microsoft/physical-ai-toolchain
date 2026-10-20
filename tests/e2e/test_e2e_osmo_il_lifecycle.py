"""
End-to-end lifecycle test for the OSMO IL (LeRobot/ACT) train -> eval path.

Runs against the platform profile ``PLATFORM_PROFILE`` selects. The azure profile stages a
synthetic dataset in Blob, trains briefly, registers the checkpoint under a unique AzureML model
name, and evaluates the concrete registered version (never ``latest``) against the same dataset.
The local profile trains on a dataset in the data root (``ur10e-gear-to-bin-learner``, or the
one ``E2E_IL_DATASET`` names), registers it in the local MLflow registry, and requires the trained
policy to beat its untrained baseline and its carrier image to reproduce the checkpoint's metrics.

Set ``E2E_LEROBOT_EVAL_POLICY_REPO_ID`` with ``E2E_LEROBOT_EVAL_POLICY_REVISION`` (a pinned
HuggingFace repo) or ``E2E_LEROBOT_EVAL_MODEL`` (registered model ``name:version``) to skip training
and evaluate a pre-existing policy — a fast inner loop while fixing the eval path.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_il_lifecycle.py
PLATFORM_PROFILE=local uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_il_lifecycle.py
```
"""

from __future__ import annotations

import pytest

from tests.e2e._common import log_e2e
from tests.e2e._lerobot_lifecycle import LeRobotScenario, run_lerobot_lifecycle
from tests.e2e._osmo import (
    _LEROBOT_EVAL_MODEL_ENV,
    _LEROBOT_EVAL_POLICY_REPO_ENV,
    _LEROBOT_EVAL_POLICY_REVISION_ENV,
    resolve_osmo_lerobot_eval_policy_override,
)
from tests.e2e._platform import E2EPlatform
from tests.e2e._profile import AZURE_PROFILE, LOCAL_PROFILE

# The azure scenario smoke-tests the pipeline on a spot A100 with a few steps on synthetic data. The local
# scenario trains long enough to beat the untrained baseline and evaluates the carrier image as well.
_SCENARIOS = {
    AZURE_PROFILE: LeRobotScenario(
        policy_type="act",
        training_steps=10,
        batch_size=8,
        save_freq=5,
        log_freq=1,
        learning_rate="1e-4",
        eval_episodes=1,
    ),
    LOCAL_PROFILE: LeRobotScenario(
        policy_type="act",
        training_steps=2000,
        batch_size=8,
        save_freq=1000,
        log_freq=50,
        learning_rate="1e-4",
        eval_episodes=3,
        data_root_dataset="ur10e-gear-to-bin-learner",
        dataset_variable="E2E_IL_DATASET",
        baseline_args=(),
        evaluate_carrier=True,
    ),
}


def test_resolve_osmo_lerobot_eval_policy_override_repo_forwards_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LEROBOT_EVAL_POLICY_REPO_ENV, "org/policy")
    monkeypatch.setenv(_LEROBOT_EVAL_POLICY_REVISION_ENV, "abc123")
    monkeypatch.delenv(_LEROBOT_EVAL_MODEL_ENV, raising=False)

    source = resolve_osmo_lerobot_eval_policy_override()

    assert source is not None
    assert source.args == ("--policy-repo-id", "org/policy", "--policy-revision", "abc123")
    assert source.description == "HuggingFace policy repo org/policy@abc123"


def test_resolve_osmo_lerobot_eval_policy_override_repo_requires_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LEROBOT_EVAL_POLICY_REPO_ENV, "org/policy")
    monkeypatch.delenv(_LEROBOT_EVAL_POLICY_REVISION_ENV, raising=False)
    monkeypatch.delenv(_LEROBOT_EVAL_MODEL_ENV, raising=False)

    with pytest.raises(pytest.skip.Exception):
        resolve_osmo_lerobot_eval_policy_override()


def test_resolve_osmo_lerobot_eval_policy_override_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LEROBOT_EVAL_POLICY_REPO_ENV, raising=False)
    monkeypatch.delenv(_LEROBOT_EVAL_POLICY_REVISION_ENV, raising=False)
    monkeypatch.setenv(_LEROBOT_EVAL_MODEL_ENV, "name:version")

    source = resolve_osmo_lerobot_eval_policy_override()

    assert source is not None
    assert source.args == ("--from-aml-model", "--model-name", "name", "--model-version", "version")
    assert source.description == "AzureML model name:version"


def test_resolve_osmo_lerobot_eval_policy_override_model_without_colon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LEROBOT_EVAL_POLICY_REPO_ENV, raising=False)
    monkeypatch.setenv(_LEROBOT_EVAL_MODEL_ENV, "name_only")

    with pytest.raises(pytest.skip.Exception):
        resolve_osmo_lerobot_eval_policy_override()


def test_resolve_osmo_lerobot_eval_policy_override_model_empty_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LEROBOT_EVAL_POLICY_REPO_ENV, raising=False)
    monkeypatch.setenv(_LEROBOT_EVAL_MODEL_ENV, "name:")

    with pytest.raises(pytest.skip.Exception):
        resolve_osmo_lerobot_eval_policy_override()


def test_resolve_osmo_lerobot_eval_policy_override_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LEROBOT_EVAL_POLICY_REPO_ENV, raising=False)
    monkeypatch.delenv(_LEROBOT_EVAL_MODEL_ENV, raising=False)

    assert resolve_osmo_lerobot_eval_policy_override() is None


@pytest.mark.e2e
@pytest.mark.usefixtures("ensure_gpu_nodes_available")
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_osmo_il_lifecycle_e2e(request: pytest.FixtureRequest, e2e_platform: E2EPlatform) -> None:
    log_e2e(f"Starting OSMO IL (LeRobot) lifecycle e2e test on the {e2e_platform.profile.name} profile")
    run_lerobot_lifecycle(request, e2e_platform, _SCENARIOS[e2e_platform.profile.name])
    log_e2e("OSMO LeRobot lifecycle e2e test finished successfully")
