from __future__ import annotations

import struct
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import msgpack


class CodecError(Exception):
    """Base codec error."""


class CodecLimitsError(CodecError):
    """Raised when message exceeds configured limits."""


class CodecTypeError(CodecError):
    """Raised when a type is unsupported and unregistered."""


@dataclass(frozen=True)
class CodecLimits:
    max_encoded_bytes: int = 8 * 1024 * 1024
    max_nesting: int = 64
    max_collection_length: int = 100_000
    max_str_length: int = 1_048_576
    max_bytes_length: int = 8 * 1024 * 1024


@dataclass(frozen=True)
class AdapterContext:
    role: str


@dataclass(frozen=True)
class TypeAdapter:
    ext_code: int
    py_type: type[Any]
    encode: Callable[[Any, AdapterContext], Any]
    decode: Callable[[Any, AdapterContext], Any]


@dataclass(frozen=True)
class _ExtEnvelope:
    code: int
    data: bytes


class AdapterRegistry:
    """Explicit adapter registry for non-builtin types."""

    def __init__(self) -> None:
        self._code_to_adapter: dict[int, TypeAdapter] = {}
        self._type_to_adapter: dict[type[Any], TypeAdapter] = {}
        self._fallback_adapter: TypeAdapter | None = None

    def register(self, adapter: TypeAdapter) -> None:
        if adapter.ext_code in _RESERVED_EXT_CODES:
            raise ValueError(f"ext_code {adapter.ext_code} is reserved")
        if not 16 <= adapter.ext_code <= 127:
            raise ValueError("ext_code must be in range 16..127")
        if adapter.ext_code in self._code_to_adapter:
            raise ValueError(f"ext_code {adapter.ext_code} already registered")
        if adapter.py_type in self._type_to_adapter:
            raise ValueError(f"type {adapter.py_type} already registered")
        self._code_to_adapter[adapter.ext_code] = adapter
        self._type_to_adapter[adapter.py_type] = adapter

    def register_fallback(self, adapter: TypeAdapter) -> None:
        if self._fallback_adapter is not None:
            raise ValueError("fallback adapter already registered")
        if adapter.ext_code in _RESERVED_EXT_CODES:
            raise ValueError(f"ext_code {adapter.ext_code} is reserved")
        if not 16 <= adapter.ext_code <= 127:
            raise ValueError("ext_code must be in range 16..127")
        if adapter.ext_code in self._code_to_adapter:
            raise ValueError(f"ext_code {adapter.ext_code} already registered")
        self._code_to_adapter[adapter.ext_code] = adapter
        self._fallback_adapter = adapter

    def find_by_type(self, value: Any) -> TypeAdapter | None:
        # First hit exact type registrations; then fallback to isinstance matching.
        adapter = self._type_to_adapter.get(type(value))
        if adapter is not None:
            return adapter
        for registered_type, candidate in self._type_to_adapter.items():
            if isinstance(value, registered_type):
                return candidate
        return None

    def find_by_code(self, code: int) -> TypeAdapter | None:
        return self._code_to_adapter.get(code)

    def find_fallback(self) -> TypeAdapter | None:
        return self._fallback_adapter


_VERSION = 1
_FRAMED_VERSION = 2
_FRAME_MAGIC = b"RMT2"
_FRAME_HEADER = struct.Struct(">4sQ")
_BLOB_INDEX = struct.Struct(">I")
_BLOB_THRESHOLD = 64 * 1024
_RESERVED_EXT_TUPLE = 1
_RESERVED_EXT_UUID = 2
_RESERVED_EXT_BLOB = 3
_RESERVED_EXT_BIGINT = 4
_RESERVED_EXT_CODES = {_RESERVED_EXT_TUPLE, _RESERVED_EXT_UUID, _RESERVED_EXT_BLOB, _RESERVED_EXT_BIGINT}


def dumps(
    obj: Any,
    registry: AdapterRegistry,
    *,
    limits: CodecLimits,
    context: AdapterContext,
) -> bytes:
    blobs: list[bytes] = []
    payload = _encode_value(obj, 0, registry, limits, context, blobs)
    if blobs:
        packet = {"v": _FRAMED_VERSION, "p": payload, "b": [len(blob) for blob in blobs]}
        metadata = msgpack.packb(packet, use_bin_type=True, strict_types=True)
        encoded = b"".join((_FRAME_HEADER.pack(_FRAME_MAGIC, len(metadata)), metadata, *blobs))
    else:
        packet = {"v": _VERSION, "p": payload}
        encoded = msgpack.packb(packet, use_bin_type=True, strict_types=True)
    if len(encoded) > limits.max_encoded_bytes:
        raise CodecLimitsError(f"encoded bytes {len(encoded)} exceed max_encoded_bytes={limits.max_encoded_bytes}")
    return encoded


def loads(
    data: bytes,
    registry: AdapterRegistry,
    *,
    limits: CodecLimits,
    context: AdapterContext,
) -> Any:
    if len(data) > limits.max_encoded_bytes:
        raise CodecLimitsError(f"encoded bytes {len(data)} exceed max_encoded_bytes={limits.max_encoded_bytes}")

    packet_data, blobs = _unpack_frame(data, limits)
    packet = msgpack.unpackb(
        packet_data,
        raw=False,
        strict_map_key=False,
        ext_hook=lambda code, ext_data: _ExtEnvelope(code=code, data=ext_data),
        max_str_len=limits.max_str_length,
        max_bin_len=limits.max_bytes_length,
        max_array_len=limits.max_collection_length,
        max_map_len=limits.max_collection_length,
        max_ext_len=limits.max_bytes_length,
    )
    expected_version = _FRAMED_VERSION if blobs is not None else _VERSION
    if not isinstance(packet, dict) or packet.get("v") != expected_version or "p" not in packet:
        raise CodecError("invalid codec packet envelope")
    if blobs is not None and packet.get("b") != [len(blob) for blob in blobs]:
        raise CodecError("invalid codec blob lengths")
    return _decode_value(packet["p"], 0, registry, limits, context, blobs or [])


def _unpack_frame(data: bytes, limits: CodecLimits) -> tuple[bytes, list[bytes] | None]:
    if not data.startswith(_FRAME_MAGIC):
        return data, None
    if len(data) < _FRAME_HEADER.size:
        raise CodecError("truncated codec frame header")

    _, metadata_length = _FRAME_HEADER.unpack_from(data)
    metadata_end = _FRAME_HEADER.size + metadata_length
    if metadata_end > len(data):
        raise CodecError("truncated codec frame metadata")
    metadata = data[_FRAME_HEADER.size : metadata_end]
    packet = msgpack.unpackb(
        metadata,
        raw=False,
        strict_map_key=False,
        max_str_len=limits.max_str_length,
        max_bin_len=limits.max_bytes_length,
        max_array_len=limits.max_collection_length,
        max_map_len=limits.max_collection_length,
        max_ext_len=limits.max_bytes_length,
    )
    if not isinstance(packet, dict) or not isinstance(packet.get("b"), list):
        raise CodecError("invalid codec frame metadata")

    blobs: list[bytes] = []
    offset = metadata_end
    for blob_length in packet["b"]:
        if not isinstance(blob_length, int) or blob_length < 0:
            raise CodecError("invalid codec blob length")
        if blob_length > limits.max_bytes_length:
            raise CodecLimitsError(f"bytes length {blob_length} exceeds max_bytes_length={limits.max_bytes_length}")
        blob_end = offset + blob_length
        if blob_end > len(data):
            raise CodecError("truncated codec blob")
        blobs.append(data[offset:blob_end])
        offset = blob_end
    if offset != len(data):
        raise CodecError("unexpected trailing codec frame data")
    return metadata, blobs


def _encode_value(
    value: Any,
    depth: int,
    registry: AdapterRegistry,
    limits: CodecLimits,
    context: AdapterContext,
    blobs: list[bytes],
) -> Any:
    if depth > limits.max_nesting:
        raise CodecLimitsError(f"nesting depth {depth} exceeds max_nesting={limits.max_nesting}")

    if value is None:
        return value
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, int):
        value = int(value)
        if -(1 << 63) <= value <= (1 << 64) - 1:
            return value
        magnitude = abs(value).to_bytes((abs(value).bit_length() + 7) // 8, "big")
        if len(magnitude) + 1 > limits.max_bytes_length:
            raise CodecLimitsError(
                f"bigint bytes {len(magnitude) + 1} exceed max_bytes_length={limits.max_bytes_length}"
            )
        sign = b"\x01" if value < 0 else b"\x00"
        return msgpack.ExtType(_RESERVED_EXT_BIGINT, sign + magnitude)
    if isinstance(value, str):
        if len(value) > limits.max_str_length:
            raise CodecLimitsError(f"string length {len(value)} exceeds max_str_length={limits.max_str_length}")
        return str(value)
    if isinstance(value, bytes):
        if len(value) > limits.max_bytes_length:
            raise CodecLimitsError(f"bytes length {len(value)} exceeds max_bytes_length={limits.max_bytes_length}")
        value = bytes(value)
        if len(value) >= _BLOB_THRESHOLD:
            blob_index = len(blobs)
            blobs.append(value)
            return msgpack.ExtType(_RESERVED_EXT_BLOB, _BLOB_INDEX.pack(blob_index))
        return value
    if isinstance(value, uuid.UUID):
        return msgpack.ExtType(_RESERVED_EXT_UUID, value.bytes)
    if isinstance(value, list):
        if len(value) > limits.max_collection_length:
            raise CodecLimitsError(
                f"list length {len(value)} exceeds max_collection_length={limits.max_collection_length}"
            )
        return [_encode_value(item, depth + 1, registry, limits, context, blobs) for item in value]
    if isinstance(value, tuple):
        if len(value) > limits.max_collection_length:
            raise CodecLimitsError(
                f"tuple length {len(value)} exceeds max_collection_length={limits.max_collection_length}"
            )
        encoded = [_encode_value(item, depth + 1, registry, limits, context, blobs) for item in value]
        return msgpack.ExtType(
            _RESERVED_EXT_TUPLE,
            msgpack.packb(encoded, use_bin_type=True, strict_types=True),
        )
    if isinstance(value, dict):
        if len(value) > limits.max_collection_length:
            raise CodecLimitsError(
                f"dict length {len(value)} exceeds max_collection_length={limits.max_collection_length}"
            )
        encoded_dict: dict[Any, Any] = {}
        for key, item in value.items():
            encoded_key = _encode_value(key, depth + 1, registry, limits, context, blobs)
            if not _is_hashable(encoded_key):
                raise CodecTypeError(f"decoded dict key type {type(key)} is not hashable")
            encoded_dict[encoded_key] = _encode_value(item, depth + 1, registry, limits, context, blobs)
        return encoded_dict

    adapter = registry.find_by_type(value)
    if adapter is None:
        adapter = registry.find_fallback()
        if adapter is None:
            raise CodecTypeError(f"unsupported type {type(value)!r}; register an explicit adapter")

    payload = adapter.encode(value, context)
    encoded_payload = _encode_value(payload, depth + 1, registry, limits, context, blobs)
    return msgpack.ExtType(
        adapter.ext_code,
        msgpack.packb(encoded_payload, use_bin_type=True, strict_types=True),
    )


def _decode_value(
    value: Any,
    depth: int,
    registry: AdapterRegistry,
    limits: CodecLimits,
    context: AdapterContext,
    blobs: list[bytes],
) -> Any:
    if depth > limits.max_nesting:
        raise CodecLimitsError(f"nesting depth {depth} exceeds max_nesting={limits.max_nesting}")

    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        if isinstance(value, str) and len(value) > limits.max_str_length:
            raise CodecLimitsError(f"string length {len(value)} exceeds max_str_length={limits.max_str_length}")
        if isinstance(value, bytes) and len(value) > limits.max_bytes_length:
            raise CodecLimitsError(f"bytes length {len(value)} exceeds max_bytes_length={limits.max_bytes_length}")
        return value

    if isinstance(value, list):
        if len(value) > limits.max_collection_length:
            raise CodecLimitsError(
                f"list length {len(value)} exceeds max_collection_length={limits.max_collection_length}"
            )
        return [_decode_value(item, depth + 1, registry, limits, context, blobs) for item in value]

    if isinstance(value, dict):
        if len(value) > limits.max_collection_length:
            raise CodecLimitsError(
                f"dict length {len(value)} exceeds max_collection_length={limits.max_collection_length}"
            )
        decoded_dict: dict[Any, Any] = {}
        for key, item in value.items():
            decoded_key = _decode_value(key, depth + 1, registry, limits, context, blobs)
            if not _is_hashable(decoded_key):
                raise CodecTypeError(f"decoded dict key type {type(decoded_key)} is not hashable")
            decoded_dict[decoded_key] = _decode_value(item, depth + 1, registry, limits, context, blobs)
        return decoded_dict

    if isinstance(value, _ExtEnvelope):
        if value.code == _RESERVED_EXT_BLOB:
            if len(value.data) != _BLOB_INDEX.size:
                raise CodecError("blob extension payload must contain an index")
            (blob_index,) = _BLOB_INDEX.unpack(value.data)
            if blob_index >= len(blobs):
                raise CodecError(f"unknown codec blob index {blob_index}")
            return blobs[blob_index]

        if value.code == _RESERVED_EXT_TUPLE:
            as_list = msgpack.unpackb(
                value.data,
                raw=False,
                strict_map_key=False,
                ext_hook=lambda c, d: _ExtEnvelope(c, d),
                max_str_len=limits.max_str_length,
                max_bin_len=limits.max_bytes_length,
                max_array_len=limits.max_collection_length,
                max_map_len=limits.max_collection_length,
                max_ext_len=limits.max_bytes_length,
            )
            if not isinstance(as_list, list):
                raise CodecError("tuple extension payload must be a list")
            return tuple(_decode_value(item, depth + 1, registry, limits, context, blobs) for item in as_list)

        if value.code == _RESERVED_EXT_UUID:
            if len(value.data) != 16:
                raise CodecError("UUID extension payload must be 16 bytes")
            return uuid.UUID(bytes=value.data)

        if value.code == _RESERVED_EXT_BIGINT:
            if len(value.data) < 2 or value.data[0] not in (0, 1) or value.data[1] == 0:
                raise CodecError("invalid bigint extension payload")
            magnitude = int.from_bytes(value.data[1:], "big")
            return -magnitude if value.data[0] else magnitude

        adapter = registry.find_by_code(value.code)
        if adapter is None:
            raise CodecTypeError(f"unknown extension code {value.code}")

        raw_payload = msgpack.unpackb(
            value.data,
            raw=False,
            strict_map_key=False,
            ext_hook=lambda c, d: _ExtEnvelope(c, d),
            max_str_len=limits.max_str_length,
            max_bin_len=limits.max_bytes_length,
            max_array_len=limits.max_collection_length,
            max_map_len=limits.max_collection_length,
            max_ext_len=limits.max_bytes_length,
        )
        decoded_payload = _decode_value(raw_payload, depth + 1, registry, limits, context, blobs)
        return adapter.decode(decoded_payload, context)

    raise CodecTypeError(f"unsupported decoded wire type {type(value)!r}")


def _is_hashable(value: Any) -> bool:
    try:
        hash(value)
        return True
    except TypeError:
        return False
