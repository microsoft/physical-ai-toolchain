from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

_RUNTIME_ROOT = Path(__file__).resolve().parents[1]
_RESULT_PREFIX = "REMOTER_TEST_RESULT="
_START_TIMEOUT_SECONDS = 10
_CLIENT_TIMEOUT_SECONDS = 30


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _subprocess_env(config_path: Path, key_path: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    python_path = env.get("PYTHONPATH")
    env.update(
        {
            "PRINTLOGLEVEL": "warning",
            "PYTHONPATH": str(_RUNTIME_ROOT) if not python_path else f"{_RUNTIME_ROOT}{os.pathsep}{python_path}",
            "PYTHONUNBUFFERED": "1",
            "REMOTER_CONFIG": str(config_path),
            "REMOTERHOST": "127.0.0.1",
            "REMOTERPORT": str(port),
            "REMOTER_KEY_FILE": str(key_path),
        }
    )
    env.pop("SERVER", None)
    return env


def _wait_for_port(process: subprocess.Popen[str], port: int, log_path: Path) -> None:
    deadline = time.monotonic() + _START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log = log_path.read_text(encoding="utf-8")
            raise RuntimeError(f"remoter server exited with status {process.returncode}\n{log}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    log = log_path.read_text(encoding="utf-8")
    raise TimeoutError(f"remoter server did not listen on port {port}\n{log}")


def _start_server(
    config_path: Path,
    key_path: Path,
    port: int,
    log_path: Path,
) -> subprocess.Popen[str]:
    env = _subprocess_env(config_path, key_path, port)
    env["SERVER"] = "true"
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "remoter.autoremote"],
            cwd=_RUNTIME_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    _wait_for_port(process, port, log_path)
    return process


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _write_config(config_path: Path, first_server_port: int, second_server_port: int) -> None:
    first_server = f"127.0.0.1:{first_server_port}"
    second_server = f"127.0.0.1:{second_server_port}"
    config = {
        "remoteclasses": [
            {
                "tests.remoter_test_fixture/Accumulator": {
                    "remoteloc": second_server,
                    "servercallablemethods": ["__init__", "add", "get_*"],
                }
            },
            {
                "tests.remoter_test_fixture/SingletonAccumulator": {
                    "remoteloc": second_server,
                    "servercallablemethods": ["__init__", "add", "get_*"],
                    "singleinstance": True,
                }
            },
            {
                "tests.remoter_test_fixture/StubAccumulator": {
                    "remoteloc": second_server,
                    "servercallablemethods": ["__init__", "add", "describe", "get_*"],
                }
            },
        ],
        "stubs": {
            "tests.remoter_test_fixture/StubAccumulator": "tests.remoter_test_fixture/StubbedAccumulator",
            "tests.remoter_test_fixture//create_stub_accumulator": (
                "tests.remoter_test_fixture//create_stubbed_accumulator"
            ),
        },
        "remotefuncs": [
            {"tests.remoter_test_fixture//add": {"remoteloc": first_server}},
            {"tests.remoter_test_fixture//multiply": {"remoteloc": second_server}},
            {"tests.remoter_test_fixture//first_server_pid": {"remoteloc": first_server}},
            {"tests.remoter_test_fixture//second_server_pid": {"remoteloc": second_server}},
            {"tests.remoter_test_fixture//fail": {"remoteloc": first_server}},
            {"tests.remoter_test_fixture//create_accumulator": {"remoteloc": first_server}},
            {"tests.remoter_test_fixture//create_stub_accumulator": {"remoteloc": second_server}},
        ],
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _parse_result(output: str) -> dict[str, Any]:
    for line in output.splitlines():
        if line.startswith(_RESULT_PREFIX):
            return json.loads(line.removeprefix(_RESULT_PREFIX))
    raise AssertionError(f"client did not emit {_RESULT_PREFIX!r}\n{output}")


def test_authenticated_remoter_routes_functions_and_classes_across_processes(tmp_path: Path) -> None:
    first_server_port = _free_port()
    second_server_port = _free_port()
    client_port = _free_port()
    config_path = tmp_path / "remote.yaml"
    key_path = tmp_path / "remoter.key"
    key_path.write_bytes(b"integration-test-remoter-key-32b")
    _write_config(config_path, first_server_port, second_server_port)

    servers: list[subprocess.Popen[str]] = []
    try:
        servers.append(_start_server(config_path, key_path, first_server_port, tmp_path / "server-1.log"))
        servers.append(_start_server(config_path, key_path, second_server_port, tmp_path / "server-2.log"))

        client = subprocess.run(
            [sys.executable, "-m", "tests.remoter_test_client"],
            cwd=_RUNTIME_ROOT,
            env=_subprocess_env(config_path, key_path, client_port),
            capture_output=True,
            text=True,
            timeout=_CLIENT_TIMEOUT_SECONDS,
            check=False,
        )

        assert client.returncode == 0, client.stdout + client.stderr
        result = _parse_result(client.stdout)
        assert result == {
            "add": 15,
            "multiply": 42,
            "first_server_pid": result["first_server_pid"],
            "second_server_pid": result["second_server_pid"],
            "concurrent": [11, 12, 13],
            "remote_error": result["remote_error"],
            "client_pid": result["client_pid"],
            "constructor_initial_value": 10,
            "constructor_added_value": 15,
            "constructor_synced_value": 21,
            "constructor_server_pid": result["constructor_server_pid"],
            "factory_initial_value": 30,
            "factory_added_value": 34,
            "factory_server_pid": result["factory_server_pid"],
            "distinct_remote_objects": True,
            "singleton_same_id": True,
            "singleton_second_initial_value": 105,
            "singleton_shared_value": 110,
            "stub_proxy_type": "StubAccumulator",
            "stub_added_value": 55,
            "stub_attribute_value": 55,
            "stub_description": "stubbed:StubbedAccumulator",
            "stub_server_pid": result["stub_server_pid"],
        }
        assert "ValueError" in result["remote_error"]
        assert "expected remote failure" in result["remote_error"]
        assert result["constructor_server_pid"] != result["client_pid"]
        assert result["factory_server_pid"] != result["client_pid"]
        assert result["first_server_pid"] != result["client_pid"]
        assert result["second_server_pid"] != result["client_pid"]
        assert result["first_server_pid"] != result["second_server_pid"]
        assert result["stub_server_pid"] == result["constructor_server_pid"]
    finally:
        for server in reversed(servers):
            _stop_process(server)
