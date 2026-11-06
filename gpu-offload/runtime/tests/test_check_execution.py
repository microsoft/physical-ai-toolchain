from __future__ import annotations

import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from remoter import simplelog

CHECKER = Path(__file__).resolve().parents[2] / "examples/first-run/scripts/check-execution.sh"
SERVER = "first-run-client-remote-server-cpu-test-pod"
RESULT = json.dumps({"executed_by": SERVER, "predictions": [1, 4, 9, 16]})


def _check_execution(tmp_path: Path, logs: str) -> subprocess.CompletedProcess[str]:
    for name, body in {
        "kubectl": 'printf "%s\\n" "$TEST_LOGS"',
        "sleep": ":",
    }.items():
        command = tmp_path / name
        command.write_text(f"#!/usr/bin/env bash\n{body}\n")
        command.chmod(0o755)
    return subprocess.run(
        ["bash", str(CHECKER)],
        env={
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "GPU_OFFLOAD_PLATFORM": "cpu",
            "GPU_OFFLOAD_RUNTIME": "kind",
            "TEST_LOGS": logs,
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    "logs",
    [
        RESULT,
        f"2026-10-27 14:17:02 INFO {RESULT}",
        f"runtime starting\n2026-10-27 14:17:02 INFO {RESULT} runtime message\nruntime stopped",
        f'{RESULT}\n{{"executed_by": "{SERVER}", "gpu_check": true}}',
    ],
)
def test_execution_result_amid_runtime_logs(tmp_path: Path, logs: str) -> None:
    completed = _check_execution(tmp_path, logs)
    assert completed.returncode == 0, completed.stderr
    assert f"Remote execution verified on {SERVER}" in completed.stdout


def test_result_between_logger_prefix_and_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    logger = simplelog.SimpleLog(str(tmp_path / "runtime.log"))
    logger.loglevel = 50
    logger.printlevel = 20
    capsys.readouterr()
    prefix_written = threading.Event()
    result_written = threading.Event()

    def paused_print(message: str, *, end: str = "\n", color: str | None = None, flush: bool = False) -> None:
        print(message, end=end, flush=flush)
        if end == "":
            prefix_written.set()
            assert result_written.wait(5), "Application did not write its result"

    monkeypatch.setattr(simplelog, "cprint", paused_print)
    with ThreadPoolExecutor(max_workers=1) as executor:
        logged = executor.submit(logger.info, "runtime message")
        try:
            assert prefix_written.wait(5), "Logger did not write its prefix"
            print(RESULT, flush=True)
        finally:
            result_written.set()
        logged.result(timeout=5)
    logger.close()

    logs = capsys.readouterr().out
    assert f"INFO     {RESULT}\nruntime message\n" in logs
    completed = _check_execution(tmp_path, logs)
    assert completed.returncode == 0, completed.stderr
    assert f"Remote execution verified on {SERVER}" in completed.stdout


@pytest.mark.parametrize(
    ("logs", "diagnostic"),
    [
        ("runtime starting", "No execution result found"),
        ('{"executed_by": "server", "gpu_check": true}', "No execution result found"),
        ('INFO "executed_by": "server"', "Malformed or truncated execution result"),
        (f"INFO {RESULT[:-1]}", "Malformed or truncated execution result"),
        ('{"executed_by": invalid}', "Malformed or truncated execution result"),
        ('{"executed_by": null}', "Expected executed_by"),
        ('{"executed_by": 123}', "Expected executed_by"),
        (json.dumps({"executed_by": "first-run-client", "predictions": [1, 4, 9, 16]}), "Expected executed_by"),
        (json.dumps({"executed_by": SERVER.replace("-cpu-", "-nvidia-")}), "Expected executed_by"),
        (json.dumps({"executed_by": SERVER}), "Expected predictions"),
        (json.dumps({"executed_by": SERVER, "predictions": [1, 4, 9]}), "Expected predictions"),
        (json.dumps({"executed_by": SERVER, "predictions": [1, 4, 9, 17]}), "Expected predictions"),
        (f'{RESULT}\n{{"executed_by": "first-run-client"}}', "Expected executed_by"),
    ],
)
def test_invalid_execution_result_fails(tmp_path: Path, logs: str, diagnostic: str) -> None:
    completed = _check_execution(tmp_path, logs)
    assert completed.returncode != 0
    assert diagnostic in completed.stderr
    assert "Remote execution verified" not in completed.stdout
    if "Malformed" in diagnostic:
        assert f"Log line: {logs}" in completed.stderr
    if "No execution result" in diagnostic:
        assert "Last logs:" in completed.stderr
        assert logs in completed.stderr
