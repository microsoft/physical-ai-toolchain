from __future__ import annotations

import threading
import uuid

import pytest

from remoter import class2dict, remoter
from remoter.safe_codec import (
    AdapterContext,
    AdapterRegistry,
    CodecError,
    CodecLimits,
    CodecLimitsError,
    CodecTypeError,
    dumps,
    loads,
)


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

    try:
        remoter.register_state_adapter(
            ext_code=30,
            cls=ImageP,
            encode_state=encode_state,
            decode_state=decode_state,
        )
    except ValueError:
        # Adapter may already be registered when tests are re-run in the same interpreter.
        pass

    image = ImageP(b"abcdefghij")
    image.setremoteloc("tcp://10.0.0.1:9000")
    encoded = remoter.serialize_payload(image)
    decoded = remoter.deserialize_payload(encoded)

    assert isinstance(decoded, ImageP)
    assert decoded.b == b"abcd"
    assert decoded.rmtloc_rmt0bf == "tcp://10.0.0.1:9000"
