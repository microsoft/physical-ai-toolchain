from __future__ import annotations

import contextlib
import enum
import secrets
import socket
import subprocess
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any

import pytest

from remoter import autoremote, class2dict, msgsock, remoter, rmtclass
from remoter.msgtcp import MessengerTCP
from remoter.safe_codec import (
    AdapterContext,
    AdapterRegistry,
    CodecError,
    CodecLimits,
    CodecLimitsError,
    CodecTypeError,
    TypeAdapter,
    dumps,
    loads,
)


class _WireClassDictionary:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state


def _encode_wire_payload(envelope: Any) -> bytes:
    registry = AdapterRegistry()
    registry.register(
        TypeAdapter(
            ext_code=18,
            py_type=_WireClassDictionary,
            encode=lambda obj, _ctx: obj.state,
            decode=lambda state, _ctx: state,
        )
    )
    return dumps(envelope, registry, limits=CodecLimits(), context=AdapterContext(role="test"))


@pytest.fixture
def class_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("_NAME_TO_TYPE", "_TYPE_TO_NAME", "_TYPE_TO_DECODER"):
        monkeypatch.setattr(class2dict, name, getattr(class2dict, name).copy())


class _FakeDtype:
    itemsize = 4


class _FakeTensor:
    def __init__(self) -> None:
        self.device: str | None = None
        self.shape: list[int] | None = None

    def reshape(self, shape: list[int]) -> _FakeTensor:
        self.shape = shape
        return self

    def to(self, device: str) -> _FakeTensor:
        self.device = device
        return self

    def requires_grad_(self, _requires_grad: bool) -> _FakeTensor:
        return self


class _FakeTorch:
    Tensor = _FakeTensor
    dtype = _FakeDtype
    float32 = _FakeDtype()
    allocation_count = 0

    @classmethod
    def frombuffer(cls, _data: bytearray, *, dtype: _FakeDtype) -> _FakeTensor:
        assert dtype is cls.float32
        cls.allocation_count += 1
        return _FakeTensor()

    @classmethod
    def empty(cls, shape: list[int], *, dtype: _FakeDtype) -> _FakeTensor:
        assert dtype is cls.float32
        cls.allocation_count += 1
        return _FakeTensor().reshape(shape)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"allowedmodules": ["valid.module", 7]}, r"allowedmodules\[1\]"),
        ({"remotefuncs": [{7: {}}]}, r"remotefuncs\[0\] target"),
        ({"remoteclasses": "not-a-list"}, "remoteclasses must be a list"),
        ({"stubs": {"stub.module/Target": 7}}, "stub target"),
    ],
)
def test_configured_modules_reject_non_string_targets_before_installing_permissions(
    monkeypatch: pytest.MonkeyPatch,
    config: dict[str, object],
    message: str,
) -> None:
    allowed_modules = {"remoter.remoter", "remoter.rmtclass"}
    monkeypatch.setattr(remoter, "allowed_modules", allowed_modules)

    with pytest.raises(ValueError, match=message):
        autoremote.allow_configured_modules(config)

    assert remoter.allowed_modules == allowed_modules


def _tensor_wire_payload(*, data: bytes, shape: list[int]) -> dict[str, object]:
    return {
        class2dict.TYPE_KEY: "torch:Tensor",
        class2dict.TENSOR_KEY: {
            "data": data,
            "device": "cpu",
            "dtype": "float32",
            "requires_grad": False,
            "shape": shape,
        },
    }


def test_safe_builtins_round_trip() -> None:
    registry = AdapterRegistry()
    payload = {
        "none": None,
        "bool": True,
        "int": 7,
        "float": 2.5,
        "str": "hello",
        "bytes": b"abc",
        "list": [1, "x", False],
        "tuple": ("a", 1),
        "dict": {"k": "v"},
        "uuid": uuid.uuid4(),
    }
    encoded = dumps(payload, registry, limits=CodecLimits(), context=AdapterContext(role="test"))
    decoded = loads(encoded, registry, limits=CodecLimits(), context=AdapterContext(role="test"))
    assert decoded == payload


def test_arbitrary_precision_integers_round_trip() -> None:
    registry = AdapterRegistry()
    payload = [2**64, -(2**63) - 1, 2**1024, -(2**1024)]

    encoded = dumps(payload, registry, limits=CodecLimits(), context=AdapterContext(role="test"))
    decoded = loads(encoded, registry, limits=CodecLimits(), context=AdapterContext(role="test"))

    assert decoded == payload


def test_class2dict_preserves_uuid_for_safe_codec() -> None:
    value = uuid.uuid4()

    converted = class2dict.to_dict({"id": value})
    encoded = remoter.serialize_payload(converted)

    assert converted["id"] is value
    assert remoter.deserialize_payload(encoded) == {"id": value}


def test_class2dict_reconstruction_does_not_call_constructor() -> None:
    class ConstructorHasSideEffects:
        def __init__(self) -> None:
            raise AssertionError("constructor must not run during deserialization")

    original = object.__new__(ConstructorHasSideEffects)
    original.value = 7
    converted = class2dict.to_dict(original)

    decoded = class2dict.from_dict(converted, cls=ConstructorHasSideEffects)

    assert isinstance(decoded, ConstructorHasSideEffects)
    assert decoded.value == 7


@pytest.mark.parametrize("authorized", [False, True])
@pytest.mark.parametrize("framed", [False, True])
def test_rpc_class_dictionary_never_constructs_loaded_unregistered_type(
    monkeypatch: pytest.MonkeyPatch, authorized: bool, framed: bool
) -> None:
    constructor_calls = []

    class ProcessSentinel:
        def __init__(self, value: Any) -> None:
            constructor_calls.append(value)

    monkeypatch.setattr(subprocess, "Popen", ProcessSentinel)
    monkeypatch.setattr(
        remoter, "allowed_functions", frozenset({"declared.module//run"}) if authorized else frozenset()
    )
    state = _WireClassDictionary({"__type__": "subprocess:Popen", "__value__": ["harmless-sentinel"]})
    args = (state, b"x" * 65_536) if framed else (state,)
    payload = _encode_wire_payload(("direct", "declared.module//run", "declared.module", "run", "", args, {}))

    func, _, _, _, error = remoter.decode_function_call(payload, None, {}, None)

    assert func is None
    assert isinstance(error, CodecError if authorized else remoter.CallableNotAllowedError)
    if authorized:
        assert "unregistered class2dict type" in str(error)
    assert constructor_calls == []


def test_encrypted_rpc_rejects_class_dictionary_before_adapter_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    decoder_calls = []
    results = []
    completed = threading.Event()
    key = secrets.token_bytes(32)
    monkeypatch.setattr(msgsock, "msgkey", key)
    monkeypatch.setattr(msgsock, "initheartbeat", True)
    monkeypatch.setattr(remoter, "allowed_functions", frozenset())
    monkeypatch.setattr(class2dict, "from_dict", lambda *args, **_kwargs: decoder_calls.append(args))
    runtime = remoter.Remoter.createemptyinstance()
    runtime.remotedClasses = {}

    def callback(fnid: uuid.UUID, result: Any, error: Exception | None, funcargs: dict) -> None:
        results.append((fnid, runtime.encode_result(funcargs, result, error, None)))
        completed.set()

    def handle(data: bytes, _messenger: msgsock.Messenger, _endpoint: str) -> None:
        runtime.execfunction(data, callback, None)

    state = _WireClassDictionary({"__type__": "subprocess:Popen", "__value__": ["harmless-sentinel"]})
    payload = _encode_wire_payload(
        ("direct", "unapproved.module//execute", "unapproved.module", "execute", "", (state,), {})
    )
    fnid = uuid.uuid4()
    request = bytes((remoter.MessageType.FunctionCall, 0)) + fnid.bytes + payload
    client_sock, server_sock = socket.socketpair()
    with client_sock, server_sock:
        messenger = MessengerTCP(server_sock, "tcp://authenticated-peer:1", handlefn=handle)
        thread = threading.Thread(target=messenger.recvthread)
        thread.start()
        try:
            frame = messenger.data + msgsock.encryptMessage(request, key)
            client_sock.sendall(len(frame).to_bytes(4, "big") + frame)
            assert completed.wait(timeout=2)
        finally:
            client_sock.shutdown(socket.SHUT_RDWR)
            thread.join(timeout=2)
            messenger.close()
        assert not thread.is_alive()

    assert decoder_calls == []
    assert len(results) == 1
    returned_fnid, response = results[0]
    assert returned_fnid == fnid
    _, result, error = runtime.decode_result(response, "direct", None)
    assert result is None
    assert isinstance(error, remoter.RemoteExecutionError)
    assert "CallableNotAllowedError" in str(error)


@pytest.mark.parametrize("framed", [False, True])
@pytest.mark.parametrize(
    "invalid",
    ["undeclared", "mismatched", "identity", "metadata", "envelope", "argument-shape", "keyword-key"],
)
def test_rejected_rpc_never_runs_nested_argument_or_metadata_adapters(
    monkeypatch: pytest.MonkeyPatch, invalid: str, framed: bool
) -> None:
    decoder_calls = []
    monkeypatch.setattr(class2dict, "from_dict", lambda *args, **_kwargs: decoder_calls.append(args))
    monkeypatch.setattr(remoter, "allowed_functions", frozenset({"declared.module//run"}))
    state = _WireClassDictionary({"__type__": "subprocess:Popen", "__value__": ["harmless-sentinel"]})
    blob = b"x" * 65_536 if framed else b""
    envelope = ["direct", "declared.module//run", "declared.module", "run", "", ([{state: state}, blob],), {}]
    if invalid == "undeclared":
        envelope[1:4] = ["unapproved.module//execute", "unapproved.module", "execute"]
    elif invalid == "mismatched":
        envelope[2] = "unapproved.module"
    elif invalid == "identity":
        envelope[2] = "invalid/module"
    elif invalid == "metadata":
        envelope[0] = state
    elif invalid == "envelope":
        envelope.append(state)
    elif invalid == "argument-shape":
        envelope[5] = state
    elif invalid == "keyword-key":
        envelope[6] = {state: state}

    func, _, args, kwargs, error = remoter.decode_function_call(_encode_wire_payload(tuple(envelope)), None, {}, None)

    assert func is None
    assert args == ()
    assert kwargs == {}
    assert isinstance(error, CodecError)
    assert decoder_calls == []


def test_authorized_rpc_decodes_registered_state_before_rehydration(
    monkeypatch: pytest.MonkeyPatch, class_registry: None
) -> None:
    class State:
        def __init__(self) -> None:
            raise AssertionError("state constructor must never run")

    events = []
    expected_function = object()

    def decode_state(payload: dict[str, Any]) -> State:
        events.append("decode")
        state = object.__new__(State)
        state.value = payload["value"]
        return state

    def rehydrate_args(args: tuple, kwargs: dict, *_rest: Any) -> tuple[tuple, dict]:
        events.append("rehydrate")
        return args, kwargs

    remoter.register_class2dict_type(State, wire_name="test:State", decode_state=decode_state)
    monkeypatch.setattr(remoter, "allowed_functions", frozenset({"declared.module//run"}))
    monkeypatch.setattr(remoter, "rehydrate_args", rehydrate_args)
    monkeypatch.setattr(remoter, "getfuncobjfromname", lambda *_args: expected_function)
    state = _WireClassDictionary({"__type__": "test:State", "value": 7})
    envelope = ("direct", "declared.module//run", "declared.module", "run", "", ([state],), {"state": state})

    func, _, args, kwargs, error = remoter.decode_function_call(_encode_wire_payload(envelope), None, {}, None)

    assert func is expected_function
    assert error is None
    assert isinstance(args[0][0], State)
    assert args[0][0].value == 7
    assert isinstance(kwargs["state"], State)
    assert events == ["decode", "decode", "rehydrate"]


def test_registered_class_value_requires_explicit_decoder(class_registry: None) -> None:
    class ConstructorSentinel:
        def __init__(self, _value: Any) -> None:
            raise AssertionError("constructor must never run")

    remoter.register_class2dict_type(ConstructorSentinel, wire_name="test:ConstructorSentinel")
    with pytest.raises(TypeError, match="requires an explicit state decoder"):
        class2dict.from_dict({"__type__": "test:ConstructorSentinel", "__value__": "value"})


def test_registered_state_rejects_descriptor_and_unknown_fields(class_registry: None) -> None:
    @dataclass
    class State:
        value: int

        @property
        def side_effect(self) -> int:
            return 0

        @side_effect.setter
        def side_effect(self, _value: int) -> None:
            raise AssertionError("descriptor must never run")

    remoter.register_class2dict_type(State, wire_name="test:State")
    decoded = class2dict.from_dict({"__type__": "test:State", "value": 7})
    assert decoded == State(7)
    with pytest.raises(TypeError, match="unknown dataclass fields"):
        class2dict.from_dict({"__type__": "test:State", "side_effect": 7})
    with pytest.raises(TypeError, match="double-underscore"):
        class2dict.from_dict({"__type__": "test:State", "__class__": "changed"})

    class DescriptorState:
        side_effect = State.side_effect

    remoter.register_class2dict_type(DescriptorState, wire_name="test:DescriptorState")
    with pytest.raises(TypeError, match="custom descriptor"):
        class2dict.from_dict({"__type__": "test:DescriptorState", "side_effect": 7})


def test_registered_slotted_state_round_trip(class_registry: None) -> None:
    @dataclass(slots=True)
    class State:
        value: int

    remoter.register_class2dict_type(State)
    original = State(7)
    assert remoter.deserialize_payload(remoter.serialize_payload(original)) == original


def test_builtin_values_and_registered_enum_round_trip(class_registry: None) -> None:
    class Choice(enum.Enum):
        A = "a"

        @classmethod
        def _missing_(cls, _value: Any) -> None:
            raise AssertionError("enum missing hook must never run")

    remoter.register_class2dict_type(Choice, wire_name="test:Choice")
    values = [date(2026, 1, 1), datetime(2026, 1, 1), PurePosixPath("model"), {1, 2}, Choice.A]
    assert remoter.deserialize_payload(remoter.serialize_payload(values)) == values
    with pytest.raises(TypeError, match="Unknown value"):
        class2dict.from_dict({"__type__": "test:Choice", "__value__": "unknown"})


@pytest.mark.parametrize("metadata", [None, {"key": "unit/test"}, {"key": "unit/test", "loc": 7}])
def test_invalid_result_metadata_precedes_adapter_decoding(monkeypatch: pytest.MonkeyPatch, metadata: Any) -> None:
    decoder_calls = []
    monkeypatch.setattr(class2dict, "from_dict", lambda *args, **_kwargs: decoder_calls.append(args))
    state = _WireClassDictionary({"__type__": "subprocess:Popen", "__value__": ["harmless-sentinel"]})
    runtime = remoter.Remoter.createemptyinstance()

    with pytest.raises(CodecError):
        runtime.decode_result(_encode_wire_payload((metadata, state, None)), "direct", None)

    assert decoder_calls == []


def test_result_rejects_loaded_unregistered_class(monkeypatch: pytest.MonkeyPatch) -> None:
    class ProcessSentinel:
        def __init__(self, _value: Any) -> None:
            raise AssertionError("constructor must never run")

    monkeypatch.setattr(subprocess, "Popen", ProcessSentinel)
    state = _WireClassDictionary({"__type__": "subprocess:Popen", "__value__": ["harmless-sentinel"]})
    runtime = remoter.Remoter.createemptyinstance()
    with pytest.raises(TypeError, match="unregistered class2dict type"):
        runtime.decode_result(
            _encode_wire_payload(({"key": "unit/test", "loc": "direct"}, state, None)), "direct", None
        )


def test_class2dict_reconstructs_scalar_tensor_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(class2dict, "_get_torch", lambda: _FakeTorch)
    _FakeTorch.allocation_count = 0

    decoded = class2dict.from_dict(
        _tensor_wire_payload(data=b"1234", shape=[]),
        cls=_FakeTensor,
        limits=CodecLimits(max_bytes_length=4),
    )

    assert isinstance(decoded, _FakeTensor)
    assert decoded.shape == []
    assert decoded.device == "cpu"
    assert _FakeTorch.allocation_count == 1


def test_class2dict_rejects_tensor_exceeding_codec_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(class2dict, "_get_torch", lambda: _FakeTorch)
    _FakeTorch.allocation_count = 0

    with pytest.raises(CodecLimitsError, match="tensor bytes exceed max_bytes_length=8"):
        class2dict.from_dict(
            _tensor_wire_payload(data=b"", shape=[3]),
            cls=_FakeTensor,
            limits=CodecLimits(max_bytes_length=8),
        )

    assert _FakeTorch.allocation_count == 0


def test_unknown_type_fails_without_adapter_and_no_reduce_exec() -> None:
    class Evil:
        def __init__(self) -> None:
            self.reduce_called = False

        def __reduce__(self):  # pragma: no cover - must never be invoked
            self.reduce_called = True
            raise AssertionError("__reduce__ should never run")

    registry = AdapterRegistry()
    obj = Evil()

    with pytest.raises(CodecTypeError):
        dumps(obj, registry, limits=CodecLimits(), context=AdapterContext(role="test"))

    assert obj.reduce_called is False


def test_limit_enforcement() -> None:
    registry = AdapterRegistry()
    with pytest.raises(CodecLimitsError):
        dumps(
            "x" * 20,
            registry,
            limits=CodecLimits(max_str_length=5),
            context=AdapterContext(role="test"),
        )


def test_meta_remote_uuid_round_trip() -> None:
    class DummyRemote:
        def __init__(self) -> None:
            self.uuid_rmt0bf = uuid.uuid4()
            self.rmtloc_rmt0bf = "tcp://127.0.0.1:9000"

    meta = remoter.MetaRemotedUUID(DummyRemote())
    encoded = remoter.serialize_payload(meta)
    decoded = remoter.deserialize_payload(encoded)
    assert isinstance(decoded, remoter.MetaRemotedUUID)
    assert decoded.uuid_rmt0bf == meta.uuid_rmt0bf
    assert decoded.rmtloc_rmt0bf == meta.rmtloc_rmt0bf
    assert decoded.name == meta.name


def test_remote_error_descriptor_round_trip() -> None:
    runtime = remoter.Remoter.createemptyinstance()
    runtime.remotedClasses = {}
    funcargs = {"key": "unit/test", "loc": "direct"}

    payload = runtime.encode_result(funcargs, None, ValueError("bad input"), None)
    _, _, ex = runtime.decode_result(payload, "direct", None)

    assert isinstance(ex, remoter.RemoteExecutionError)
    assert "ValueError" in str(ex)
    assert "bad input" in str(ex)


def test_remote_attribute_error_preserves_python_attribute_fallback() -> None:
    runtime = remoter.Remoter.createemptyinstance()
    runtime.remotedClasses = {}
    funcargs = {"key": "unit/objgetattr", "loc": "direct"}

    payload = runtime.encode_result(funcargs, None, AttributeError("value2x"), None)
    _, _, ex = runtime.decode_result(payload, "direct", None)

    assert type(ex) is AttributeError
    assert str(ex) == "value2x"


def test_malformed_function_call_returns_wire_safe_error() -> None:
    runtime = remoter.Remoter.createemptyinstance()
    runtime.remotedClasses = {}
    fnid = uuid.uuid4()
    callback_args: list[tuple[uuid.UUID, object, Exception, dict[str, object]]] = []
    payload = remoter.serialize_payload(("only", "two"))
    message = b"".join(
        (
            int.to_bytes(remoter.MessageType.FunctionCall, 1, "big"),
            int.to_bytes(remoter.FunctionType.toint(remoter.FunctionType.Direct), 1, "big"),
            fnid.bytes,
            payload,
        )
    )

    returned_fnid, classuid, task = runtime.execfunction(
        message,
        lambda *args: callback_args.append(args),
        None,
    )

    assert returned_fnid == fnid
    assert classuid is None
    assert task == {}
    assert len(callback_args) == 1
    _, result, ex, funcargs = callback_args[0]
    assert result is None
    assert isinstance(ex, CodecError)
    assert "seven fields" in str(ex)
    assert funcargs == {"key": "unknown", "loc": "unknown"}

    error_payload = runtime.encode_result(funcargs, None, ex, None)
    decoded_funcargs, decoded_result, decoded_error = runtime.decode_result(error_payload, "direct", None)
    assert decoded_funcargs == funcargs
    assert decoded_result is None
    assert isinstance(decoded_error, remoter.RemoteExecutionError)
    assert "seven fields" in str(decoded_error)


def test_undeclared_function_is_rejected_before_rehydration_or_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remoter, "allowed_functions", frozenset({"declared.module//run"}))
    monkeypatch.setattr(remoter, "callable_policy_frozen", True)
    monkeypatch.setattr(
        remoter,
        "rehydrate_args",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("arguments must not be rehydrated")),
    )
    monkeypatch.setattr(
        remoter,
        "getfuncobjfromname",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("function must not be imported")),
    )
    payload = remoter.serialize_payload(("tcp://server", "os//system", "os", "system", "", (), {}))

    func, funcargs, args, kwargs, error = remoter.decode_function_call(payload, None, {}, None)

    assert func is None
    assert funcargs["key"] == "os//system"
    assert args == ()
    assert kwargs == {}
    assert isinstance(error, remoter.CallableNotAllowedError)
    assert "not allowed" in str(error)


def test_mismatched_callable_identity_is_rejected_before_rehydration_or_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remoter, "allowed_functions", frozenset({"declared.module//run"}))
    monkeypatch.setattr(remoter, "callable_policy_frozen", True)
    monkeypatch.setattr(
        remoter,
        "rehydrate_args",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("arguments must not be rehydrated")),
    )
    monkeypatch.setattr(
        remoter,
        "getfuncobjfromname",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("function must not be imported")),
    )
    payload = remoter.serialize_payload(("tcp://server", "declared.module//run", "os", "system", "", (), {}))

    func, _, args, kwargs, error = remoter.decode_function_call(payload, None, {}, None)

    assert func is None
    assert args == ()
    assert kwargs == {}
    assert isinstance(error, remoter.CallableNotAllowedError)
    assert "does not match decoded identity" in str(error)


def test_declared_function_is_resolved_after_identity_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_function = object()
    events: list[str] = []
    monkeypatch.setattr(remoter, "allowed_functions", frozenset({"declared.module//run"}))
    monkeypatch.setattr(remoter, "callable_policy_frozen", True)

    def rehydrate_args(*_args: object, **_kwargs: object) -> tuple[tuple[str], dict[str, str]]:
        events.append("rehydrate")
        return ("argument",), {"name": "value"}

    def resolve_function(*_args: object, **_kwargs: object) -> object:
        events.append("resolve")
        return expected_function

    monkeypatch.setattr(remoter, "rehydrate_args", rehydrate_args)
    monkeypatch.setattr(remoter, "getfuncobjfromname", resolve_function)
    payload = remoter.serialize_payload(("tcp://server", "declared.module//run", "declared.module", "run", "", (), {}))

    func, _, args, kwargs, error = remoter.decode_function_call(payload, None, {}, None)

    assert func is expected_function
    assert args == ("argument",)
    assert kwargs == {"name": "value"}
    assert error is None
    assert events == ["rehydrate", "resolve"]


def test_callable_policy_is_immutable_after_freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remoter, "allowed_functions", {"declared.module//run"})
    monkeypatch.setattr(remoter, "callable_policy_frozen", False)

    policy = remoter.freeze_callable_policy()

    assert policy == frozenset({"declared.module//run"})
    remoter.allow_function("declared.module//run")
    with pytest.raises(RuntimeError, match="policy is frozen"):
        remoter.allow_function("undeclared.module//run")


def test_allowall_runtime_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="allowall is not supported"):
        remoter.Remoter({}, "127.0.0.1", 0, None, None, 0, True, None)


def test_server_callable_patterns_expand_to_exact_unwrapped_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    class Policy:
        def __init__(self) -> None:
            pass

        def allowed(self) -> None:
            pass

        def blocked(self) -> None:
            pass

        def local_only(self) -> None:
            pass

    Policy.__qualname__ = "Policy"
    for method_name in ("__init__", "allowed", "blocked", "local_only"):
        getattr(Policy, method_name).__qualname__ = f"Policy.{method_name}"

    module_name = Policy.__module__
    class_key = f"{module_name}/Policy"
    original_init = Policy.__init__
    original_allowed = Policy.allowed
    monkeypatch.setattr(
        remoter,
        "allowed_functions",
        {
            f"{module_name}/Policy/blocked",
            f"{module_name}/Policy/local_only",
        },
    )
    monkeypatch.setattr(remoter, "callable_policy_frozen", False)
    monkeypatch.setattr(
        remoter,
        "remoterclassparams",
        {
            class_key: {
                "servercallablemethods": ["__init__", "allow*", "blocked"],
                "serverdeniedmethods": ["block*"],
            }
        },
    )

    rmtclass.allowallfunctions(Policy, isserver=True)

    assert f"{module_name}/Policy/__init__" in remoter.allowed_functions
    assert f"{module_name}/Policy/allowed" in remoter.allowed_functions
    assert f"{module_name}/Policy/blocked" not in remoter.allowed_functions
    assert f"{module_name}/Policy/local_only" not in remoter.allowed_functions
    assert Policy.__init__ is original_init
    assert Policy.allowed is original_allowed


def test_server_callable_pattern_must_match_a_method(monkeypatch: pytest.MonkeyPatch) -> None:
    class Policy:
        def __init__(self) -> None:
            pass

    Policy.__qualname__ = "Policy"
    Policy.__init__.__qualname__ = "Policy.__init__"
    class_key = f"{Policy.__module__}/Policy"
    monkeypatch.setattr(remoter, "allowed_functions", set())
    monkeypatch.setattr(remoter, "callable_policy_frozen", False)
    monkeypatch.setattr(
        remoter,
        "remoterclassparams",
        {class_key: {"servercallablemethods": ["missing_*"]}},
    )

    with pytest.raises(ValueError, match="matches no methods"):
        rmtclass.allowallfunctions(Policy, isserver=True)


def test_legacy_server_remoteability_field_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    class Policy:
        def __init__(self) -> None:
            pass

    Policy.__qualname__ = "Policy"
    Policy.__init__.__qualname__ = "Policy.__init__"
    class_key = f"{Policy.__module__}/Policy"
    monkeypatch.setattr(remoter, "allowed_functions", set())
    monkeypatch.setattr(remoter, "callable_policy_frozen", False)
    monkeypatch.setattr(remoter, "remoterclassparams", {class_key: {"remoteableserver": True}})

    with pytest.raises(ValueError, match="use servercallablemethods"):
        rmtclass.allowallfunctions(Policy, isserver=True)


def test_inherited_method_policy_uses_configured_class_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    class BasePolicy:
        def __init__(self) -> None:
            pass

        def predict(self) -> None:
            pass

    class AllowedPolicy(BasePolicy):
        pass

    class BlockedPolicy(BasePolicy):
        pass

    for cls in (BasePolicy, AllowedPolicy, BlockedPolicy):
        cls.__qualname__ = cls.__name__
    for method_name in ("__init__", "predict"):
        getattr(BasePolicy, method_name).__qualname__ = f"BasePolicy.{method_name}"

    monkeypatch.setattr(remoter, "allowed_functions", set())
    monkeypatch.setattr(remoter, "callable_policy_frozen", False)
    monkeypatch.setattr(
        remoter,
        "remoterclassparams",
        {
            f"{AllowedPolicy.__module__}/AllowedPolicy": {"servercallablemethods": ["pre*"]},
            f"{BlockedPolicy.__module__}/BlockedPolicy": {"servercallablemethods": []},
        },
    )

    rmtclass.allowallfunctions(AllowedPolicy, isserver=True)
    rmtclass.allowallfunctions(BlockedPolicy, isserver=True)

    module_name = BasePolicy.__module__
    allowed_key = f"{module_name}/AllowedPolicy/predict"
    blocked_key = f"{module_name}/BlockedPolicy/predict"
    base_key = f"{module_name}/BasePolicy/predict"
    assert allowed_key in remoter.allowed_functions
    assert blocked_key not in remoter.allowed_functions
    assert base_key not in remoter.allowed_functions

    rmtclass.allowallfunctions(AllowedPolicy, isserver=False)
    instance = object.__new__(AllowedPolicy)
    payload = remoter.encode_function_call(instance.predict, "direct", {}, None)
    _, key, decoded_module, func_name, class_name, _, _ = remoter.deserialize_payload(payload)
    assert key == allowed_key
    assert decoded_module == module_name
    assert class_name == "AllowedPolicy"
    assert func_name == "predict"

    captured: dict[str, object] = {}

    class FakeRuntime:
        init = True

        def runSyncFunction(self, *args: object, **kwargs: object) -> str:
            captured["args"] = args
            captured["callable_key"] = kwargs["_remote_callable_key"]
            return "result"

    monkeypatch.setattr(remoter, "remoter", FakeRuntime())

    assert instance.predict() == "result"
    assert captured["callable_key"] == allowed_key


def test_decode_result_rejects_invalid_envelope() -> None:
    runtime = remoter.Remoter.createemptyinstance()
    runtime.remotedClasses = {}
    payload = remoter.serialize_payload(("only", "two"))

    with pytest.raises(CodecError, match="must contain three fields"):
        runtime.decode_result(payload, "direct", None)


def test_send_result_returns_serialization_failure_to_client() -> None:
    class FakeMessenger:
        def __init__(self) -> None:
            self.messages: list[bytes] = []

        def senddata(self, message: bytes) -> None:
            self.messages.append(message)

    runtime = remoter.Remoter.createemptyinstance()
    runtime.remotedClasses = {}
    fnid = uuid.uuid4()
    messenger = FakeMessenger()
    conn = {
        "alive": True,
        "classes": set(),
        "fns": {fnid: object()},
        "lock": threading.Lock(),
    }
    funcargs = {"key": "unit/test", "loc": "direct"}

    runtime.sendResult(messenger, conn, fnid, object(), None, funcargs)

    assert len(messenger.messages) == 1
    message = messenger.messages[0]
    assert message[1:17] == fnid.bytes
    _, result, ex = runtime.decode_result(message[17:], "direct", None)
    assert result is None
    assert isinstance(ex, remoter.RemoteExecutionError)
    assert "Cannot serialize object" in str(ex)
    assert fnid not in conn["fns"]


def test_send_result_uses_preencoded_fallback_when_error_encoding_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMessenger:
        def __init__(self) -> None:
            self.messages: list[bytes] = []

        def senddata(self, message: bytes) -> None:
            self.messages.append(message)

    runtime = remoter.Remoter.createemptyinstance()
    runtime.remotedClasses = {}
    fnid = uuid.uuid4()
    messenger = FakeMessenger()
    conn = {
        "alive": True,
        "classes": set(),
        "fns": {fnid: object()},
        "lock": threading.Lock(),
    }

    monkeypatch.setattr(runtime, "encode_result", lambda *_args, **_kwargs: (_ for _ in ()).throw(TypeError("bad")))
    monkeypatch.setattr(remoter, "serialize_payload", lambda _payload: (_ for _ in ()).throw(TypeError("worse")))

    runtime.sendResult(messenger, conn, fnid, None, None, {"key": "unit/test", "loc": "direct"})

    assert len(messenger.messages) == 1
    _, result, ex = runtime.decode_result(messenger.messages[0][17:], "direct", None)
    assert result is None
    assert isinstance(ex, remoter.RemoteExecutionError)
    assert "Failed to serialize remote result" in str(ex)
    assert fnid not in conn["fns"]


def test_unpack_result_converts_decode_failure_to_remote_error() -> None:
    runtime = remoter.Remoter.createemptyinstance()
    captured: list[tuple[uuid.UUID, object, Exception, dict[str, object]]] = []
    runtime.qcallback = lambda *args: captured.append(args)
    fnid = uuid.uuid4()
    message = int.to_bytes(remoter.MessageType.FunctionResult, 1, "big") + fnid.bytes

    runtime.unpackResult(message, "tcp://client", None)

    assert len(captured) == 1
    returned_fnid, result, ex, funcargs = captured[0]
    assert returned_fnid == fnid
    assert result is None
    assert isinstance(ex, remoter.RemoteExecutionError)
    assert "Empty payload received" in str(ex)
    assert funcargs == {"key": "unknown", "loc": "tcp://client"}


def test_imagep_style_adapter() -> None:
    class ImageP:
        def __init__(self, b: bytes, recompress_quality: int = 10) -> None:
            self.b = b
            self.rmtloc_rmt0bf: str | None = None
            self.recompress_quality = recompress_quality

        def setremoteloc(self, loc: str) -> None:
            self.rmtloc_rmt0bf = loc

    def encode_state(image: ImageP) -> dict[str, object]:
        image_bytes = image.b if image.rmtloc_rmt0bf in {"direct", "directqueue"} else image.b[:4]
        return {
            "b": image_bytes,
            "rmtloc": image.rmtloc_rmt0bf,
            "recompress_quality": image.recompress_quality,
        }

    def decode_state(state: dict[str, object]) -> ImageP:
        image = ImageP(state["b"])  # type: ignore[arg-type]
        image.rmtloc_rmt0bf = state["rmtloc"]  # type: ignore[assignment]
        image.recompress_quality = state["recompress_quality"]  # type: ignore[assignment]
        return image

    # Adapter may already be registered when tests are re-run in the same interpreter.
    with contextlib.suppress(ValueError):
        remoter.register_state_adapter(
            ext_code=30,
            cls=ImageP,
            encode_state=encode_state,
            decode_state=decode_state,
        )

    image = ImageP(b"abcdefghij")
    image.setremoteloc("tcp://10.0.0.1:9000")
    encoded = remoter.serialize_payload(image)
    decoded = remoter.deserialize_payload(encoded)

    assert isinstance(decoded, ImageP)
    assert decoded.b == b"abcd"
    assert decoded.rmtloc_rmt0bf == "tcp://10.0.0.1:9000"
