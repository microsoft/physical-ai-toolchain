from __future__ import annotations

import importlib.util
from io import BytesIO
from pathlib import Path
from types import ModuleType

import pytest


def _load_client() -> ModuleType:
    path = Path(__file__).parents[1] / "infrastructure/setup/cleanup/redis_tls_client.py"
    spec = importlib.util.spec_from_file_location("redis_tls_client", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load Redis TLS client from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRedisTlsClient:
    @pytest.fixture
    def client(self) -> ModuleType:
        return _load_client()

    def test_encodes_redis_commands(self, client: ModuleType) -> None:
        assert client._encode_command("AUTH", "secret") == b"*2\r\n$4\r\nAUTH\r\n$6\r\nsecret\r\n"

    @pytest.mark.parametrize(
        ("operation", "script", "expected"),
        [
            ("PING", None, ("PING",)),
            ("EVAL", "return 1", ("EVAL", "return 1", "0")),
        ],
    )
    def test_builds_supported_operations(
        self,
        client: ModuleType,
        operation: str,
        script: str | None,
        expected: tuple[str, ...],
    ) -> None:
        assert client._build_operation_command(operation, script) == expected

    @pytest.mark.parametrize(
        ("operation", "script"),
        [
            ("FLUSHALL", None),
            ("EVAL", None),
        ],
    )
    def test_rejects_unsupported_operations(
        self,
        client: ModuleType,
        operation: str,
        script: str | None,
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported Redis operation"):
            client._build_operation_command(operation, script)

    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            (b"+PONG\r\n", "PONG"),
            (b":3\r\n", "3"),
        ],
    )
    def test_reads_supported_responses(
        self,
        client: ModuleType,
        response: bytes,
        expected: str,
    ) -> None:
        assert client._read_response(BytesIO(response)) == expected

    def test_raises_redis_errors(self, client: ModuleType) -> None:
        with pytest.raises(RuntimeError, match="Redis error: invalid password"):
            client._read_response(BytesIO(b"-invalid password\r\n"))

    def test_rejects_unexpected_response_types(self, client: ModuleType) -> None:
        with pytest.raises(RuntimeError, match="Unexpected Redis response type"):
            client._read_response(BytesIO(b"$4\r\nPONG\r\n"))
