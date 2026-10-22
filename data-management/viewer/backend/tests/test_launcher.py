"""Exercise the Bash launcher's package resolution, backend invocation, and failure reporting."""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest


def _get_unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture
def launcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    for tool in ("bash", "python3", "node", "npm", "uv", "curl"):
        if shutil.which(tool) is None:
            pytest.skip(f"Launcher prerequisite unavailable: {tool}")

    root = tmp_path / "repo with spaces"
    viewer = root / "data-management" / "viewer"
    backend = viewer / "backend"
    backend_bin = backend / ".venv" / "bin"
    backend_bin.mkdir(parents=True)
    (viewer / "frontend").mkdir()
    shutil.copy2(Path(__file__).resolve().parents[2] / "start.sh", viewer / "start.sh")

    vite = root / "node_modules" / "vite"
    (vite / "bin").mkdir(parents=True)
    (vite / "package.json").write_text('{"name":"vite","version":"0.0.0"}\n', encoding="utf-8")
    (vite / "bin" / "vite.js").write_text("process.exit(23)\n", encoding="utf-8")
    (backend_bin / "activate").write_text(
        f'export PATH={shlex.quote(str(backend_bin))}:"$PATH"\n',
        encoding="utf-8",
    )
    uv = backend_bin / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%q ' \"$@\" >> {shlex.quote(str(backend / 'uv-calls.txt'))}\n"
        f"printf '\\n' >> {shlex.quote(str(backend / 'uv-calls.txt'))}\n"
        'if [[ "$1 $2" == "pip check" ]]; then exit "${TEST_DEPENDENCY_CHECK_EXIT:-0}"; fi\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    uvicorn = backend_bin / "uvicorn"
    uvicorn.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(backend / 'uvicorn-args.txt'))}\n"
        "echo 'Backend child invoked'\n"
        "exit 23\n",
        encoding="utf-8",
    )
    uvicorn.chmod(0o755)
    (backend / ".env").write_text(f"DATA_DIR={root}\n", encoding="utf-8")

    for name in ("DATA_DIR", "VLM_JUDGE_ENABLED", "VLM_JUDGE_BACKEND", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", f"{backend_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HEALTH_TIMEOUT", "5")
    backend_port = _get_unused_port()
    frontend_port = _get_unused_port()
    while frontend_port == backend_port:
        frontend_port = _get_unused_port()
    monkeypatch.setenv("BACKEND_PORT", str(backend_port))
    monkeypatch.setenv("FRONTEND_PORT", str(frontend_port))
    yield viewer / "start.sh"


def test_check_resolves_hoisted_vite_without_starting_services(launcher: Path) -> None:
    result = subprocess.run(
        ["bash", str(launcher), "--check"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "no services started" in result.stdout
    assert "Backend child invoked" not in result.stdout


@pytest.mark.parametrize(
    ("mode_args", "expected_mode"),
    [([], "both"), (["--backend"], "backend"), (["--frontend"], "frontend")],
)
def test_config_preview_preserves_mode_without_starting_services(
    launcher: Path, monkeypatch: pytest.MonkeyPatch, mode_args: list[str], expected_mode: str
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    data_dir = launcher.parent / "uncreated dataset parent"
    result = subprocess.run(
        ["bash", str(launcher), "--config-preview", "--data-dir", str(data_dir), *mode_args],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Mode: {expected_mode}" in result.stdout
    assert "Log Level: info" in result.stdout
    assert f"Data Directory: {data_dir}" in result.stdout
    assert "Mutation: None" in result.stdout
    assert "\x1b[" not in result.stdout
    assert "Backend child invoked" not in result.stdout
    assert not data_dir.exists()


def test_backend_failure_is_reported_with_optional_env_keys_absent(launcher: Path) -> None:
    result = subprocess.run(
        ["bash", str(launcher), "--backend"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 23
    assert "Backend child invoked" in result.stdout
    assert "Backend exited before readiness" in result.stdout or "Backend failed to start" in result.stdout
    assert "All services stopped" in result.stdout


def test_vlm_setup_preserves_existing_optional_dependencies(launcher: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLM_JUDGE_ENABLED", "true")
    result = subprocess.run(
        ["bash", str(launcher), "--backend"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 23, result.stdout + result.stderr
    calls = [
        shlex.split(line)
        for line in (launcher.parent / "backend" / "uv-calls.txt").read_text(encoding="utf-8").splitlines()
    ]
    sync_calls = [args for args in calls if args and args[0] == "sync"]
    assert len(sync_calls) == 1
    assert "--inexact" not in sync_calls[0]
    assert "--no-default-groups" in sync_calls[0]
    assert "--all-packages" in sync_calls[0]
    assert _option_value(sync_calls[0], "--project") == str(launcher.parents[2])
    for extra in ("analysis", "export", "azure", "auth"):
        assert ["--extra", extra] in [sync_calls[0][index : index + 2] for index in range(len(sync_calls[0]) - 1)]


def _option_value(args: list[str], option: str) -> str | None:
    return args[args.index(option) + 1] if option in args else None


@pytest.mark.parametrize(("backend", "extra"), [("qwen3-vl", "qwen3-vl"), ("openai-compat", "openai")])
@pytest.mark.parametrize("dependency_status", [0, 19])
def test_judge_runtime_uses_frozen_evaluator_profile(
    launcher: Path, monkeypatch: pytest.MonkeyPatch, backend: str, extra: str, dependency_status: int
) -> None:
    monkeypatch.setenv("VLM_JUDGE_ENABLED", "true")
    monkeypatch.setenv("VLM_JUDGE_BACKEND", backend)
    monkeypatch.setenv("TEST_DEPENDENCY_CHECK_EXIT", str(dependency_status))
    result = subprocess.run(
        ["bash", str(launcher), "--backend"], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == (dependency_status or 23), result.stdout + result.stderr
    assert ("Backend child invoked" in result.stdout) is (dependency_status == 0)
    calls = [shlex.split(line) for line in (launcher.parent / "backend" / "uv-calls.txt").read_text().splitlines()]
    assert len(calls) == 2
    assert calls[0][0] == "sync"
    assert "--frozen" in calls[0]
    assert "--inexact" not in calls[0]
    assert "--no-default-groups" in calls[0]
    assert "--no-editable" in calls[0]
    assert "--all-packages" in calls[0]
    assert ["--extra", extra] in [calls[0][index : index + 2] for index in range(len(calls[0]) - 1)]
    assert _option_value(calls[0], "--project") == str(launcher.parents[2])
    assert _option_value(calls[0], "--python") == "3.12"
    assert "--group" not in calls[0]
    assert calls[1] == ["pip", "check", "--python", str(launcher.parent / "backend/.venv/bin/python")]


@pytest.mark.parametrize(("log_level", "expected_level"), [(None, "info"), ("debug", "debug")])
@pytest.mark.parametrize("entrypoint", ["bash", "node"])
def test_backend_runs_on_loopback_with_logging_config(
    launcher: Path, monkeypatch: pytest.MonkeyPatch, log_level: str | None, expected_level: str, entrypoint: str
) -> None:
    if log_level is not None:
        monkeypatch.setenv("LOG_LEVEL", log_level)
    command = (
        ["bash", str(launcher), "--backend"]
        if entrypoint == "bash"
        else ["node", str(Path(__file__).resolve().parents[2] / "start-backend.mjs")]
    )
    subprocess.run(command, cwd=launcher.parent, capture_output=True, text=True, timeout=10, check=False)

    if entrypoint == "bash":
        args = (launcher.parent / "backend" / "uvicorn-args.txt").read_text(encoding="utf-8").splitlines()
    else:
        args = shlex.split((launcher.parent / "backend" / "uv-calls.txt").read_text(encoding="utf-8"))
        assert args[:5] == ["run", "--no-sync", "--frozen", "--directory", "backend"]
        assert _option_value(args, "--project") == str(launcher.parents[2])
        assert _option_value(args, "--package") == "lerobot-annotation-api"
        args = args[args.index("uvicorn") + 1 :]
    assert args[0] == "src.api.main:app"
    assert _option_value(args, "--log-config") == "logging.json"
    assert _option_value(args, "--log-level") == expected_level
    assert _option_value(args, "--host") == "127.0.0.1"
    assert _option_value(args, "--port") == os.environ["BACKEND_PORT"]
    assert "--reload" in args
