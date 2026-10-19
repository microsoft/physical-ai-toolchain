"""Infrastructure-free tests for named-environment resolution in the e2e harness.

These cover the bundle loader in ``tests/e2e/_environment.py`` and the conftest resolution
helpers that the Azure ML fixtures call, using synthetic bundles only. They prove that a
selected ``E2E_ENVIRONMENT`` never consults local Terraform state, fails loudly when a
required value is missing, that a stopped AKS cluster behind the compute target is detected,
that job cleanup can't hang on a cancel request or skip archiving test models, and that a
pipeline Azure ML creates without starting is retired and resubmitted once.
"""

# cspell:ignore amlcompute

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.e2e import _aml, conftest
from tests.e2e._environment import (
    BUNDLE_DIR_VAR,
    ENVIRONMENT_VAR,
    LOCAL_ENV_FILE,
    EnvironmentBundle,
    EnvironmentBundleError,
    LocalEnvError,
    activate_named_environment,
    apply_environment_defaults,
    bundle_search_paths,
    derive_compute_target,
    environment_defaults,
    load_environment_bundle,
    read_local_env,
)

_RESOURCE_VARIABLES = (
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_RESOURCE_GROUP",
    "AZUREML_WORKSPACE_NAME",
    "AZURE_STORAGE_ACCOUNT_NAME",
    "AKS_CLUSTER_NAME",
    "AZUREML_COMPUTE",
    "E2E_VLA_STORAGE_ACCOUNT",
)

_COMPLETE_BUNDLE = {
    "schema_version": 1,
    "environment": "sample",
    "subscription_id": "00000000-0000-0000-0000-000000000000",
    "resource_group": "rg-sample",
    "azureml_workspace": "mlw-sample",
    "storage_account": "sample-storage",
    "aks_cluster": "aks-sample-dev-001",
}


def _write_bundle(directory: Path, payload: object) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    deployment = directory / "deployment.json"
    deployment.write_text(json.dumps(payload), encoding="utf-8")
    return deployment


def _environ(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"HOME": str(tmp_path / "home"), **extra}


def test_search_paths_follow_precedence(tmp_path: Path) -> None:
    environ = _environ(tmp_path, **{BUNDLE_DIR_VAR: str(tmp_path / "explicit")})

    paths = bundle_search_paths("sample", tmp_path / "repo", environ)

    assert paths == [
        tmp_path / "explicit",
        tmp_path / "home" / ".config" / "physical-ai-toolchain" / "environments" / "sample",
        tmp_path / "repo" / "infrastructure" / "setup" / "generated" / "sample",
    ]


def test_explicit_bundle_directory_wins(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _write_bundle(tmp_path / "explicit", {**_COMPLETE_BUNDLE, "resource_group": "rg-explicit"})
    _write_bundle(
        tmp_path / "home" / ".config" / "physical-ai-toolchain" / "environments" / "sample",
        {**_COMPLETE_BUNDLE, "resource_group": "rg-home"},
    )
    _write_bundle(repo_root / "infrastructure" / "setup" / "generated" / "sample", _COMPLETE_BUNDLE)

    bundle = load_environment_bundle(
        "sample", repo_root, _environ(tmp_path, **{BUNDLE_DIR_VAR: str(tmp_path / "explicit")})
    )

    assert bundle.values["resource_group"] == "rg-explicit"


def test_home_bundle_wins_over_generated_bundle(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _write_bundle(
        tmp_path / "home" / ".config" / "physical-ai-toolchain" / "environments" / "sample",
        {**_COMPLETE_BUNDLE, "resource_group": "rg-home"},
    )
    _write_bundle(repo_root / "infrastructure" / "setup" / "generated" / "sample", _COMPLETE_BUNDLE)

    bundle = load_environment_bundle("sample", repo_root, _environ(tmp_path))

    assert bundle.values["resource_group"] == "rg-home"


def test_generated_bundle_is_the_last_fallback(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    generated = repo_root / "infrastructure" / "setup" / "generated" / "sample"
    _write_bundle(generated, _COMPLETE_BUNDLE)

    bundle = load_environment_bundle("sample", repo_root, _environ(tmp_path))

    assert bundle.directory == generated
    assert bundle.values["azureml_workspace"] == "mlw-sample"


def test_missing_bundle_names_every_searched_location(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentBundleError, match="was not found") as error:
        load_environment_bundle("sample", tmp_path / "repo", _environ(tmp_path))

    message = str(error.value)
    assert str(tmp_path / "home") in message
    assert str(tmp_path / "repo" / "infrastructure" / "setup" / "generated" / "sample") in message


@pytest.mark.parametrize("payload", ["not json", "[1, 2]"], ids=["malformed", "not-an-object"])
def test_unreadable_bundle_is_rejected(tmp_path: Path, payload: str) -> None:
    deployment = tmp_path / "explicit" / "deployment.json"
    deployment.parent.mkdir(parents=True)
    deployment.write_text(payload, encoding="utf-8")

    with pytest.raises(EnvironmentBundleError, match=r"deployment\.json"):
        load_environment_bundle("sample", tmp_path, _environ(tmp_path, **{BUNDLE_DIR_VAR: str(deployment.parent)}))


def test_symlinked_bundle_file_is_rejected(tmp_path: Path) -> None:
    target = _write_bundle(tmp_path / "real", _COMPLETE_BUNDLE)
    linked = tmp_path / "explicit"
    linked.mkdir()
    (linked / "deployment.json").symlink_to(target)

    with pytest.raises(EnvironmentBundleError, match="symlink"):
        load_environment_bundle("sample", tmp_path, _environ(tmp_path, **{BUNDLE_DIR_VAR: str(linked)}))


@pytest.mark.parametrize("name", ["../escape", "", "has space", "a/b"])
def test_invalid_environment_names_are_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(EnvironmentBundleError, match="Invalid"):
        load_environment_bundle(name, tmp_path, _environ(tmp_path))


def test_a_bundle_for_another_environment_is_never_used(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _write_bundle(tmp_path / "explicit", {**_COMPLETE_BUNDLE, "environment": "production-example"})
    _write_bundle(repo_root / "infrastructure" / "setup" / "generated" / "sample", _COMPLETE_BUNDLE)

    with pytest.raises(EnvironmentBundleError, match="is for environment 'production-example', not 'sample'"):
        load_environment_bundle("sample", repo_root, _environ(tmp_path, **{BUNDLE_DIR_VAR: str(tmp_path / "explicit")}))


@pytest.mark.parametrize(
    "schema",
    [pytest.param({"schema_version": 2}, id="newer"), pytest.param({"schema_version": None}, id="missing")],
)
def test_a_bundle_with_an_unknown_schema_is_rejected(tmp_path: Path, schema: dict[str, object]) -> None:
    _write_bundle(tmp_path / "explicit", {**_COMPLETE_BUNDLE, **schema})

    with pytest.raises(EnvironmentBundleError, match="schema_version"):
        load_environment_bundle("sample", tmp_path, _environ(tmp_path, **{BUNDLE_DIR_VAR: str(tmp_path / "explicit")}))


def test_blank_and_non_string_fields_are_ignored(tmp_path: Path) -> None:
    _write_bundle(
        tmp_path / "explicit",
        {**_COMPLETE_BUNDLE, "azureml_workspace": "  ", "storage_account": 42},
    )

    bundle = load_environment_bundle(
        "sample", tmp_path, _environ(tmp_path, **{BUNDLE_DIR_VAR: str(tmp_path / "explicit")})
    )

    assert "azureml_workspace" not in bundle.values
    assert "storage_account" not in bundle.values
    assert "AZUREML_WORKSPACE_NAME" not in environment_defaults(bundle)


@pytest.mark.parametrize(
    ("aks_cluster", "expected"),
    [
        ("aks-sample-dev-001", "k8s-sample-dev-0"),
        ("aks-ab-dev-001", "k8s-ab-dev-001"),
        ("aks-abcdefghijk-001", "k8s-abcdefghijk"),
        ("custom-cluster", "custom-cluster"),
    ],
)
def test_compute_target_matches_the_conftest_derivation(aks_cluster: str, expected: str) -> None:
    assert derive_compute_target(aks_cluster) == expected


def test_defaults_never_override_exported_values() -> None:
    bundle = EnvironmentBundle(
        name="sample",
        directory=Path("unused"),
        values={"resource_group": "rg-bundle", "azureml_workspace": "mlw-bundle", "aks_cluster": "aks-x"},
    )
    environ = {"AZURE_RESOURCE_GROUP": "rg-exported", "AZUREML_WORKSPACE_NAME": "  "}

    applied = apply_environment_defaults(bundle, environ)

    assert environ["AZURE_RESOURCE_GROUP"] == "rg-exported"
    assert environ["AZUREML_WORKSPACE_NAME"] == "mlw-bundle"
    assert environ["AZUREML_COMPUTE"] == "k8s-x"
    assert "AZURE_RESOURCE_GROUP" not in applied


def test_activation_is_a_no_op_without_a_selection(tmp_path: Path) -> None:
    environ = _environ(tmp_path)

    assert activate_named_environment(tmp_path, environ) is None
    assert set(environ) == {"HOME"}


@pytest.fixture
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every lookup at temporary locations and restore all resource variables afterward."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv(ENVIRONMENT_VAR, raising=False)
    monkeypatch.delenv(BUNDLE_DIR_VAR, raising=False)
    # Blank values are treated as unset, and setenv records them for restoration at teardown.
    for variable in _RESOURCE_VARIABLES:
        monkeypatch.setenv(variable, "")

    def _no_terraform(_repo_root: Path) -> conftest.TerraformOutputs:
        raise AssertionError("local Terraform state must not be consulted for a named environment")

    monkeypatch.setattr(conftest, "_terraform_outputs", _no_terraform)
    conftest._activate_named_environment.cache_clear()
    yield tmp_path
    conftest._activate_named_environment.cache_clear()


def _select_bundle(monkeypatch: pytest.MonkeyPatch, directory: Path, payload: object) -> None:
    _write_bundle(directory, payload)
    monkeypatch.setenv(ENVIRONMENT_VAR, "sample")
    monkeypatch.setenv(BUNDLE_DIR_VAR, str(directory))


def test_named_environment_resolves_without_terraform(
    isolated_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _select_bundle(monkeypatch, isolated_environment / "bundle", _COMPLETE_BUNDLE)

    identity = conftest._resolve_workspace_identity(isolated_environment)

    assert identity == ("00000000-0000-0000-0000-000000000000", "rg-sample", "mlw-sample")
    assert conftest._resolve_compute_name(isolated_environment) == "k8s-sample-dev-0"
    assert conftest._resolve_storage_account(isolated_environment) == "sample-storage"


def test_named_environment_missing_field_fails(isolated_environment: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {key: value for key, value in _COMPLETE_BUNDLE.items() if key != "azureml_workspace"}
    _select_bundle(monkeypatch, isolated_environment / "bundle", payload)

    with pytest.raises(pytest.fail.Exception, match="AZUREML_WORKSPACE_NAME"):
        conftest._resolve_workspace_identity(isolated_environment)


def test_named_environment_missing_storage_fails(isolated_environment: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {key: value for key, value in _COMPLETE_BUNDLE.items() if key != "storage_account"}
    _select_bundle(monkeypatch, isolated_environment / "bundle", payload)

    with pytest.raises(pytest.fail.Exception, match="AZURE_STORAGE_ACCOUNT_NAME"):
        conftest._resolve_storage_account(isolated_environment)


def test_named_environment_without_a_bundle_fails(isolated_environment: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENVIRONMENT_VAR, "sample")

    with pytest.raises(pytest.fail.Exception, match="was not found"):
        conftest._resolve_compute_name(isolated_environment)


def test_unnamed_environment_keeps_the_terraform_fallback(
    isolated_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = conftest.TerraformOutputs(
        {
            "resource_group": {"value": {"name": "rg-terraform"}},
            "azureml_workspace": {"value": {"name": "mlw-terraform"}},
            "aks_cluster": {"value": {"name": "aks-tf-dev-001"}},
            "storage_account": {"value": {"name": "terraform-storage"}},
        }
    )
    monkeypatch.setattr(conftest, "_terraform_outputs", lambda _repo_root: outputs)
    monkeypatch.setattr(conftest, "_subscription_id_from_az_cli", lambda: "sub-from-cli")

    assert conftest._resolve_workspace_identity(isolated_environment) == (
        "sub-from-cli",
        "rg-terraform",
        "mlw-terraform",
    )
    assert conftest._resolve_compute_name(isolated_environment) == "k8s-tf-dev-001"
    assert conftest._resolve_storage_account(isolated_environment) == "terraform-storage"


_AKS_ID = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-sample"
    "/providers/Microsoft.ContainerService/managedClusters/aks-sample-dev-001"
)


def _record_az(monkeypatch: pytest.MonkeyPatch, stdout: str, returncode: int = 0) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(args: list[str], *, cwd: Path, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(conftest, "run_command", fake_run)
    return calls


def test_stopped_aks_cluster_behind_a_kubernetes_compute_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_az(monkeypatch, "Stopped\n")

    state = conftest._attached_aks_power_state({"type": "kubernetes", "resource_id": _AKS_ID}, Path("."))

    assert state == "Stopped"
    assert calls == [
        [
            "az",
            "aks",
            "show",
            "--subscription",
            "00000000-0000-0000-0000-000000000000",
            "--resource-group",
            "rg-sample",
            "--name",
            "aks-sample-dev-001",
            "--query",
            "powerState.code",
            "-o",
            "tsv",
        ]
    ]


@pytest.mark.parametrize(
    "compute",
    [
        {"type": "amlcompute", "resource_id": _AKS_ID},
        {
            "type": "kubernetes",
            "resource_id": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Kubernetes/connectedClusters/k3s",
        },
        {"type": "kubernetes"},
    ],
    ids=["managed-compute", "arc-connected-cluster", "no-resource-id"],
)
def test_computes_without_an_aks_cluster_skip_the_power_check(
    monkeypatch: pytest.MonkeyPatch, compute: dict[str, object]
) -> None:
    calls = _record_az(monkeypatch, "Running\n")

    assert conftest._attached_aks_power_state(compute, Path(".")) is None
    assert calls == []


def test_unknown_aks_power_state_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    _record_az(monkeypatch, "", returncode=1)

    assert conftest._attached_aks_power_state({"type": "kubernetes", "resource_id": _AKS_ID}, Path(".")) is None


def _sample_job() -> _aml.AzureMLJob:
    return _aml.AzureMLJob("sample-job", _aml.AzureMLWorkspace("sub", "rg-sample", "mlw-sample"), "sample")


def _wait_until_gone(pid: int, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_a_hung_cancel_request_is_stopped_with_its_child_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_az = bin_dir / "az"
    fake_az.write_text('#!/usr/bin/env bash\nsleep 30 &\necho $! > "$CHILD_PID_FILE"\nwait\n', encoding="utf-8")
    fake_az.chmod(0o755)
    child_pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CHILD_PID_FILE", str(child_pid_file))
    monkeypatch.setattr(_aml, "AML_CANCEL_TIMEOUT_SECONDS", 1)

    started = time.monotonic()
    _aml.cancel_aml_job(_sample_job(), tmp_path)

    assert time.monotonic() - started < 10
    assert _wait_until_gone(int(child_pid_file.read_text(encoding="utf-8")))


def test_cleanup_archives_models_when_the_job_never_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archived: list[str] = []

    def never_terminal(*args: object, **kwargs: object) -> str:
        raise AssertionError("Timed out waiting for cleanup; last status was 'NotStarted'")

    monkeypatch.setattr(_aml, "cancel_aml_job", lambda job, repo_root: None)
    monkeypatch.setattr(_aml, "wait_for_status", never_terminal)
    monkeypatch.setattr(
        _aml, "archive_all_model_versions", lambda repo_root, workspace, model_name: archived.append(model_name)
    )
    job = _sample_job()

    with pytest.raises(AssertionError, match="NotStarted"):
        _aml.cleanup_aml_job_and_model_versions(job, tmp_path, job.workspace, "sample-model")

    assert archived == ["sample-model"]


class _FakePipelineService:
    """Stand-in for the Azure ML calls that ``start_aml_pipeline`` makes, keyed by submission order.

    Orphan outcomes differ only after the cancel request: ``orphaned`` ignores it, as real orphans do,
    ``orphan-canceled`` reaches ``Canceled``, and ``orphan-starts`` starts running with a child job.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, outcomes: list[str]) -> None:
        self.outcomes = outcomes
        self.submitted: list[_aml.AzureMLJob] = []
        self.registered: list[str] = []
        self.cancelled: list[tuple[str, float | None]] = []
        self.archived: list[str] = []
        self.waits: list[tuple[str, int]] = []
        self.observed: list[str] = []
        monkeypatch.setattr(_aml, "wait_until_aml_started", self.wait_until_started)
        monkeypatch.setattr(_aml, "fetch_aml_job_payload", self.payload)
        monkeypatch.setattr(_aml, "list_aml_child_jobs", self.children)
        monkeypatch.setattr(_aml, "cancel_aml_job", self.cancel)
        monkeypatch.setattr(_aml, "archive_aml_job", lambda job, repo_root: self.archived.append(job.name))
        monkeypatch.setattr(_aml, "log_e2e", lambda message: None)

    def outcome(self, job: _aml.AzureMLJob) -> str:
        return self.outcomes[int(job.name.rsplit("-", 1)[1])]

    def submit(self) -> _aml.AzureMLJob:
        job = _aml.AzureMLJob(f"pipeline-{len(self.submitted)}", _sample_job().workspace, "sample")
        self.submitted.append(job)
        return job

    def wait_until_started(
        self, job: _aml.AzureMLJob, repo_root: Path, *, timeout_minutes: int, poll_interval_seconds: int
    ) -> None:
        self.waits.append((job.name, timeout_minutes))
        outcome = self.outcome(job)
        if outcome == "started" or (outcome == "slow" and len(self.waits) > 1):
            return
        if outcome == "failed":
            raise AssertionError(f"AzureML job {job.name} to start failed with status 'Failed'")
        raise AssertionError(f"Timed out waiting for AzureML job {job.name} to start; last status was 'NotStarted'")

    def _cancel_requested(self, job: _aml.AzureMLJob) -> bool:
        return any(name == job.name for name, _ in self.cancelled)

    def payload(self, job: _aml.AzureMLJob, repo_root: Path) -> dict[str, str]:
        self.observed.append(job.name)
        outcome = self.outcome(job)
        if outcome == "failed":
            return {"status": "Failed"}
        if self._cancel_requested(job) and outcome == "orphan-canceled":
            return {"status": "Canceled"}
        if self._cancel_requested(job) and outcome == "orphan-starts":
            return {"status": "Running"}
        return {"status": "NotStarted"}

    def children(self, job: _aml.AzureMLJob, repo_root: Path) -> list[str]:
        outcome = self.outcome(job)
        started_late = outcome == "orphan-starts" and self._cancel_requested(job)
        return ["step"] if outcome == "slow" or started_late else []

    def cancel(self, job: _aml.AzureMLJob, repo_root: Path, *, timeout_seconds: float | None = None) -> None:
        self.cancelled.append((job.name, timeout_seconds))

    def start(self, repo_root: Path) -> _aml.AzureMLJob:
        return _aml.start_aml_pipeline(
            self.submit,
            repo_root,
            on_submitted=lambda job: self.registered.append(job.name),
            timeout_minutes=15,
            poll_interval_seconds=30,
        )


def test_a_pipeline_that_starts_is_returned_without_a_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakePipelineService(monkeypatch, ["started"])

    job = service.start(tmp_path)

    assert job.name == "pipeline-0"
    assert service.registered == ["pipeline-0"]
    assert service.waits == [("pipeline-0", _aml.AML_ORPHAN_WINDOW_MINUTES)]
    assert service.cancelled == []
    assert service.archived == []
    assert job.handle.retry_classifications.get("azureml_job", "none") == "none"


def test_an_orphaned_pipeline_is_retired_and_resubmitted_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakePipelineService(monkeypatch, ["orphaned", "started"])

    job = service.start(tmp_path)

    orphan = service.submitted[0]
    assert job.name == "pipeline-1"
    assert service.registered == ["pipeline-0", "pipeline-1"]
    assert service.cancelled == [("pipeline-0", _aml.AML_ORPHAN_CANCEL_TIMEOUT_SECONDS)]
    assert service.archived == ["pipeline-0"]
    # The cancel had no visible effect, so the orphan is suspected, not terminal, and stays cleanup-eligible.
    assert orphan.suspected_orphan
    assert not orphan.is_terminal
    assert orphan.terminal_status is None
    assert job.handle.attempts["azureml_job"] == ["initial", "orphaned-resubmit-1"]
    assert job.handle.retry_classifications["azureml_job"] == _aml.ORPHANED_SUBMISSION


def test_an_orphan_that_stops_after_its_cancel_records_the_observed_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakePipelineService(monkeypatch, ["orphan-canceled", "started"])

    job = service.start(tmp_path)

    orphan = service.submitted[0]
    assert job.name == "pipeline-1"
    assert orphan.is_terminal
    assert orphan.terminal_status == "Canceled"


def test_an_orphan_that_starts_after_its_cancel_is_not_resubmitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakePipelineService(monkeypatch, ["orphan-starts", "started"])

    with pytest.raises(AssertionError, match="started after it looked orphaned"):
        service.start(tmp_path)

    orphan = service.submitted[0]
    assert service.registered == ["pipeline-0"]
    assert orphan.suspected_orphan
    assert not orphan.is_terminal


def test_a_second_orphan_fails_with_the_activity_log_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakePipelineService(monkeypatch, ["orphaned", "orphaned", "started"])

    with pytest.raises(AssertionError, match=r"pipeline-0, pipeline-1.*GatewayTimeout.*jobs/write") as error:
        service.start(tmp_path)

    assert "Activity Log" in str(error.value)
    assert "no pipeline step ran" in str(error.value)
    assert service.registered == ["pipeline-0", "pipeline-1"]
    assert service.archived == ["pipeline-0", "pipeline-1"]


def test_a_pipeline_with_child_jobs_keeps_waiting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakePipelineService(monkeypatch, ["slow"])

    job = service.start(tmp_path)

    assert job.name == "pipeline-0"
    assert service.waits == [("pipeline-0", 5), ("pipeline-0", 10)]
    assert service.cancelled == []
    assert service.archived == []


def test_a_failed_pipeline_is_not_resubmitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakePipelineService(monkeypatch, ["failed", "started"])

    with pytest.raises(AssertionError, match="failed with status 'Failed'"):
        service.start(tmp_path)

    assert service.registered == ["pipeline-0"]
    assert service.archived == []


def test_cancel_uses_the_requested_time_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    limits: list[float] = []

    def fake_run(args: list[str], *, cwd: Path, timeout_seconds: float) -> None:
        limits.append(timeout_seconds)
        return None

    monkeypatch.setattr(_aml, "_run_with_time_limit", fake_run)
    monkeypatch.setattr(_aml, "log_e2e", lambda message: None)

    suspected = _sample_job()
    suspected.suspected_orphan = True

    _aml.cancel_aml_job(_sample_job(), tmp_path)
    _aml.cancel_aml_job(_sample_job(), tmp_path, timeout_seconds=_aml.AML_ORPHAN_CANCEL_TIMEOUT_SECONDS)
    _aml.cancel_aml_job(suspected, tmp_path)

    assert limits == [
        _aml.AML_CANCEL_TIMEOUT_SECONDS,
        _aml.AML_ORPHAN_CANCEL_TIMEOUT_SECONDS,
        _aml.AML_ORPHAN_CANCEL_TIMEOUT_SECONDS,
    ]


def test_child_jobs_and_archive_use_the_job_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    messages: list[str] = []

    def fake_run(args: list[str], *, cwd: Path, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[3] == "list":
            return subprocess.CompletedProcess(args, 0, stdout='["step-a", "step-b"]', stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="archive refused")

    monkeypatch.setattr(_aml, "run_command", fake_run)
    monkeypatch.setattr(_aml, "log_e2e", messages.append)
    job = _sample_job()

    assert _aml.list_aml_child_jobs(job, tmp_path) == ["step-a", "step-b"]
    _aml.archive_aml_job(job, tmp_path)

    assert commands[0][commands[0].index("--parent-job-name") + 1] == "sample-job"
    assert commands[1][:4] == ["az", "ml", "job", "archive"]
    assert all("rg-sample" in command and "mlw-sample" in command for command in commands)
    assert "Could not archive AzureML job sample-job" in messages[0]


def _retired_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> tuple[_FakePipelineService, _aml.AzureMLJob, list[str], list[str]]:
    service = _FakePipelineService(monkeypatch, [outcome, "started"])
    if outcome == "orphaned":
        service.start(tmp_path)
    else:
        with pytest.raises(AssertionError, match="started after it looked orphaned"):
            service.start(tmp_path)
    archived: list[str] = []
    waits: list[str] = []

    def record_wait(*args: object, **kwargs: object) -> str:
        waits.append(str(kwargs["goal_description"]))
        return "Canceled"

    monkeypatch.setattr(_aml, "wait_for_status", record_wait)
    monkeypatch.setattr(
        _aml, "archive_all_model_versions", lambda repo_root, workspace, model_name: archived.append(model_name)
    )
    return service, service.submitted[0], archived, waits


def test_cleanup_cancels_and_rechecks_an_inert_orphan_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, orphan, archived, waits = _retired_orphan(tmp_path, monkeypatch, "orphaned")
    observed_before = service.observed.count("pipeline-0")

    _aml.cleanup_aml_job_and_model_versions(orphan, tmp_path, orphan.workspace, "sample-model")

    # A failed or ignored cancel during recovery is retried at cleanup, and the job is checked again.
    assert [name for name, _ in service.cancelled] == ["pipeline-0", "pipeline-0"]
    assert service.observed.count("pipeline-0") > observed_before
    assert waits == []
    assert not orphan.is_terminal
    assert archived == ["sample-model"]


def test_cleanup_waits_for_an_orphan_that_started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, orphan, archived, waits = _retired_orphan(tmp_path, monkeypatch, "orphan-starts")

    _aml.cleanup_aml_job_and_model_versions(orphan, tmp_path, orphan.workspace, "sample-model")

    assert [name for name, _ in service.cancelled] == ["pipeline-0", "pipeline-0"]
    assert waits == ["AzureML job pipeline-0 cleanup"]
    assert orphan.terminal_status == "Canceled"
    assert archived == ["sample-model"]


_FAKE_SECRET = "fake-secret-0123456789"


def _write_local_env(root: Path, text: str) -> None:
    (root / LOCAL_ENV_FILE).write_text(text, encoding="utf-8")


def test_local_env_follows_shell_assignment_rules(tmp_path: Path) -> None:
    _write_local_env(
        tmp_path,
        "\n".join(
            [
                "# HF_TOKEN=commented-out",
                f"export EXPORTED={_FAKE_SECRET}",
                'DOUBLE="double value"',
                "SINGLE='single'",
                "EMPTY=",
                "EMPTY_WITH_COMMENT= # comment",
                'QUOTED_EMPTY=""',
                'PADDED=" padded value "',
                "TRAILING=kept # comment",
                "REPEATED=first",
                "REPEATED=second",
                "CLEARED=value",
                "CLEARED=",
                "NOT ASSIGNMENT=ignored",
                "NOT_REQUESTED=$EXPANDED ~/path",
            ]
        ),
    )
    names = [
        "EXPORTED",
        "DOUBLE",
        "SINGLE",
        "EMPTY",
        "EMPTY_WITH_COMMENT",
        "QUOTED_EMPTY",
        "PADDED",
        "TRAILING",
        "REPEATED",
        "CLEARED",
        "MISSING",
    ]

    assert read_local_env(tmp_path, names) == {
        "EXPORTED": _FAKE_SECRET,
        "DOUBLE": "double value",
        "SINGLE": "single",
        "EMPTY": "",
        "EMPTY_WITH_COMMENT": "",
        "QUOTED_EMPTY": "",
        "PADDED": " padded value ",
        "TRAILING": "kept",
        "REPEATED": "second",
        "CLEARED": "",
    }


@pytest.mark.parametrize(
    "line",
    [
        'HF_TOKEN="$OTHER"',
        "HF_TOKEN=$OTHER",
        f'HF_TOKEN="{_FAKE_SECRET}"suffix',
        f"HF_TOKEN= {_FAKE_SECRET}",
        f'HF_TOKEN="{_FAKE_SECRET}',
        f"HF_TOKEN={_FAKE_SECRET}\\x",
        "HF_TOKEN=~/token",
        f"HF_TOKEN = {_FAKE_SECRET}",
        f"HF_TOKEN={_FAKE_SECRET}; echo done",
        "HF_TOKEN=`cat token`",
    ],
    ids=[
        "quoted-expansion",
        "expansion",
        "concatenation",
        "value-after-space",
        "unterminated-quote",
        "backslash",
        "tilde",
        "spaces-around-equals",
        "command-separator",
        "command-substitution",
    ],
)
def test_assignments_that_are_not_plain_literals_fail_without_their_values(tmp_path: Path, line: str) -> None:
    _write_local_env(tmp_path, f"{line}\n")

    with pytest.raises(LocalEnvError, match=r"HF_TOKEN .*line 1") as raised:
        read_local_env(tmp_path, ["HF_TOKEN"])

    assert _FAKE_SECRET not in str(raised.value)


def test_local_env_returns_only_requested_names(tmp_path: Path) -> None:
    _write_local_env(tmp_path, f"HF_TOKEN={_FAKE_SECRET}\nOTHER_SECRET=other\n")

    assert read_local_env(tmp_path, ["HF_TOKEN"]) == {"HF_TOKEN": _FAKE_SECRET}
    assert read_local_env(tmp_path, []) == {}


def test_missing_local_env_file_is_empty(tmp_path: Path) -> None:
    assert read_local_env(tmp_path, ["HF_TOKEN"]) == {}


def test_unreadable_local_env_file_is_reported_without_its_contents(tmp_path: Path) -> None:
    (tmp_path / LOCAL_ENV_FILE).mkdir()

    with pytest.raises(LocalEnvError, match=r"\.env\.local"):
        read_local_env(tmp_path, ["HF_TOKEN"])


def test_invalid_utf8_local_env_file_is_reported_without_its_contents(tmp_path: Path) -> None:
    (tmp_path / LOCAL_ENV_FILE).write_bytes(b"HF_TOKEN=\xff\xfe" + _FAKE_SECRET.encode())

    with pytest.raises(LocalEnvError) as raised:
        read_local_env(tmp_path, ["HF_TOKEN"])

    assert "UTF-8" in str(raised.value)
    assert _FAKE_SECRET not in str(raised.value)


class _GateItem:
    nodeid = "tests/e2e/test_e2e_aml_vla_pi0_training.py::test_vla"

    def __init__(self, *, marked: bool = True) -> None:
        self._marked = marked
        self.stash = pytest.Stash()

    def get_closest_marker(self, name: str) -> object | None:
        return object() if self._marked and name == "requires_hf_token" else None


@pytest.fixture
def gate_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(conftest, "_REPO_ROOT", tmp_path)
    monkeypatch.setenv("HF_TOKEN", "")
    return tmp_path


def test_gate_loads_the_token_from_local_env(gate_root: Path) -> None:
    _write_local_env(gate_root, f"HF_TOKEN={_FAKE_SECRET}\n")

    conftest.pytest_runtest_setup(_GateItem())  # type: ignore[arg-type]

    assert os.environ["HF_TOKEN"] == _FAKE_SECRET


def test_gate_uses_an_exported_token_when_local_env_does_not_set_it(
    gate_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_local_env(gate_root, f"OTHER_SECRET={_FAKE_SECRET}\n")
    monkeypatch.setenv("HF_TOKEN", "exported-token")

    conftest.pytest_runtest_setup(_GateItem())  # type: ignore[arg-type]

    assert os.environ["HF_TOKEN"] == "exported-token"


def test_gate_lets_local_env_override_an_exported_token_like_the_scripts(
    gate_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_local_env(gate_root, f"HF_TOKEN={_FAKE_SECRET}\n")
    monkeypatch.setenv("HF_TOKEN", "exported-token")

    conftest.pytest_runtest_setup(_GateItem())  # type: ignore[arg-type]

    assert os.environ["HF_TOKEN"] == _FAKE_SECRET


def test_gate_rejects_an_empty_local_env_token_that_overrides_the_environment(
    gate_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_local_env(gate_root, "HF_TOKEN=\n")
    monkeypatch.setenv("HF_TOKEN", _FAKE_SECRET)

    with pytest.raises(pytest.fail.Exception) as failed:
        conftest.pytest_runtest_setup(_GateItem())  # type: ignore[arg-type]

    assert LOCAL_ENV_FILE in str(failed.value)
    assert "empty" in str(failed.value)
    assert _FAKE_SECRET not in str(failed.value)


def test_gate_names_both_places_when_the_token_is_missing(gate_root: Path) -> None:
    _write_local_env(gate_root, f"OTHER_SECRET={_FAKE_SECRET}\n")

    with pytest.raises(pytest.fail.Exception) as failed:
        conftest.pytest_runtest_setup(_GateItem())  # type: ignore[arg-type]

    assert LOCAL_ENV_FILE in str(failed.value)
    assert "export" in str(failed.value)
    assert _FAKE_SECRET not in str(failed.value)


def test_gate_reports_an_unreadable_local_env_file_even_with_an_exported_token(
    gate_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (gate_root / LOCAL_ENV_FILE).mkdir()
    monkeypatch.setenv("HF_TOKEN", _FAKE_SECRET)

    with pytest.raises(pytest.fail.Exception, match=r"\.env\.local") as failed:
        conftest.pytest_runtest_setup(_GateItem())  # type: ignore[arg-type]

    assert _FAKE_SECRET not in str(failed.value)


def test_gate_restores_an_absent_token_after_the_test(gate_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_local_env(gate_root, f"HF_TOKEN={_FAKE_SECRET}\n")
    monkeypatch.delenv("HF_TOKEN")
    item = _GateItem()

    conftest.pytest_runtest_setup(item)  # type: ignore[arg-type]
    assert os.environ["HF_TOKEN"] == _FAKE_SECRET
    conftest.pytest_runtest_teardown(item)  # type: ignore[arg-type]

    assert "HF_TOKEN" not in os.environ


def test_gate_restores_an_exported_token_after_the_test(gate_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_local_env(gate_root, f"HF_TOKEN={_FAKE_SECRET}\n")
    monkeypatch.setenv("HF_TOKEN", "exported-token")
    item = _GateItem()

    conftest.pytest_runtest_setup(item)  # type: ignore[arg-type]
    assert os.environ["HF_TOKEN"] == _FAKE_SECRET
    conftest.pytest_runtest_teardown(item)  # type: ignore[arg-type]

    assert os.environ["HF_TOKEN"] == "exported-token"


def test_gate_teardown_leaves_untouched_tests_alone(gate_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "exported-token")

    conftest.pytest_runtest_teardown(_GateItem(marked=False))  # type: ignore[arg-type]

    assert os.environ["HF_TOKEN"] == "exported-token"


def test_gate_ignores_tests_without_the_marker(gate_root: Path) -> None:
    (gate_root / LOCAL_ENV_FILE).mkdir()

    conftest.pytest_runtest_setup(_GateItem(marked=False))  # type: ignore[arg-type]


def test_local_env_file_stays_out_of_git_and_repo_root_snapshots(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    no_global_excludes = ["-c", f"core.excludesFile={os.devnull}"]
    tracked = subprocess.run(["git", *no_global_excludes, "check-ignore", "-q", LOCAL_ENV_FILE], cwd=repo, check=False)
    assert tracked.returncode == 0, f"{LOCAL_ENV_FILE} must stay gitignored"

    # Azure ML snapshots of the repository root use only the root .amlignore, or .gitignore when it is absent.
    amlignore = repo / ".amlignore"
    snapshot_rules = amlignore if amlignore.is_file() else repo / ".gitignore"
    probe = tmp_path / "snapshot-root"
    probe.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=probe, check=True)
    shutil.copyfile(snapshot_rules, probe / ".gitignore")
    excluded = subprocess.run(
        ["git", *no_global_excludes, "check-ignore", "-q", LOCAL_ENV_FILE], cwd=probe, check=False
    )
    assert excluded.returncode == 0, f"{snapshot_rules.name} must keep {LOCAL_ENV_FILE} out of repo-root code snapshots"
