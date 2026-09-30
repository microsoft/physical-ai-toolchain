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


def _option_value(args: list[str], option: str) -> str | None:
    return args[args.index(option) + 1] if option in args else None


@pytest.mark.parametrize(("log_level", "expected_level"), [(None, "info"), ("debug", "debug")])
def test_backend_runs_on_loopback_with_logging_config(
    launcher: Path, monkeypatch: pytest.MonkeyPatch, log_level: str | None, expected_level: str
) -> None:
    if log_level is not None:
        monkeypatch.setenv("LOG_LEVEL", log_level)
    subprocess.run(["bash", str(launcher), "--backend"], capture_output=True, text=True, timeout=10, check=False)

    args = (launcher.parent / "backend" / "uvicorn-args.txt").read_text(encoding="utf-8").splitlines()
    assert args[0] == "src.api.main:app"
    assert _option_value(args, "--log-config") == "logging.json"
    assert _option_value(args, "--log-level") == expected_level
    assert _option_value(args, "--host") == "127.0.0.1"
    assert _option_value(args, "--port") == os.environ["BACKEND_PORT"]
    assert "--reload" in args
