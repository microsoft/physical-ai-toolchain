"""CPU-only wire and session tests with a finite external backend and real loopback sockets."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from threading import Event, Thread, current_thread
from typing import Any

import msgpack
import numpy as np
import pytest
import websockets.sync.client
from sil.vla.config import MAX_MESSAGE_BYTES, EvaluationConfig, load_config
from sil.vla.transport import PolicyClient, PolicySession, pack, unpack
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK
from websockets.sync.server import ServerConnection, serve

_WIRE_TIMEOUT = 5.0
_WireHandler = Callable[[ServerConnection], None]
_ServerFactory = Callable[[_WireHandler], AbstractContextManager[int]]


@pytest.fixture
def config(tmp_path: Path) -> EvaluationConfig:
    """Load a tiny, non-identity mapping and a two-episode finite budget from JSON."""
    source = {
        "schema_version": 1,
        "instruction": "Move the observed part into the tray.",
        "channels": ["shoulder", "gripper", "wrist"],
        "image_shapes": {"front": [2, 4, 3], "wrist": [4, 2, 3]},
        "policy": {
            "backend": "openpi",
            "checkpoint": "unused-local-checkpoint",
            "checkpoint_sha256": "a" * 64,
            "training_config": "synthetic_cpu_contract",
            "state_key": "state",
            "prompt_key": "prompt",
            "image_keys": {"front": "images/front", "wrist": "images/wrist"},
            "state_mapping": {"indices": [1, 0, 2], "scale": [10, 2, 0.25], "offset": [100, 200, 300]},
            "action_mapping": {"indices": [2, 0, 1], "scale": [2, -1, 0.5], "offset": [1, 0.25, -0.125]},
            "delta_indices": [0, 2],
            "action_horizon": 3,
        },
        "task": {
            "adapter": "synthetic_external_task",
            "producer_root": "unused-producer",
            "expert_config": "unused.json",
            "stage": "unused.usda",
            "reference_root": "unused-assets",
            "runtime_assets": [],
        },
        "evaluation": {
            "control_hz": 30,
            "execution_horizon": 2,
            "seeds": {"validation": [11], "test": [23]},
            "max_steps": 3,
            "timeout_seconds": _WIRE_TIMEOUT,
            "minimum_free_gib": 0,
            "joint_limit_behavior": "reject",
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    return load_config(path)


class _FiniteBackend:
    """Record only permitted inputs and return synthetic arrays without a model runtime."""

    instruction: str
    actions: np.ndarray
    provenance: dict[str, Any]
    calls: list[dict[str, Any]]
    error: Exception | None
    mutate_inputs: bool

    def __init__(self, instruction: str) -> None:
        self.instruction = instruction
        self.actions = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float64)
        self.provenance = {"backend": "synthetic_external", "model_loaded": False, "device": "cpu"}
        self.calls = []
        self.error = None
        self.mutate_inputs = False

    def infer(self, state: np.ndarray, images: dict[str, np.ndarray], *, seed: int, reset: bool) -> np.ndarray:
        self.calls.append(
            {
                "state": state.copy(),
                "images": {key: image.copy() for key, image in images.items()},
                "instruction": self.instruction,
                "seed": seed,
                "reset": reset,
            }
        )
        if self.error is not None:
            raise self.error
        if self.mutate_inputs:
            state[:] = -999
            for image in images.values():
                image[:] = 255
        return self.actions.copy()


@pytest.fixture
def backend(config: EvaluationConfig) -> _FiniteBackend:
    """Give the external producer only the configured task instruction."""
    return _FiniteBackend(config.instruction)


@pytest.fixture
def session(config: EvaluationConfig, backend: _FiniteBackend) -> PolicySession:
    """Exercise the production session directly, without patching its methods."""
    return PolicySession(config, backend)


def _request(session: PolicySession, *, request_id: int = 0, seed: int = 11, reset: bool = True) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "request_id": request_id,
        "config_sha256": session.config.sha256,
        "seed": seed,
        "reset": reset,
        "state": np.array([10, 20, 30], dtype=np.float64),
        "images": {
            key: np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)
            for key, shape in session.config.image_shapes.items()
        },
    }


def _wire_array(payload: Any, *, code: int = 42) -> bytes:
    return msgpack.packb({"array": msgpack.ExtType(code, msgpack.packb(payload, use_bin_type=True))}, use_bin_type=True)


def _duplicate_map(key: str, first: Any, second: Any) -> bytes:
    encoder = msgpack.Packer(use_bin_type=True)
    return (
        encoder.pack_map_header(2) + encoder.pack(key) + encoder.pack(first) + encoder.pack(key) + encoder.pack(second)
    )


@contextmanager
def _loopback_server(handler: _WireHandler) -> Iterator[int]:
    ready = Event()
    connections = []
    handlers = []
    failures = []
    worker = None

    def guarded(connection: ServerConnection) -> None:
        connections.append(connection)
        handlers.append(current_thread())
        try:
            handler(connection)
        except ConnectionClosedOK:
            pass
        except BaseException as error:
            failures.append(error)
        finally:
            connection.close()

    try:
        with serve(
            guarded,
            "127.0.0.1",
            0,
            compression=None,
            max_size=MAX_MESSAGE_BYTES,
            max_queue=1,
            open_timeout=_WIRE_TIMEOUT,
            close_timeout=1,
            ping_interval=None,
        ) as server:

            def accept_connections() -> None:
                ready.set()
                try:
                    server.serve_forever()
                except BaseException as error:
                    failures.append(error)

            worker = Thread(target=accept_connections, name="vla-test-loopback", daemon=True)
            worker.start()
            try:
                assert ready.wait(_WIRE_TIMEOUT), "Loopback server did not start"
                yield server.socket.getsockname()[1]
            finally:
                for connection in connections:
                    connection.close()
    finally:
        if worker is not None:
            worker.join(_WIRE_TIMEOUT)
            assert not worker.is_alive(), "Loopback listener did not stop"
        for thread in handlers:
            thread.join(_WIRE_TIMEOUT)
            assert not thread.is_alive(), "Loopback handler did not stop"
        assert not failures, f"Loopback server errors: {failures!r}"


@pytest.fixture
def wire_server(monkeypatch: pytest.MonkeyPatch) -> _ServerFactory:
    """Keep actual socket tests on loopback regardless of the operator's HTTP proxy settings."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")
    return _loopback_server


class TestWireCodec:
    @pytest.mark.parametrize("dtype", ["uint8", "float32", "float64"])
    def test_real_roundtrip_preserves_dtype_shape_values_and_copies(self, dtype: str) -> None:
        array = np.arange(24, dtype=dtype).reshape(2, 4, 3)[:, ::-1]
        expected = array.copy()
        encoded = pack({"images": {"front": array}, "seed": 11, "reset": True, "instruction": "measured only"})

        result = unpack(encoded)
        extension = msgpack.unpackb(encoded, raw=False)["images"]["front"]
        descriptor = msgpack.unpackb(extension.data, raw=False)
        array[:] = 0

        assert extension.code == 42
        assert descriptor == {"dtype": expected.dtype.str, "shape": [2, 4, 3], "data": expected.tobytes(order="C")}
        np.testing.assert_array_equal(result["images"]["front"], expected)
        assert result["images"]["front"].dtype == expected.dtype
        assert result["images"]["front"].flags.c_contiguous
        assert result["images"]["front"].flags.writeable
        assert result["seed"] == 11 and result["reset"] is True

    def test_decoder_accepts_independently_encoded_arrays(self) -> None:
        data = np.array([1.25, -2.5], dtype="<f4")
        encoded = _wire_array({"dtype": "<f4", "shape": [2], "data": data.tobytes()})

        result = unpack(encoded)

        np.testing.assert_array_equal(result["array"], data)
        assert result["array"].dtype == np.dtype("<f4")

    @pytest.mark.parametrize("dtype", [object, bool, np.int32, np.complex64, ">f4", "U2"])
    def test_encoder_rejects_object_and_unsupported_array_dtypes(self, dtype: Any) -> None:
        with pytest.raises(ValueError, match="Unsupported wire value or array dtype"):
            pack({"array": np.ones(3, dtype=dtype)})

    @pytest.mark.parametrize("shape", [(), (1, 1, 1, 1)])
    def test_encoder_rejects_unsupported_array_rank(self, shape: tuple[int, ...]) -> None:
        with pytest.raises(ValueError, match="Array exceeds wire limits"):
            pack({"array": np.zeros(shape, dtype=np.float64)})

    @pytest.mark.parametrize("shape", [(0,), (2, 0, 3), (100001,)])
    def test_encoder_rejects_shapes_that_its_decoder_cannot_accept(self, shape: tuple[int, ...]) -> None:
        array = np.zeros(shape, dtype=np.uint8)
        encoded = _wire_array({"dtype": "|u1", "shape": list(shape), "data": array.tobytes()})

        with pytest.raises(ValueError, match="Malformed wire shape"):
            unpack(encoded)
        with pytest.raises(ValueError):
            pack({"array": array})

    def test_largest_supported_axis_roundtrips_at_the_inclusive_boundary(self) -> None:
        array = np.arange(100000, dtype=np.float32)

        result = unpack(pack({"array": array}))

        np.testing.assert_array_equal(result["array"], array)

    @pytest.mark.parametrize("dtype", ["|O", "<i8", ">f4", "garbage", ["<f4"], True])
    def test_decoder_rejects_unsafe_extension_dtypes(self, dtype: Any) -> None:
        encoded = _wire_array({"dtype": dtype, "shape": [1], "data": b"\0" * 8})

        with pytest.raises(ValueError, match="Unsafe wire dtype"):
            unpack(encoded)

    @pytest.mark.parametrize("shape", [[], [0], [-1], [True], [1.0], [100001], [1, 1, 1, 1], "1"])
    def test_decoder_rejects_invalid_dimensions_without_allocating(self, shape: Any) -> None:
        with pytest.raises(ValueError, match="Malformed wire shape"):
            unpack(_wire_array({"dtype": "|u1", "shape": shape, "data": b"\0"}))

    @pytest.mark.parametrize(
        "payload",
        [
            {"dtype": "<f8", "shape": [2], "data": b"\0" * 8},
            {"dtype": "<f4", "shape": [1], "data": b"\0" * 8},
            {"dtype": "|u1", "shape": [100000, 100000, 100000], "data": b"\0"},
            {"dtype": "|u1", "shape": [1], "data": "x"},
            {"dtype": "|u1", "shape": [1], "data": [0]},
        ],
    )
    def test_extension_byte_count_must_exactly_match_shape(self, payload: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match="Array byte count differs from shape"):
            unpack(_wire_array(payload))

    @pytest.mark.parametrize(
        "payload",
        [[], None, {}, {"dtype": "|u1", "shape": [1]}, {"dtype": "|u1", "shape": [1], "data": b"x", "oracle": True}],
    )
    def test_extension_requires_exact_descriptor_fields(self, payload: Any) -> None:
        with pytest.raises(ValueError, match="Malformed wire array"):
            unpack(_wire_array(payload))

    def test_unknown_extension_code_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown or oversized wire extension"):
            unpack(_wire_array({"dtype": "|u1", "shape": [1], "data": b"x"}, code=43))

    @pytest.mark.parametrize("location", ["root", "nested", "extension"])
    def test_duplicate_wire_keys_are_rejected_at_every_depth(self, location: str) -> None:
        duplicate = _duplicate_map("seed", 11, 23)
        if location == "nested":
            encoder = msgpack.Packer(use_bin_type=True)
            duplicate = encoder.pack_map_header(1) + encoder.pack("nested") + duplicate
        elif location == "extension":
            duplicate = msgpack.packb(
                {"array": msgpack.ExtType(42, _duplicate_map("dtype", "|u1", "<f8"))}, use_bin_type=True
            )

        with pytest.raises(ValueError, match="Duplicate wire keys"):
            unpack(duplicate)

    @pytest.mark.parametrize("value", [{1: "value"}, {b"key": "value"}, {"nested": {b"key": "value"}}])
    def test_wire_maps_require_string_keys(self, value: dict[Any, Any]) -> None:
        with pytest.raises(ValueError):
            unpack(msgpack.packb(value, use_bin_type=True))

    @pytest.mark.parametrize("data", ["text", bytearray(b"\x80"), None, b"", b"\xc1", b"\x81", b"\x80\x80"])
    def test_text_truncated_and_extra_messages_are_rejected(self, data: Any) -> None:
        with pytest.raises(ValueError):
            unpack(data)

    @pytest.mark.parametrize("value", [[], None, 1, True, "text"])
    def test_wire_root_must_be_an_object(self, value: Any) -> None:
        with pytest.raises(ValueError, match="Expected wire object"):
            unpack(msgpack.packb(value, use_bin_type=True))

    @pytest.mark.parametrize("boundary", ["decode", "encode", "array"])
    def test_actual_message_byte_limit_is_enforced(self, boundary: str) -> None:
        if boundary == "decode":
            with pytest.raises(ValueError, match="Expected bounded binary message"):
                unpack(b"x" * (MAX_MESSAGE_BYTES + 1))
        elif boundary == "encode":
            with pytest.raises(ValueError, match="Message exceeds transport limit"):
                pack({"data": b"x" * MAX_MESSAGE_BYTES})
        else:
            with pytest.raises(ValueError, match="Array exceeds wire limits"):
                pack({"array": np.zeros(MAX_MESSAGE_BYTES + 1, dtype=np.uint8)})

    @pytest.mark.parametrize("kind", ["string", "array", "map"])
    def test_nested_containers_have_independent_size_limits(self, kind: str) -> None:
        values = {"string": "x" * (1024 * 1024 + 1), "array": [0] * 100001, "map": {str(i): 0 for i in range(10001)}}
        encoded = msgpack.packb({"value": values[kind]}, use_bin_type=True)
        assert len(encoded) < MAX_MESSAGE_BYTES

        with pytest.raises(ValueError):
            unpack(encoded)


class TestPolicySession:
    def test_hello_has_stable_per_session_identity_and_finite_provenance(
        self, config: EvaluationConfig, session: PolicySession, backend: _FiniteBackend
    ) -> None:
        hello = session.hello()
        another = PolicySession(config, backend)

        assert hello == session.hello()
        assert hello["contract"] == config.metadata()
        assert hello["producer"] == backend.provenance
        assert hello["session_id"] == session.session_id != another.session_id
        assert backend.calls == []

    def test_hello_rejects_nonfinite_producer_identity(self, session: PolicySession, backend: _FiniteBackend) -> None:
        backend.provenance["invalid"] = float("nan")

        with pytest.raises(ValueError):
            session.hello()

    def test_backend_receives_only_measured_inputs_and_configured_task_context(
        self, session: PolicySession, backend: _FiniteBackend
    ) -> None:
        request = _request(session)

        response = session.infer(request)

        assert len(backend.calls) == 1
        call = backend.calls[0]
        assert set(call) == {"state", "images", "instruction", "seed", "reset"}
        np.testing.assert_array_equal(call["state"], [10, 20, 30])
        assert call["instruction"] == session.config.instruction
        assert call["seed"] == 11 and call["reset"] is True
        assert set(call["images"]) == {"front", "wrist"}
        for key in call["images"]:
            np.testing.assert_array_equal(call["images"][key], request["images"][key])
        assert set(response) == {
            "session_id",
            "request_id",
            "seed",
            "config_sha256",
            "actions",
            "model_actions",
            "inference_ms",
        }
        np.testing.assert_array_equal(
            response["actions"], [[17, -0.75, 30.875], [23, -3.75, 32.375], [29, -6.75, 33.875]]
        )
        np.testing.assert_array_equal(response["model_actions"], backend.actions)
        assert response["actions"].shape[0] == 3 > session.config.execution_horizon
        assert response["request_id"] == 0 and response["session_id"] == session.session_id
        assert np.isfinite(response["inference_ms"]) and response["inference_ms"] >= 0

    def test_backend_mutation_cannot_change_raw_delta_anchor_or_caller_observation(
        self, session: PolicySession, backend: _FiniteBackend
    ) -> None:
        request = _request(session)
        original_images = {key: value.copy() for key, value in request["images"].items()}
        backend.mutate_inputs = True

        response = session.infer(request)

        np.testing.assert_array_equal(request["state"], [10, 20, 30])
        np.testing.assert_array_equal(
            response["actions"], [[17, -0.75, 30.875], [23, -3.75, 32.375], [29, -6.75, 33.875]]
        )
        for key, image in original_images.items():
            np.testing.assert_array_equal(request["images"][key], image)

    def test_seed_order_reset_and_request_sequence_continue_across_episodes(
        self, session: PolicySession, backend: _FiniteBackend
    ) -> None:
        sequence = [(11, True), (11, False), (23, True), (23, False)]

        for request_id, (seed, reset) in enumerate(sequence):
            response = session.infer(_request(session, request_id=request_id, seed=seed, reset=reset))
            assert response["request_id"] == request_id and response["seed"] == seed

        assert [(call["seed"], call["reset"]) for call in backend.calls] == sequence
        assert session.next_request == 4 and session.episode_requests == 2
        with pytest.raises(ValueError, match="Episode must begin with a reset"):
            session.infer(_request(session, request_id=4, seed=23))
        assert session.closed and len(backend.calls) == 4

    def test_episode_request_budget_uses_ceiling_and_resets_only_for_next_seed(
        self, session: PolicySession, backend: _FiniteBackend
    ) -> None:
        session.infer(_request(session))
        session.infer(_request(session, request_id=1, reset=False))

        with pytest.raises(ValueError, match="Episode inference budget exceeded"):
            session.infer(_request(session, request_id=2, reset=False))

        assert session.closed and len(backend.calls) == 2
        with pytest.raises(ValueError, match="session is closed"):
            session.infer(_request(session, request_id=2, seed=23))
        assert len(backend.calls) == 2

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("session_id", "different-session", "identity differs"),
            ("config_sha256", "b" * 64, "identity differs"),
            ("request_id", False, "Duplicate or out-of-order"),
            ("request_id", 0.0, "Duplicate or out-of-order"),
            ("request_id", 1, "Duplicate or out-of-order"),
            ("request_id", -1, "Duplicate or out-of-order"),
            ("seed", True, "Invalid seed/reset types"),
            ("seed", 11.0, "Invalid seed/reset types"),
            ("seed", 23, "Seed differs from declared order"),
            ("reset", 1, "Invalid seed/reset types"),
            ("reset", 0, "Invalid seed/reset types"),
            ("reset", "true", "Invalid seed/reset types"),
            ("reset", None, "Invalid seed/reset types"),
            ("reset", False, "Episode must begin with a reset"),
        ],
    )
    def test_bad_identity_sequence_and_boolean_types_fail_closed_before_backend(
        self, session: PolicySession, backend: _FiniteBackend, field: str, value: Any, message: str
    ) -> None:
        request = _request(session)
        request[field] = value

        with pytest.raises(ValueError, match=message):
            session.infer(request)

        assert session.closed and backend.calls == []
        with pytest.raises(ValueError, match="session is closed"):
            session.infer(_request(session))
        assert backend.calls == []

    @pytest.mark.parametrize("request_id", [0, 2])
    def test_duplicate_and_skipped_requests_never_repeat_inference(
        self, session: PolicySession, backend: _FiniteBackend, request_id: int
    ) -> None:
        session.infer(_request(session))

        with pytest.raises(ValueError, match="Duplicate or out-of-order"):
            session.infer(_request(session, request_id=request_id, reset=False))
        assert session.closed and len(backend.calls) == 1

    @pytest.mark.parametrize("seed", [11, 23])
    def test_seed_cannot_change_without_reset_or_repeat_after_reset(
        self, session: PolicySession, backend: _FiniteBackend, seed: int
    ) -> None:
        session.infer(_request(session))

        with pytest.raises(ValueError, match="Seed differs from declared order"):
            session.infer(_request(session, request_id=1, seed=seed, reset=seed == 11))
        assert session.closed and len(backend.calls) == 1

    @pytest.mark.parametrize(
        "field",
        [
            "oracle_state",
            "object_pose",
            "goal_pose",
            "expert_action",
            "reward",
            "success",
            "instruction",
            "task_context",
        ],
    )
    def test_oracle_and_caller_supplied_task_fields_never_reach_backend(
        self, session: PolicySession, backend: _FiniteBackend, field: str
    ) -> None:
        request = _request(session)
        request[field] = {"privileged": [100, 200, 300]}

        with pytest.raises(ValueError, match="Unexpected policy request fields"):
            session.infer(request)
        assert session.closed and backend.calls == []

    @pytest.mark.parametrize("field", ["session_id", "request_id", "config_sha256", "seed", "reset", "state", "images"])
    def test_missing_request_fields_fail_closed(
        self, session: PolicySession, backend: _FiniteBackend, field: str
    ) -> None:
        request = _request(session)
        del request[field]

        with pytest.raises(ValueError, match="Unexpected policy request fields"):
            session.infer(request)
        assert session.closed and backend.calls == []

    @pytest.mark.parametrize(
        "case", ["extra_state", "nonfinite_state", "boolean_state", "extra_camera", "float_camera"]
    )
    def test_invalid_observations_do_not_reach_backend(
        self, session: PolicySession, backend: _FiniteBackend, case: str
    ) -> None:
        request = _request(session)
        if case == "extra_state":
            request["state"] = np.zeros(4)
        elif case == "nonfinite_state":
            request["state"][0] = np.nan
        elif case == "boolean_state":
            request["state"] = np.ones(3, dtype=bool)
        elif case == "extra_camera":
            request["images"]["oracle_depth"] = request["images"]["front"]
        else:
            request["images"]["front"] = request["images"]["front"].astype(np.float32)

        with pytest.raises(ValueError):
            session.infer(request)
        assert session.closed and backend.calls == []

    @pytest.mark.parametrize(
        "actions",
        [
            np.zeros((2, 3)),
            np.zeros((3, 2)),
            np.ones((3, 3), dtype=bool),
            np.full((3, 3), np.nan),
            np.full((3, 3), np.inf),
        ],
    )
    def test_invalid_backend_horizon_channels_and_numbers_fail_closed(
        self, session: PolicySession, backend: _FiniteBackend, actions: np.ndarray
    ) -> None:
        backend.actions = actions

        with pytest.raises(ValueError):
            session.infer(_request(session))
        assert session.closed and session.next_request == 0 and len(backend.calls) == 1

    def test_backend_exception_propagates_without_retry_or_fallback(
        self, session: PolicySession, backend: _FiniteBackend
    ) -> None:
        error = RuntimeError("synthetic external backend failed")
        backend.error = error

        with pytest.raises(RuntimeError, match="synthetic external backend failed") as caught:
            session.infer(_request(session))

        assert caught.value is error
        assert session.closed and len(backend.calls) == 1
        with pytest.raises(ValueError, match="session is closed"):
            session.infer(_request(session))
        assert len(backend.calls) == 1


class TestPolicyClientLoopback:
    def test_public_client_and_session_exchange_real_wire_messages(
        self, config: EvaluationConfig, session: PolicySession, backend: _FiniteBackend, wire_server: _ServerFactory
    ) -> None:
        requests = []

        def handler(connection: ServerConnection) -> None:
            connection.send(pack(session.hello()))
            for _ in range(3):
                request = unpack(connection.recv(timeout=_WIRE_TIMEOUT))
                requests.append(request)
                connection.send(pack(session.infer(request)))

        request = _request(session)
        with wire_server(handler) as port:
            client = PolicyClient(config, port=port)
            try:
                assert client.metadata == session.hello()
                for request_id, (seed, reset) in enumerate([(11, True), (11, False), (23, True)]):
                    response = client.infer(request["state"], request["images"], seed=seed, reset=reset)
                    assert response["request_id"] == request_id and response["seed"] == seed
                    np.testing.assert_array_equal(
                        response["actions"], [[17, -0.75, 30.875], [23, -3.75, 32.375], [29, -6.75, 33.875]]
                    )
                    np.testing.assert_array_equal(response["model_actions"], backend.actions)
                assert client.request_id == 3
            finally:
                client.close()
            assert client.connection is None

        assert len(requests) == len(backend.calls) == 3
        assert all(
            set(item) == {"session_id", "request_id", "config_sha256", "seed", "reset", "state", "images"}
            for item in requests
        )
        assert [item["reset"] for item in requests] == [True, False, True]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("config_sha256", "b" * 64),
            ("checkpoint_sha256", "b" * 64),
            ("control_hz", 31),
            ("channels", ["gripper", "shoulder", "wrist"]),
            ("action_horizon", 2),
            ("action_horizon", 3.0),
            ("execution_horizon", 1),
            ("image_shapes", {"front": [4, 4, 3]}),
            ("units", "degrees"),
            ("backend", "rho"),
            ("episodes", [{"split": "test", "seed": 23}, {"split": "validation", "seed": 11}]),
        ],
    )
    def test_wrong_contract_handshake_is_rejected_and_connection_closed(
        self,
        config: EvaluationConfig,
        session: PolicySession,
        backend: _FiniteBackend,
        wire_server: _ServerFactory,
        field: str,
        value: Any,
    ) -> None:
        hello = session.hello()
        hello["contract"][field] = value
        disconnected = Event()

        def handler(connection: ServerConnection) -> None:
            connection.send(pack(hello))
            with pytest.raises(ConnectionClosed):
                connection.recv(timeout=_WIRE_TIMEOUT)
            disconnected.set()

        with wire_server(handler) as port:
            with pytest.raises(ValueError, match="Policy handshake contract differs"):
                PolicyClient(config, port=port)
            assert disconnected.wait(_WIRE_TIMEOUT)
        assert backend.calls == []

    @pytest.mark.parametrize("case", ["missing_producer", "extra_field", "empty_session", "nonfinite_producer"])
    def test_malformed_handshake_is_rejected_on_real_wire(
        self, config: EvaluationConfig, session: PolicySession, wire_server: _ServerFactory, case: str
    ) -> None:
        hello = session.hello()
        if case == "missing_producer":
            del hello["producer"]
        elif case == "extra_field":
            hello["oracle"] = True
        elif case == "empty_session":
            hello["session_id"] = ""
        else:
            hello["producer"] = {"invalid": float("nan")}

        def handler(connection: ServerConnection) -> None:
            connection.send(pack(hello))

        with wire_server(handler) as port, pytest.raises(ValueError):
            PolicyClient(config, port=port)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("session_id", "another-session"),
            ("config_sha256", "b" * 64),
            ("request_id", 1),
            ("request_id", False),
            ("request_id", 0.0),
            ("seed", 23),
            ("seed", 11.0),
            ("seed", True),
        ],
    )
    def test_stale_or_type_aliased_response_identity_fails_closed(
        self,
        config: EvaluationConfig,
        session: PolicySession,
        backend: _FiniteBackend,
        wire_server: _ServerFactory,
        field: str,
        value: Any,
    ) -> None:
        def handler(connection: ServerConnection) -> None:
            connection.send(pack(session.hello()))
            response = session.infer(unpack(connection.recv(timeout=_WIRE_TIMEOUT)))
            response[field] = value
            connection.send(pack(response))

        request = _request(session)
        with wire_server(handler) as port:
            client = PolicyClient(config, port=port)
            try:
                with pytest.raises(ValueError, match=f"Stale or mismatched policy response: {field}"):
                    client.infer(request["state"], request["images"], seed=11, reset=True)
                assert client.connection is None and client.request_id == 0
                with pytest.raises(ValueError, match="Policy connection is closed"):
                    client.infer(request["state"], request["images"], seed=11, reset=True)
            finally:
                client.close()
        assert len(backend.calls) == 1

    def test_duplicate_action_response_cannot_satisfy_the_next_request(
        self, config: EvaluationConfig, session: PolicySession, backend: _FiniteBackend, wire_server: _ServerFactory
    ) -> None:
        received = []

        def handler(connection: ServerConnection) -> None:
            connection.send(pack(session.hello()))
            first = unpack(connection.recv(timeout=_WIRE_TIMEOUT))
            received.append(first)
            response = pack(session.infer(first))
            connection.send(response)
            received.append(unpack(connection.recv(timeout=_WIRE_TIMEOUT)))
            connection.send(response)

        request = _request(session)
        with wire_server(handler) as port:
            client = PolicyClient(config, port=port)
            try:
                client.infer(request["state"], request["images"], seed=11, reset=True)
                with pytest.raises(ValueError, match="Stale or mismatched policy response: request_id"):
                    client.infer(request["state"], request["images"], seed=11, reset=False)
                assert client.connection is None and client.request_id == 1
            finally:
                client.close()

        assert [value["request_id"] for value in received] == [0, 1]
        assert len(backend.calls) == 1

    @pytest.mark.parametrize("case", ["actions", "model_actions", "double_delta"])
    def test_action_mapping_mismatch_is_detected_from_real_response_bytes(
        self, config: EvaluationConfig, session: PolicySession, wire_server: _ServerFactory, case: str
    ) -> None:
        def handler(connection: ServerConnection) -> None:
            connection.send(pack(session.hello()))
            request = unpack(connection.recv(timeout=_WIRE_TIMEOUT))
            response = session.infer(request)
            if case == "double_delta":
                response["actions"][:, [0, 2]] += request["state"][[0, 2]]
            else:
                response[case] += 0.25
            connection.send(pack(response))

        request = _request(session)
        with wire_server(handler) as port:
            client = PolicyClient(config, port=port)
            try:
                with pytest.raises(ValueError, match="Policy action mapping differs"):
                    client.infer(request["state"], request["images"], seed=11, reset=True)
                assert client.connection is None and client.request_id == 0
            finally:
                client.close()

    @pytest.mark.parametrize(
        "case", ["missing", "extra", "horizon", "nonfinite_actions", "negative_time", "nan_time", "boolean_time"]
    )
    def test_response_schema_horizon_and_finite_timing_are_enforced(
        self, config: EvaluationConfig, session: PolicySession, wire_server: _ServerFactory, case: str
    ) -> None:
        def handler(connection: ServerConnection) -> None:
            connection.send(pack(session.hello()))
            response = session.infer(unpack(connection.recv(timeout=_WIRE_TIMEOUT)))
            if case == "missing":
                del response["model_actions"]
            elif case == "extra":
                response["oracle"] = True
            elif case == "horizon":
                response["actions"] = response["actions"][:2]
            elif case == "nonfinite_actions":
                response["actions"][0, 0] = np.nan
            else:
                response["inference_ms"] = {"negative_time": -1, "nan_time": float("nan"), "boolean_time": True}[case]
            connection.send(pack(response))

        request = _request(session)
        with wire_server(handler) as port:
            client = PolicyClient(config, port=port)
            try:
                with pytest.raises(ValueError):
                    client.infer(request["state"], request["images"], seed=11, reset=True)
                assert client.connection is None
            finally:
                client.close()

    def test_invalid_local_observation_closes_client_without_sending_inference(
        self, config: EvaluationConfig, session: PolicySession, backend: _FiniteBackend, wire_server: _ServerFactory
    ) -> None:
        def handler(connection: ServerConnection) -> None:
            connection.send(pack(session.hello()))
            with pytest.raises(ConnectionClosed):
                connection.recv(timeout=_WIRE_TIMEOUT)

        request = _request(session)
        with wire_server(handler) as port:
            client = PolicyClient(config, port=port)
            try:
                with pytest.raises(ValueError, match="Measured state"):
                    client.infer(np.zeros(4), request["images"], seed=11, reset=True)
                assert client.connection is None and client.request_id == 0
            finally:
                client.close()
        assert backend.calls == []

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.1"])
    def test_nonloopback_hosts_are_rejected_before_connecting(self, config: EvaluationConfig, host: str) -> None:
        with pytest.raises(ValueError, match="loopback-only"):
            PolicyClient(config, host=host)


class _FailingConnection:
    """An external connection boundary that raises deterministically, without waiting."""

    hello: bytes | None
    error: Exception
    sent: list[bytes]
    timeouts: list[float | None]
    close_count: int

    def __init__(self, hello: bytes | None, error: Exception) -> None:
        self.hello = hello
        self.error = error
        self.sent = []
        self.timeouts = []
        self.close_count = 0

    def recv(self, timeout: float | None = None) -> bytes:
        self.timeouts.append(timeout)
        if self.hello is not None:
            hello, self.hello = self.hello, None
            return hello
        raise self.error

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.close_count += 1


class TestPolicyClientExternalFailures:
    @pytest.mark.parametrize("phase", ["handshake", "inference"])
    @pytest.mark.parametrize("failure", ["timeout", "disconnect"])
    def test_timeout_and_disconnect_close_without_retry(
        self,
        config: EvaluationConfig,
        session: PolicySession,
        monkeypatch: pytest.MonkeyPatch,
        phase: str,
        failure: str,
    ) -> None:
        error = TimeoutError("synthetic timeout") if failure == "timeout" else ConnectionClosedError(None, None)
        connection = _FailingConnection(pack(session.hello()) if phase == "inference" else None, error)
        connects = []

        def connect(uri: str, **kwargs: Any) -> _FailingConnection:
            connects.append({"uri": uri, "options": kwargs})
            return connection

        monkeypatch.setattr(websockets.sync.client, "connect", connect)

        if phase == "handshake":
            with pytest.raises(type(error)) as caught:
                PolicyClient(config)
        else:
            client = PolicyClient(config)
            request = _request(session)
            with pytest.raises(type(error)) as caught:
                client.infer(request["state"], request["images"], seed=11, reset=True)
            assert client.connection is None and client.request_id == 0
            client.close()
            assert len(connection.sent) == 1
            assert unpack(connection.sent[0])["request_id"] == 0

        assert caught.value is error
        assert connection.close_count == 1
        assert len(connects) == 1
        assert connects[0] == {
            "uri": "ws://127.0.0.1:8000",
            "options": {
                "compression": None,
                "max_size": MAX_MESSAGE_BYTES,
                "open_timeout": config.timeout_seconds,
                "close_timeout": 2,
                "max_queue": 1,
                "proxy": None,
            },
        }
        assert connection.timeouts == [config.timeout_seconds] * (2 if phase == "inference" else 1)
