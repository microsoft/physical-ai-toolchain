# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Tests for cosmos-data-factory/scripts/sdg.sh using stubbed external tools."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sdg.sh"
TOOLS = ("docker", "kubectl", "helm", "make", "curl")


@pytest.fixture
def sdg_env(tmp_path: Path) -> dict:
    """Fake sdg-workflow checkout, stub tools that log their calls, and an env file."""
    repo = tmp_path / "sdg-workflow"
    (repo / "scripts").mkdir(parents=True)
    (repo / "Makefile").write_text("")
    (repo / "scripts" / "setup_airflow_secrets.sh").write_text("")
    (repo / "payloads").mkdir()
    (repo / "payloads" / "p.json").write_text("{}")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for tool in TOOLS:
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n')
        stub.chmod(0o755)
    # Keep the real python3 and core utils reachable.
    for tool in ("python3", "bash", "env", "grep", "mktemp", "rm", "cat", "dirname"):
        real = shutil.which(tool)
        if real:
            (bin_dir / tool).symlink_to(real)

    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("")
    env_file = tmp_path / "sdg.env"
    env_file.write_text(f"SDG_WORKFLOW_DIR={repo}\nKUBECONFIG={kubeconfig}\nNGC_API_KEY=ngc\nHF_TOKEN=hf\n")

    env = {
        "PATH": str(bin_dir),
        "HOME": str(tmp_path),
        "SDG_ENV_FILE": str(env_file),
    }
    return {"env": env, "log": log, "repo": repo, "env_file": env_file}


def run(ctx: dict, *args: str, **overrides: str) -> subprocess.CompletedProcess:
    env = {**ctx["env"], **overrides}
    return subprocess.run(
        [str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False
    )


def calls(ctx: dict) -> list[str]:
    return ctx["log"].read_text().splitlines() if ctx["log"].exists() else []


def test_help_lists_commands(sdg_env):
    result = run(sdg_env, "--help")
    assert result.returncode == 0
    for cmd in ("preflight", "setup", "run", "trigger", "uninstall"):
        assert cmd in result.stdout


def test_no_args_prints_usage_and_fails(sdg_env):
    result = run(sdg_env)
    assert result.returncode == 1
    assert "Usage:" in result.stdout


def test_unknown_command_fails(sdg_env):
    result = run(sdg_env, "bogus")
    assert result.returncode != 0
    assert "Unknown command" in result.stderr


def test_missing_env_file_fails(sdg_env):
    sdg_env["env_file"].unlink()
    result = run(sdg_env, "--dry-run", "preflight")
    assert result.returncode != 0
    assert "Missing" in result.stderr


def test_dry_run_prints_all_run_steps_without_executing(sdg_env):
    result = run(sdg_env, "--dry-run", "run")
    assert result.returncode == 0, result.stderr
    for target in ("setup", "install-nfs", "build-sdg-controller", "install-sdg-controller"):
        assert f"[DRY-RUN] make -C {sdg_env['repo']} {target}" in result.stdout
    assert not [c for c in calls(sdg_env) if c.startswith("make")]


@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("nfs", "install-nfs"),
        ("build", "build-sdg-controller"),
        ("install", "install-sdg-controller"),
        ("port-forward", "port-forward"),
        ("uninstall", "uninstall"),
    ],
)
def test_commands_invoke_make_target(sdg_env, command, target):
    result = run(sdg_env, command)
    assert result.returncode == 0, result.stderr
    assert calls(sdg_env) == [f"make -C {sdg_env['repo']} {target}"]


@pytest.mark.parametrize("secret", ["NGC_API_KEY", "HF_TOKEN"])
def test_preflight_requires_secrets(sdg_env, secret):
    text = sdg_env["env_file"].read_text().replace(f"{secret}=", f"{secret}_UNUSED=")
    sdg_env["env_file"].write_text(text)
    result = run(sdg_env, "--dry-run", "preflight")
    assert result.returncode != 0
    assert f"{secret} is required" in result.stderr


def test_preflight_requires_existing_kubeconfig(sdg_env):
    result = run(sdg_env, "--dry-run", "preflight", KUBECONFIG="/nonexistent")
    assert result.returncode != 0
    assert "KUBECONFIG" in result.stderr


@pytest.mark.parametrize("tool", ["docker", "kubectl", "helm", "make"])
def test_preflight_reports_missing_tool(sdg_env, tool):
    (Path(sdg_env["env"]["PATH"]) / tool).unlink()
    result = run(sdg_env, "--dry-run", "preflight")
    assert result.returncode != 0
    assert f"missing required tool: {tool}" in result.stderr


def test_rejects_non_sdg_directory(sdg_env):
    (sdg_env["repo"] / "Makefile").unlink()
    result = run(sdg_env, "--dry-run", "preflight")
    assert result.returncode != 0
    assert "Not an sdg-workflow checkout" in result.stderr


def test_empty_shell_value_does_not_mask_env_file(sdg_env):
    result = run(sdg_env, "--dry-run", "preflight", NGC_API_KEY="")
    assert result.returncode == 0  # empty shell value does not mask the file value


def test_trigger_requires_payload(sdg_env):
    result = run(sdg_env, "--dry-run", "trigger")
    assert result.returncode != 0
    assert "requires a payload" in result.stderr


def test_trigger_dry_run_resolves_payload_relative_to_sdg_repo(sdg_env):
    result = run(sdg_env, "--dry-run", "trigger", "payloads/p.json")
    assert result.returncode == 0, result.stderr
    assert f"{sdg_env['repo']}/payloads/p.json" in result.stdout
    assert "/api/v2/dags/autonomous_vehicle_dataset_generation_dag_k8s/dagRuns" in result.stdout


def test_trigger_missing_payload_fails(sdg_env):
    result = run(sdg_env, "--dry-run", "trigger", "nope.json")
    assert result.returncode != 0
    assert "Payload not found" in result.stderr


def test_script_is_executable():
    assert os.access(SCRIPT, os.X_OK)
