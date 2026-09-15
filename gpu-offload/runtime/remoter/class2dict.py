"""Utilities to convert class instances to plain dictionaries and back.

The produced dictionary embeds the fully qualified type name under the
``__type__`` key so the original object can be reconstructed later.
"""

from __future__ import annotations

import enum
import importlib
import sys
import uuid
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from pathlib import PurePath
from threading import Lock
from typing import Any

from .safe_codec import CodecLimits, CodecLimitsError

TYPE_KEY = "__type__"
VALUE_KEY = "__value__"
TENSOR_KEY = "__tensor__"

_PRIMITIVES = (str, bytes, int, float, bool, uuid.UUID, type(None))
_NAME_TO_TYPE: dict[str, type[Any]] = {}
_TYPE_TO_NAME: dict[type[Any], str] = {}
_UNAVAILABLE_TENSOR_DEVICES: dict[str, None] = {}
_UNAVAILABLE_TENSOR_DEVICES_LOCK = Lock()
_UNAVAILABLE_TENSOR_DEVICES_MAX_SIZE = 32


def _qualname(cls: type) -> str:
    return _TYPE_TO_NAME.get(cls, f"{cls.__module__}:{cls.__qualname__}")


def register_type(cls: type[Any], *, name: str | None = None) -> None:
    """Register a stable wire name for a class2dict-serialized type."""
    wire_name = name or f"{cls.__module__}:{cls.__qualname__}"
    module_name, separator, qualname = wire_name.partition(":")
    if not separator or not module_name or not qualname:
        raise ValueError("class2dict type name must use the format 'module:qualname'")

    registered_type = _NAME_TO_TYPE.get(wire_name)
    if registered_type is not None and registered_type is not cls:
        raise ValueError(f"class2dict type name {wire_name!r} is already registered")

    registered_name = _TYPE_TO_NAME.get(cls)
    if registered_name is not None and registered_name != wire_name:
        raise ValueError(f"class {cls!r} is already registered as {registered_name!r}")

    _NAME_TO_TYPE[wire_name] = cls
    _TYPE_TO_NAME[cls] = wire_name


def _resolve(path: str) -> type:
    registered_type = _NAME_TO_TYPE.get(path)
    if registered_type is not None:
        return registered_type

    module_name, _, qualname = path.partition(":")
    obj: Any = sys.modules.get(module_name)
    if obj is None:
        raise TypeError(
            f"Cannot reconstruct class2dict type {path!r}: module {module_name!r} is not loaded. "
            "Load the defining module on this endpoint or register the local equivalent with "
            "remoter.register_class2dict_type(..., wire_name=<sender type name>)."
        )
    for part in qualname.split("."):
        obj = getattr(obj, part)
    if not isinstance(obj, type):
        raise TypeError(f"Resolved class2dict type {path!r} to non-type object {obj!r}")
    return obj


def _get_torch() -> Any | None:
    try:
        return importlib.import_module("torch")
    except ModuleNotFoundError:
        return None


def _is_torch_tensor_type(cls: type[Any]) -> bool:
    return any(base.__module__ == "torch" and base.__name__ == "Tensor" for base in cls.__mro__)


def _is_tensor_device_unavailable(device: str) -> bool:
    with _UNAVAILABLE_TENSOR_DEVICES_LOCK:
        return device in _UNAVAILABLE_TENSOR_DEVICES


def _remember_unavailable_tensor_device(device: str) -> None:
    with _UNAVAILABLE_TENSOR_DEVICES_LOCK:
        _UNAVAILABLE_TENSOR_DEVICES[device] = None
        if len(_UNAVAILABLE_TENSOR_DEVICES) > _UNAVAILABLE_TENSOR_DEVICES_MAX_SIZE:
            del _UNAVAILABLE_TENSOR_DEVICES[next(iter(_UNAVAILABLE_TENSOR_DEVICES))]


def _tensor_byte_length(shape: list[int], itemsize: int, max_bytes_length: int) -> int:
    max_numel = max_bytes_length // itemsize
    numel = 1
    for size in shape:
        if size and numel > max_numel // size:
            raise CodecLimitsError(f"tensor bytes exceed max_bytes_length={max_bytes_length}")
        numel *= size
    if numel > max_numel:
        raise CodecLimitsError(f"tensor bytes exceed max_bytes_length={max_bytes_length}")
    return numel * itemsize


def _tensor_to_dict(obj: Any) -> dict[str, Any] | None:
    if not _is_torch_tensor_type(type(obj)):
        return None
    torch = _get_torch()
    if torch is None or not isinstance(obj, torch.Tensor):
        return None
    if obj.layout != torch.strided:
        raise TypeError(f"Cannot serialize tensor with layout {obj.layout}; register an explicit adapter")

    contiguous = obj.detach().cpu().contiguous().reshape(-1)
    data = contiguous.view(torch.uint8).numpy().tobytes() if contiguous.numel() else b""
    return {
        TYPE_KEY: _qualname(type(obj)),
        TENSOR_KEY: {
            "data": data,
            "device": str(obj.device),
            "dtype": str(obj.dtype).removeprefix("torch."),
            "requires_grad": obj.requires_grad,
            "shape": list(obj.shape),
        },
    }


def _tensor_from_dict(target: type[Any], payload: Any, device: Any | None, limits: CodecLimits) -> Any:
    torch = _get_torch()
    if torch is None or not issubclass(target, torch.Tensor):
        raise TypeError(f"Cannot reconstruct tensor type {target!r}: PyTorch is not installed")
    if not isinstance(payload, dict):
        raise TypeError("Tensor payload must be a mapping")

    dtype_name = payload.get("dtype")
    dtype = getattr(torch, dtype_name, None) if isinstance(dtype_name, str) else None
    if not isinstance(dtype, torch.dtype):
        raise TypeError(f"Tensor payload has unsupported dtype {dtype_name!r}")

    shape = payload.get("shape")
    if not isinstance(shape, list) or not all(isinstance(size, int) and size >= 0 for size in shape):
        raise TypeError("Tensor payload shape must be a list of non-negative integers")
    data = payload.get("data")
    if not isinstance(data, bytes):
        raise TypeError("Tensor payload data must be bytes")
    expected_bytes = _tensor_byte_length(shape, dtype.itemsize, limits.max_bytes_length)
    if len(data) != expected_bytes:
        raise TypeError(f"Tensor payload data length {len(data)} does not match expected length {expected_bytes}")
    source_device = payload.get("device", "cpu")
    if not isinstance(source_device, str):
        raise TypeError("Tensor payload device must be a string")

    if data:
        tensor = torch.frombuffer(bytearray(data), dtype=dtype).reshape(shape)
    else:
        tensor = torch.empty(shape, dtype=dtype)
    target_device = device if device is not None else source_device
    if device is None and _is_tensor_device_unavailable(source_device):
        target_device = "cpu"
    try:
        tensor = tensor.to(target_device)
    except (AssertionError, RuntimeError, TypeError, ValueError):
        if device is not None or target_device == "cpu":
            raise
        _remember_unavailable_tensor_device(source_device)
        tensor = tensor.to("cpu")
    tensor.requires_grad_(bool(payload.get("requires_grad", False)))
    if target is torch.Tensor:
        return tensor
    return tensor.as_subclass(target)


def to_dict(obj: Any) -> Any:
    """Recursively convert ``obj`` into JSON friendly primitives."""
    if isinstance(obj, enum.Enum):
        return {TYPE_KEY: _qualname(type(obj)), VALUE_KEY: obj.value}
    if isinstance(obj, PurePath):
        return {TYPE_KEY: _qualname(type(obj)), VALUE_KEY: str(obj)}
    if isinstance(obj, _PRIMITIVES):
        return obj
    tensor_data = _tensor_to_dict(obj)
    if tensor_data is not None:
        return tensor_data
    if isinstance(obj, (datetime, date)):
        return {TYPE_KEY: _qualname(type(obj)), VALUE_KEY: obj.isoformat()}
    if isinstance(obj, (list, tuple, set)):
        items = [to_dict(item) for item in obj]
        if isinstance(obj, list):
            return items
        return {TYPE_KEY: _qualname(type(obj)), VALUE_KEY: items}
    if isinstance(obj, dict):
        return {key: to_dict(value) for key, value in obj.items()}

    if is_dataclass(obj):
        data = {f.name: to_dict(getattr(obj, f.name)) for f in fields(obj)}
    elif hasattr(obj, "__dict__"):
        data = {k: to_dict(v) for k, v in vars(obj).items()}
    elif hasattr(obj, "__slots__"):
        data = {name: to_dict(getattr(obj, name)) for name in obj.__slots__ if hasattr(obj, name)}
    else:
        raise TypeError(f"Cannot serialize object of type {type(obj)!r}")

    data[TYPE_KEY] = _qualname(type(obj))
    return data


def from_dict(
    data: Any,
    cls: type | None = None,
    *,
    device: Any | None = None,
    limits: CodecLimits | None = None,
) -> Any:
    """Rebuild an object on ``device``, or use the source device with CPU fallback."""
    limits = limits or CodecLimits()
    if isinstance(data, _PRIMITIVES):
        return data
    if isinstance(data, list):
        return [from_dict(item, device=device, limits=limits) for item in data]
    if not isinstance(data, dict):
        return data

    if TYPE_KEY not in data and cls is None:
        return {key: from_dict(value, device=device, limits=limits) for key, value in data.items()}

    target = cls if cls is not None else _resolve(data[TYPE_KEY])

    if TENSOR_KEY in data:
        return _tensor_from_dict(target, data[TENSOR_KEY], device, limits)

    if VALUE_KEY in data:
        value = data[VALUE_KEY]
        if isinstance(target, type) and issubclass(target, enum.Enum):
            return target(value)
        if isinstance(target, type) and issubclass(target, (datetime, date)):
            return target.fromisoformat(value)
        if isinstance(target, type) and issubclass(target, (tuple, set, frozenset)):
            return target(from_dict(item, device=device, limits=limits) for item in value)
        return target(value)

    payload = {k: from_dict(v, device=device, limits=limits) for k, v in data.items() if k != TYPE_KEY}

    instance = object.__new__(target)
    for key, value in payload.items():
        object.__setattr__(instance, key, value)
    return instance


__all__ = ["TENSOR_KEY", "TYPE_KEY", "VALUE_KEY", "from_dict", "register_type", "to_dict"]
