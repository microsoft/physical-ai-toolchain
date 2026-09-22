from __future__ import annotations

import os
import socket
import ssl
from typing import BinaryIO


def _encode_command(*parts: str) -> bytes:
    encoded = [part.encode() for part in parts]
    return f"*{len(encoded)}\r\n".encode() + b"".join(f"${len(part)}\r\n".encode() + part + b"\r\n" for part in encoded)


def _read_response(stream: BinaryIO) -> str:
    prefix = stream.read(1)
    line = stream.readline().decode().rstrip("\r\n")
    if prefix == b"-":
        raise RuntimeError(f"Redis error: {line}")
    if prefix not in {b"+", b":"}:
        raise RuntimeError(f"Unexpected Redis response type: {prefix!r}")
    return line


def _build_operation_command(operation: str, script: str | None = None) -> tuple[str, ...]:
    if operation == "PING":
        return ("PING",)
    if operation == "EVAL" and script is not None:
        return ("EVAL", script, "0")
    raise ValueError(f"Unsupported Redis operation: {operation}")


def main() -> None:
    host = os.environ["REDIS_HOST"]
    port = int(os.environ["REDIS_PORT"])
    password = os.environ["REDIS_PASSWORD"]
    operation = os.environ["REDIS_OPERATION"]
    command = _build_operation_command(operation, os.environ.get("REDIS_SCRIPT"))

    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with (
        socket.create_connection((host, port), timeout=30) as raw_socket,
        context.wrap_socket(raw_socket, server_hostname=host) as tls_socket,
    ):
        stream = tls_socket.makefile("rwb", buffering=0)
        stream.write(_encode_command("AUTH", password))
        _read_response(stream)
        stream.write(_encode_command(*command))
        print(_read_response(stream))


if __name__ == "__main__":
    main()
