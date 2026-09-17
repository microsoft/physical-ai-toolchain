"""Bounded local WebSocket transport with explicit contract and request identities."""

from __future__ import annotations

import ipaddress
import math
import time
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

import msgpack
import numpy as np

from .backends import Backend
from .config import MAX_MESSAGE_BYTES, EvaluationConfig, canonical, finite_array, require

_ARRAY_CODE = 42
_DTYPES = {"|u1", "<f4", "<f8"}


def _pack_array(value: Any) -> msgpack.ExtType:
    require(isinstance(value, np.ndarray) and value.dtype.str in _DTYPES, "Unsupported wire value or array dtype")
    require(
        1 <= value.ndim <= 3 and all(0 < size <= 100000 for size in value.shape) and value.nbytes <= MAX_MESSAGE_BYTES,
        "Array exceeds wire limits",
    )
    payload = {"dtype": value.dtype.str, "shape": list(value.shape), "data": value.tobytes(order="C")}
    return msgpack.ExtType(_ARRAY_CODE, msgpack.packb(payload, use_bin_type=True))


def _pairs(values: list[tuple[Any, Any]]) -> dict[str, Any]:
    require(all(isinstance(key, str) for key, _ in values), "Wire maps require string keys")
    result = dict(values)
    require(len(result) == len(values), "Duplicate wire keys")
    return result


def _unpack_array(code: int, data: bytes) -> np.ndarray:
    require(code == _ARRAY_CODE and len(data) <= MAX_MESSAGE_BYTES, "Unknown or oversized wire extension")
    value = msgpack.unpackb(data, raw=False, strict_map_key=True, object_pairs_hook=_pairs)
    require(isinstance(value, dict) and set(value) == {"dtype", "shape", "data"}, "Malformed wire array")
    require(isinstance(value["dtype"], str) and value["dtype"] in _DTYPES, "Unsafe wire dtype")
    shape = value["shape"]
    require(
        isinstance(shape, list)
        and 1 <= len(shape) <= 3
        and all(type(size) is int and 0 < size <= 100000 for size in shape),
        "Malformed wire shape",
    )
    dtype = np.dtype(value["dtype"])
    require(
        isinstance(value["data"], bytes) and math.prod(shape) * dtype.itemsize == len(value["data"]),
        "Array byte count differs from shape",
    )
    return np.frombuffer(value["data"], dtype=dtype).reshape(shape).copy()


def pack(value: dict[str, Any]) -> bytes:
    """Serialize arrays without pickle, object arrays, or unbounded frame sizes."""
    data = msgpack.packb(value, default=_pack_array, use_bin_type=True)
    require(len(data) <= MAX_MESSAGE_BYTES, "Message exceeds transport limit")
    return data


def unpack(data: Any) -> dict[str, Any]:
    """Reject text responses, malformed arrays, and unexpected extension types."""
    require(isinstance(data, bytes) and len(data) <= MAX_MESSAGE_BYTES, "Expected bounded binary message")
    result = msgpack.unpackb(
        data,
        raw=False,
        ext_hook=_unpack_array,
        strict_map_key=True,
        object_pairs_hook=_pairs,
        max_array_len=100000,
        max_map_len=10000,
        max_bin_len=MAX_MESSAGE_BYTES,
        max_ext_len=MAX_MESSAGE_BYTES,
        max_str_len=1024 * 1024,
    )
    require(isinstance(result, dict), "Expected wire object")
    return result


def _loopback(host: str) -> None:
    require(ipaddress.ip_address(host).is_loopback, "Policy transport is loopback-only; use a tunnel for remote hosts")


class PolicySession:
    """One sequential evaluation session; no other client shares policy state concurrently."""

    def __init__(self, config: EvaluationConfig, backend: Backend) -> None:
        self.config = config
        self.backend = backend
        self.session_id = uuid4().hex
        self.next_request = 0
        self.episode_index = -1
        self.episode_requests = 0
        self.closed = False

    def hello(self) -> dict[str, Any]:
        """Expose the exact inference contract and actual backend provenance."""
        canonical(self.backend.provenance)
        return {"contract": self.config.metadata(), "session_id": self.session_id, "producer": self.backend.provenance}

    def infer(self, request: dict[str, Any]) -> dict[str, Any]:
        """Validate identities and permitted observations before performing one inference."""
        require(not self.closed, "Policy session is closed")
        try:
            require(
                set(request) == {"session_id", "request_id", "config_sha256", "seed", "reset", "state", "images"},
                "Unexpected policy request fields",
            )
            require(
                request["session_id"] == self.session_id and request["config_sha256"] == self.config.sha256,
                "Policy session/config identity differs",
            )
            require(
                type(request["request_id"]) is int and request["request_id"] == self.next_request,
                "Duplicate or out-of-order policy request",
            )
            require(type(request["seed"]) is int and type(request["reset"]) is bool, "Invalid seed/reset types")
            if request["reset"]:
                self.episode_index += 1
                self.episode_requests = 0
            require(0 <= self.episode_index < len(self.config.episodes), "Episode must begin with a reset")
            require(request["seed"] == self.config.episodes[self.episode_index][1], "Seed differs from declared order")
            require(
                self.episode_requests < math.ceil(self.config.max_steps / self.config.execution_horizon),
                "Episode inference budget exceeded",
            )
            state, images = self.config.validate_observation(request["state"], request["images"])
            started = time.perf_counter()
            raw = self.backend.infer(state.copy(), images, seed=request["seed"], reset=request["reset"])
            actions = self.config.decode_actions(raw, state)
            self.next_request += 1
            self.episode_requests += 1
            return {
                "session_id": self.session_id,
                "request_id": request["request_id"],
                "seed": request["seed"],
                "config_sha256": self.config.sha256,
                "actions": actions,
                "model_actions": np.asarray(raw, dtype=np.float64),
                "inference_ms": (time.perf_counter() - started) * 1000,
            }
        except Exception:
            self.closed = True
            raise


class PolicyClient:
    """Fail closed on stale, timed-out, disconnected, or contract-incompatible policy responses."""

    def __init__(
        self,
        config: EvaluationConfig,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        socket_path: Path | None = None,
    ) -> None:
        from websockets.sync.client import connect, unix_connect

        self.config = config
        self.request_id = 0
        self.connection = None
        kwargs = {
            "compression": None,
            "max_size": MAX_MESSAGE_BYTES,
            "open_timeout": config.timeout_seconds,
            "close_timeout": 2,
            "max_queue": 1,
        }
        try:
            if socket_path is None:
                _loopback(host)
                address = f"[{host}]" if ":" in host else host
                self.connection = connect(f"ws://{address}:{port}", proxy=None, **kwargs)
            else:
                require(socket_path.is_socket(), "Selected policy socket is missing")
                self.connection = unix_connect(str(socket_path), uri="ws://localhost", **kwargs)
            self.metadata = unpack(self.connection.recv(timeout=config.timeout_seconds))
            require(set(self.metadata) == {"contract", "session_id", "producer"}, "Invalid policy handshake")
            require(
                canonical(self.metadata["contract"]) == canonical(config.metadata()),
                "Policy handshake contract differs",
            )
            require(
                isinstance(self.metadata["session_id"], str) and bool(self.metadata["session_id"]), "Missing session ID"
            )
            canonical(self.metadata)
        except Exception:
            self.close()
            raise

    def infer(self, state: np.ndarray, images: dict[str, np.ndarray], *, seed: int, reset: bool) -> dict[str, Any]:
        """Return a matched absolute action chunk and the raw backend output for audit."""
        require(self.connection is not None, "Policy connection is closed")
        try:
            state, images = self.config.validate_observation(state, images)
            request = {
                "session_id": self.metadata["session_id"],
                "request_id": self.request_id,
                "config_sha256": self.config.sha256,
                "seed": seed,
                "reset": reset,
                "state": state,
                "images": images,
            }
            self.connection.send(pack(request))
            response = unpack(self.connection.recv(timeout=self.config.timeout_seconds))
            require(
                set(response)
                == {"session_id", "request_id", "config_sha256", "seed", "actions", "model_actions", "inference_ms"},
                "Unexpected policy response fields",
            )
            for key in ("session_id", "request_id", "config_sha256", "seed"):
                require(
                    type(response[key]) is type(request[key]) and response[key] == request[key],
                    f"Stale or mismatched policy response: {key}",
                )
            response["actions"] = finite_array(
                response["actions"], (self.config.policy.action_horizon, len(self.config.channels)), "Policy actions"
            )
            require(
                np.array_equal(response["actions"], self.config.decode_actions(response["model_actions"], state)),
                "Policy action mapping differs from declared conversion",
            )
            elapsed = response["inference_ms"]
            require(
                type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0, "Invalid inference timing"
            )
            self.request_id += 1
            return response
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Close transport without retrying or replaying a request."""
        if self.connection is not None:
            self.connection.close()
            self.connection = None


def serve_policy(
    config: EvaluationConfig,
    backend: Backend,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    socket_path: Path | None = None,
) -> None:
    """Serve one active client on loopback or a local Unix socket; never expose a public listener."""
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.server import serve, unix_serve

    lease = Lock()

    def handler(connection: Any) -> None:
        if not lease.acquire(blocking=False):
            connection.close(code=1013, reason="An evaluation session already owns the policy")
            return
        session = PolicySession(config, backend)
        try:
            connection.send(pack(session.hello()))
            for data in connection:
                connection.send(pack(session.infer(unpack(data))))
        except ConnectionClosed:
            pass
        except Exception:
            connection.close(code=1011, reason="Policy contract or inference failed")
            raise
        finally:
            session.closed = True
            lease.release()

    options = {"compression": None, "max_size": MAX_MESSAGE_BYTES, "max_queue": 1, "close_timeout": 2}
    if socket_path is None:
        _loopback(host)
        with serve(handler, host, port, **options) as server:
            server.serve_forever()
    else:
        require(
            socket_path.parent.is_dir() and not socket_path.exists() and not socket_path.is_symlink(),
            "Policy socket must be fresh beneath an existing directory",
        )
        with unix_serve(handler, str(socket_path), **options) as server:
            socket_path.chmod(0o600)
            server.serve_forever()
