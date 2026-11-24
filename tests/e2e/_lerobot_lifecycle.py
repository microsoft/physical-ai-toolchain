"""The OSMO LeRobot train, register, and evaluate lifecycle shared by the IL and VLA e2e tests.

A scenario sets what one platform profile trains and how far its evaluation goes. The azure scenarios
smoke-test the pipeline: a few training steps on a synthetic Blob dataset, then one evaluation that must
finish with tracked, schema-valid metrics. The local scenarios train on a dataset in the data root and also
prove that the policy learned and survives packaging: it must beat its untrained baseline on action error,
and the carrier image built from the registered checkpoint must reproduce the checkpoint's action error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from tests.e2e._common import e2e_name, log_e2e
from tests.e2e._lerobot_dataset import LeRobotDatasetSource, data_root_dataset_source, stage_synthetic_lerobot_dataset
from tests.e2e._mlflow import (
    LeRobotEvalResult,
    assert_osmo_lerobot_eval_has_mlflow_tracking,
    assert_osmo_lerobot_training_has_mlflow_tracking,
)
from tests.e2e._osmo import (
    OsmoLeRobotEvalPolicySource,
    assert_workflow_task_succeeded,
    monitor_osmo_workflow,
    registered_model_override,
    resolve_osmo_lerobot_eval_policy_override,
    submit_osmo_lerobot_eval,
    submit_osmo_lerobot_training,
)
from tests.e2e._platform import E2EPlatform, LocalPlatform, common_default

LEROBOT_TRAIN_TASK_NAME = "lerobot-train"
LEROBOT_EVAL_TASK_NAME = "lerobot-eval"


@dataclass(frozen=True)
class LeRobotScenario:
    """What one platform profile trains for a policy family, and which evaluations it gates."""

    policy_type: str
    training_steps: int
    batch_size: int
    save_freq: int
    log_freq: int
    learning_rate: str
    eval_episodes: int
    # A data-root dataset for the local profile; the azure profile stages a synthetic Blob dataset.
    data_root_dataset: str | None = None
    dataset_variable: str | None = None
    training_args: tuple[str, ...] = ()
    # Options for the untrained baseline after --builtin-policy; None skips the baseline comparison.
    baseline_args: tuple[str, ...] | None = None
    evaluate_carrier: bool = False

    def dataset_name(self) -> str | None:
        """Return the data-root dataset, which ``dataset_variable`` can name instead."""
        override = os.environ.get(self.dataset_variable, "").strip() if self.dataset_variable else ""
        return override or self.data_root_dataset


@dataclass(frozen=True)
class LeRobotLifecycleResult:
    trained: LeRobotEvalResult
    baseline: LeRobotEvalResult | None = None
    carrier: LeRobotEvalResult | None = None


def lerobot_dataset(
    request: pytest.FixtureRequest, platform: E2EPlatform, scenario: LeRobotScenario
) -> LeRobotDatasetSource:
    """Return the scenario's dataset: one in the local data root, or a synthetic dataset staged in Blob."""
    if isinstance(platform, LocalPlatform):
        name = scenario.dataset_name()
        if not name:
            pytest.skip(f"The local {scenario.policy_type} scenario names no data-root dataset")
        return data_root_dataset_source(platform.profile.datasets_host_dir, name)
    storage_account = request.getfixturevalue("storage_account")
    return stage_synthetic_lerobot_dataset(request, platform.repo_root, storage_account).source()


def _evaluate(
    request: pytest.FixtureRequest,
    platform: E2EPlatform,
    scenario: LeRobotScenario,
    dataset: LeRobotDatasetSource,
    policy_source: OsmoLeRobotEvalPolicySource,
    label: str,
) -> LeRobotEvalResult:
    workflow = submit_osmo_lerobot_eval(
        platform.repo_root,
        platform,
        policy_source=policy_source,
        dataset=dataset,
        policy_type=scenario.policy_type,
        eval_episodes=scenario.eval_episodes,
        eval_batch_size=1,
    )
    monitor_osmo_workflow(
        request, workflow, platform.repo_root, LEROBOT_EVAL_TASK_NAME, phase=f"LeRobot eval ({label})"
    )
    log_e2e(f"Validating OSMO LeRobot eval ({label}) MLflow tracking")
    result = assert_osmo_lerobot_eval_has_mlflow_tracking(workflow, platform.tracking)
    log_e2e(f"Validating OSMO LeRobot eval ({label}) workflow task success")
    assert_workflow_task_succeeded(workflow, platform.repo_root, LEROBOT_EVAL_TASK_NAME)
    return result


def run_lerobot_lifecycle(
    request: pytest.FixtureRequest, platform: E2EPlatform, scenario: LeRobotScenario
) -> LeRobotLifecycleResult:
    """Train and register a policy, then run the evaluations the scenario gates.

    ``E2E_LEROBOT_EVAL_POLICY_REPO_ID`` or ``E2E_LEROBOT_EVAL_MODEL`` skips training and evaluates that
    policy; a registered model also feeds the carrier evaluation.
    """
    repo_root = platform.repo_root
    policy_source = resolve_osmo_lerobot_eval_policy_override(platform.lerobot_policy_source)
    model = registered_model_override() if policy_source is not None else None
    if model is not None and platform.lerobot_policy_source(model) != policy_source:
        model = None
    dataset = lerobot_dataset(request, platform, scenario)
    if policy_source is None:
        register_model_name = e2e_name(f"{scenario.policy_type}-e2e-osmo-model")
        # Register cleanup before submit: the workflow registers the model server-side, so a later
        # assertion failure must not leak the registered version.
        request.addfinalizer(lambda: platform.remove_registered_model(register_model_name))
        workflow = submit_osmo_lerobot_training(
            repo_root,
            platform,
            dataset=dataset,
            policy_type=scenario.policy_type,
            training_steps=scenario.training_steps,
            save_freq=scenario.save_freq,
            batch_size=scenario.batch_size,
            learning_rate=scenario.learning_rate,
            log_freq=scenario.log_freq,
            register_model_name=register_model_name,
            extra_args=scenario.training_args,
        )
        monitor_osmo_workflow(
            request, workflow, repo_root, LEROBOT_TRAIN_TASK_NAME, phase=f"LeRobot {scenario.policy_type} training"
        )
        log_e2e("Validating OSMO LeRobot training MLflow tracking")
        assert_osmo_lerobot_training_has_mlflow_tracking(workflow, platform.tracking)
        log_e2e("Validating OSMO LeRobot training workflow task success")
        assert_workflow_task_succeeded(workflow, repo_root, LEROBOT_TRAIN_TASK_NAME)
        model = platform.resolve_registered_model(register_model_name)
        policy_source = platform.lerobot_policy_source(model)
    else:
        log_e2e(f"Using pre-configured eval policy {policy_source.description} (training skipped)")

    trained = _evaluate(request, platform, scenario, dataset, policy_source, "trained policy")
    result = LeRobotLifecycleResult(trained=trained)

    if scenario.baseline_args is not None:
        baseline_source = OsmoLeRobotEvalPolicySource(
            args=("--builtin-policy", *scenario.baseline_args),
            description=f"untrained {scenario.policy_type} baseline",
        )
        baseline = _evaluate(request, platform, scenario, dataset, baseline_source, "untrained baseline")
        if not trained.aggregate_mse < baseline.aggregate_mse:
            raise AssertionError(
                f"The trained {scenario.policy_type} policy's action MSE {trained.aggregate_mse} is not below "
                f"its untrained baseline's {baseline.aggregate_mse}"
            )
        log_e2e(f"Trained action MSE {trained.aggregate_mse} beats the untrained baseline's {baseline.aggregate_mse}")
        result = LeRobotLifecycleResult(trained=trained, baseline=baseline)

    if scenario.evaluate_carrier:
        if not isinstance(platform, LocalPlatform):
            raise AssertionError("Carrier evaluation runs under the local platform profile")
        if model is None:
            log_e2e("Skipping the carrier evaluation: the evaluated policy is not a registered model")
            return result
        image = platform.build_policy_carrier(model, base_image=common_default(repo_root, "DEFAULT_LEROBOT_EVAL_IMAGE"))
        request.addfinalizer(lambda: platform.remove_policy_carrier(image))
        carrier_source = OsmoLeRobotEvalPolicySource(args=("--policy-image", image), description=f"carrier {image}")
        carrier = _evaluate(request, platform, scenario, dataset, carrier_source, "policy carrier")
        if carrier.action_error() != trained.action_error():
            raise AssertionError(
                f"The policy carrier's action error {carrier.action_error()} differs from the checkpoint's "
                f"{trained.action_error()}"
            )
        log_e2e(f"Policy carrier {image} reproduces the checkpoint's action error exactly")
        result = LeRobotLifecycleResult(trained=trained, baseline=result.baseline, carrier=carrier)
    return result
