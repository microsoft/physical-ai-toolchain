"""Infrastructure-free tests for named-environment resolution in the e2e harness.

These cover the bundle loader in ``tests/e2e/_environment.py`` and the conftest resolution
helpers that the Azure ML fixtures call, using synthetic bundles only. They prove that a
selected ``E2E_ENVIRONMENT`` never consults local Terraform state, fails loudly when a
required value is missing, that a stopped AKS cluster behind the compute target is detected,
and that job cleanup can't hang on a cancel request or skip archiving test models.
"""

# cspell:ignore amlcompute

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.e2e import _aml, conftest
from tests.e2e._environment import (
    BUNDLE_DIR_VAR,
    ENVIRONMENT_VAR,
    EnvironmentBundle,
    EnvironmentBundleError,
    activate_named_environment,
    apply_environment_defaults,
    bundle_search_paths,
    derive_compute_target,
    environment_defaults,
    load_environment_bundle,
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
