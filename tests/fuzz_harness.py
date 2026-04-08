"""Polyglot fuzz harness — runs as pytest test AND Atheris coverage-guided fuzzer.

Satisfies the OpenSSF Scorecard Fuzzing check (Phase 3) which detects
``import atheris`` in any .py file in the repository.

When executed by pytest, deterministic test classes exercise the same
functions with controlled inputs. When executed directly with atheris
installed, ``fuzz_dispatch`` routes randomized bytes to all registered
targets via ``FuzzedDataProvider``.

Targets:
    - ``validate_blob_path`` / ``get_validation_error`` — blob path regex validation
    - ``_extract_from_value`` / ``_extract_from_tracking_data`` — metrics type dispatch
    - ``sanitize_user_string`` / ``_sanitize_nested_value`` — dataviewer input sanitization
    - ``validate_safe_string`` — dataviewer string validation with pattern matching
    - ``dataset_id_to_blob_prefix`` — dataset ID to storage path conversion
    - ``DateTimeEncoder`` — JSON serialization of datetime objects
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import atheris

    FUZZING = True
except ImportError:
    FUZZING = False

from blob_path_validator import get_validation_error, validate_blob_path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str) -> Any:
    """Load a source module by file path, bypassing the package tree."""
    full_path = _REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, full_path)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module {name!r} from {full_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_metrics = _load_module("metrics_fuzz", "training/utils/metrics.py")
_extract_from_value = _metrics._extract_from_value
_extract_from_tracking_data = _metrics._extract_from_tracking_data

_DATA_TYPES = ["raw", "converted", "reports", "checkpoints"]

# Dataviewer backend modules loaded via importlib to avoid full FastAPI import chain.
# We import only the pure functions that don't require the FastAPI app context.
_validation = _load_module("validation_fuzz", "data-management/viewer/backend/src/api/validation.py")
_sanitize_user_string = _validation.sanitize_user_string
_sanitize_nested_value = _validation._sanitize_nested_value
_validate_safe_string = _validation.validate_safe_string
_SAFE_DATASET_ID_PATTERN = _validation.SAFE_DATASET_ID_PATTERN
_SAFE_CAMERA_NAME_PATTERN = _validation.SAFE_CAMERA_NAME_PATTERN

_paths = _load_module("paths_fuzz", "data-management/viewer/backend/src/api/storage/paths.py")
_dataset_id_to_blob_prefix = _paths.dataset_id_to_blob_prefix

_serializers = _load_module("serializers_fuzz", "data-management/viewer/backend/src/api/storage/serializers.py")
_DateTimeEncoder = _serializers.DateTimeEncoder


# ================================================================
# Fuzz functions (Atheris mode only — never called during pytest)
# ================================================================


def fuzz_validate_blob_path(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    path = fdp.ConsumeUnicodeNoSurrogates(256)
    idx = fdp.ConsumeIntInRange(0, len(_DATA_TYPES) - 1)
    with suppress(ValueError):
        validate_blob_path(path, _DATA_TYPES[idx])


def fuzz_get_validation_error(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    path = fdp.ConsumeUnicodeNoSurrogates(256)
    idx = fdp.ConsumeIntInRange(0, len(_DATA_TYPES) - 1)
    with suppress(ValueError):
        get_validation_error(path, _DATA_TYPES[idx])


def fuzz_extract_from_value(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    name = fdp.ConsumeUnicodeNoSurrogates(64)
    choice = fdp.ConsumeIntInRange(0, 4)
    if choice == 0:
        value: Any = fdp.ConsumeFloat()
    elif choice == 1:
        value = fdp.ConsumeInt(8)
    elif choice == 2:
        value = fdp.ConsumeUnicodeNoSurrogates(32)
    elif choice == 3:
        value = None
    else:
        value = [fdp.ConsumeFloat() for _ in range(fdp.ConsumeIntInRange(0, 5))]
    metrics: dict[str, float] = {}
    with suppress(ValueError, TypeError, OverflowError):
        _extract_from_value(name, value, metrics)


def _build_fuzz_dict(fdp: Any, depth: int) -> dict[str, Any]:
    d: dict[str, Any] = {}
    num_keys = fdp.ConsumeIntInRange(0, 4)
    for _ in range(num_keys):
        key = fdp.ConsumeUnicodeNoSurrogates(16)
        if depth > 0 and fdp.ConsumeBool():
            d[key] = _build_fuzz_dict(fdp, depth - 1)
        else:
            choice = fdp.ConsumeIntInRange(0, 2)
            if choice == 0:
                d[key] = fdp.ConsumeFloat()
            elif choice == 1:
                d[key] = fdp.ConsumeInt(8)
            else:
                d[key] = fdp.ConsumeUnicodeNoSurrogates(16)
    return d


def fuzz_extract_from_tracking_data(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    tracking = _build_fuzz_dict(fdp, depth=fdp.ConsumeIntInRange(0, 3))
    metrics: dict[str, float] = {}
    with suppress(ValueError, TypeError, OverflowError):
        _extract_from_tracking_data(tracking, metrics, "")


def fuzz_sanitize_user_string(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    value = fdp.ConsumeUnicodeNoSurrogates(512)
    _sanitize_user_string(value)


def _build_nested_fuzz_value(fdp: Any, depth: int) -> Any:
    choice = fdp.ConsumeIntInRange(0, 6)
    if choice == 0:
        return fdp.ConsumeUnicodeNoSurrogates(64)
    if choice == 1:
        return fdp.ConsumeFloat()
    if choice == 2:
        return fdp.ConsumeInt(8)
    if choice == 3 and depth > 0:
        return [_build_nested_fuzz_value(fdp, depth - 1) for _ in range(fdp.ConsumeIntInRange(0, 3))]
    if choice == 4 and depth > 0:
        return tuple(_build_nested_fuzz_value(fdp, depth - 1) for _ in range(fdp.ConsumeIntInRange(0, 3)))
    if choice == 5 and depth > 0:
        return {fdp.ConsumeUnicodeNoSurrogates(16): _build_nested_fuzz_value(fdp, depth - 1)}
    return fdp.ConsumeUnicodeNoSurrogates(16)


def fuzz_sanitize_nested_value(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    value = _build_nested_fuzz_value(fdp, depth=fdp.ConsumeIntInRange(0, 3))
    _sanitize_nested_value(value)


def fuzz_validate_safe_string(data: bytes) -> None:
    from fastapi import HTTPException

    fdp = atheris.FuzzedDataProvider(data)
    value = fdp.ConsumeUnicodeNoSurrogates(256)
    patterns = [_SAFE_DATASET_ID_PATTERN, _SAFE_CAMERA_NAME_PATTERN]
    pattern = patterns[fdp.ConsumeIntInRange(0, len(patterns) - 1)]
    with suppress(HTTPException, ValueError, TypeError):
        _validate_safe_string(value, pattern=pattern, label="fuzz")


def fuzz_dataset_id_to_blob_prefix(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    dataset_id = fdp.ConsumeUnicodeNoSurrogates(256)
    _dataset_id_to_blob_prefix(dataset_id)


def fuzz_datetime_encoder(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    year = fdp.ConsumeIntInRange(1, 9999)
    month = fdp.ConsumeIntInRange(1, 12)
    day = fdp.ConsumeIntInRange(1, 28)
    hour = fdp.ConsumeIntInRange(0, 23)
    minute = fdp.ConsumeIntInRange(0, 59)
    second = fdp.ConsumeIntInRange(0, 59)
    dt = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    encoder = _DateTimeEncoder()
    with suppress(TypeError, ValueError, OverflowError):
        encoder.encode({"ts": dt})
    with suppress(TypeError, ValueError, OverflowError):
        encoder.encode({"val": fdp.ConsumeUnicodeNoSurrogates(32)})


FUZZ_TARGETS = [
    fuzz_validate_blob_path,
    fuzz_get_validation_error,
    fuzz_extract_from_value,
    fuzz_extract_from_tracking_data,
    fuzz_sanitize_user_string,
    fuzz_sanitize_nested_value,
    fuzz_validate_safe_string,
    fuzz_dataset_id_to_blob_prefix,
    fuzz_datetime_encoder,
]


def fuzz_dispatch(data: bytes) -> None:
    if not data:
        return
    idx = data[0] % len(FUZZ_TARGETS)
    FUZZ_TARGETS[idx](data[1:])


# ================================================================
# Pytest tests (deterministic mode)
# ================================================================

_BLOB_PATH_CASES = [
    ("raw/robot-01/2026-03-05/episode-001.mcap", "raw", True),
    ("raw/ROBOT-01/2026-03-05/episode.mcap", "raw", False),
    ("converted/pick-place/data/chunk-000.parquet", "converted", True),
    ("converted/pick-place-v2/meta/info.json", "converted", True),
    ("reports/eval-run/2026-01-15/summary.json", "reports", True),
    ("checkpoints/policy-01/20260315_143022.pt", "checkpoints", True),
    ("checkpoints/policy-01/20260315_143022_step_1000.onnx", "checkpoints", True),
    ("", "raw", False),
    ("../traversal/attack.mcap", "raw", False),
]


class TestFuzzValidateBlobPath:
    def test_known_paths(self) -> None:
        for path, data_type, expected in _BLOB_PATH_CASES:
            assert validate_blob_path(path, data_type) is expected, f"Failed for {path!r}"

    def test_unknown_data_type_raises(self) -> None:
        try:
            validate_blob_path("any/path", "unknown")  # type: ignore[arg-type]
        except ValueError:
            return
        raise AssertionError("Expected ValueError")


class TestFuzzGetValidationError:
    def test_valid_path_returns_none(self) -> None:
        assert get_validation_error("raw/robot-01/2026-03-05/episode.mcap", "raw") is None

    def test_invalid_uppercase_path(self) -> None:
        error = get_validation_error("INVALID/PATH", "raw")
        assert error is not None
        assert "uppercase" in error

    def test_invalid_spaces_path(self) -> None:
        error = get_validation_error("raw/robot 01/2026-03-05/ep.mcap", "raw")
        assert error is not None
        assert "spaces" in error


class TestFuzzExtractFromValue:
    def test_float_value(self) -> None:
        metrics: dict[str, float] = {}
        _extract_from_value("loss", 3.14, metrics)
        assert metrics["loss"] == 3.14

    def test_none_value(self) -> None:
        metrics: dict[str, float] = {}
        _extract_from_value("loss", None, metrics)
        assert "loss" not in metrics

    def test_int_value(self) -> None:
        metrics: dict[str, float] = {}
        _extract_from_value("step", 42, metrics)
        assert metrics["step"] == 42.0

    def test_string_value_ignored(self) -> None:
        metrics: dict[str, float] = {}
        _extract_from_value("tag", "not-a-number", metrics)
        assert "tag" not in metrics


class TestFuzzExtractFromTrackingData:
    def test_flat_dict(self) -> None:
        metrics: dict[str, float] = {}
        _extract_from_tracking_data({"loss": 0.5, "reward": 1.0}, metrics, "")
        assert metrics["loss"] == 0.5
        assert metrics["reward"] == 1.0

    def test_nested_dict(self) -> None:
        metrics: dict[str, float] = {}
        _extract_from_tracking_data({"train": {"loss": 0.1}}, metrics, "")
        assert metrics["train/loss"] == 0.1

    def test_empty_dict(self) -> None:
        metrics: dict[str, float] = {}
        _extract_from_tracking_data({}, metrics, "")
        assert len(metrics) == 0


class TestFuzzSanitizeUserString:
    def test_strips_carriage_return(self) -> None:
        assert _sanitize_user_string("hello\rworld") == "helloworld"

    def test_strips_line_feed(self) -> None:
        assert _sanitize_user_string("hello\nworld") == "helloworld"

    def test_strips_crlf(self) -> None:
        assert _sanitize_user_string("a\r\nb") == "ab"

    def test_preserves_normal_string(self) -> None:
        assert _sanitize_user_string("hello-world_123") == "hello-world_123"

    def test_empty_string(self) -> None:
        assert _sanitize_user_string("") == ""

    def test_unicode_passthrough(self) -> None:
        result = _sanitize_user_string("café-über")
        assert "\r" not in result
        assert "\n" not in result


class TestFuzzSanitizeNestedValue:
    def test_string_sanitized(self) -> None:
        result = _sanitize_nested_value("hello\r\nworld")
        assert result == "helloworld"

    def test_list_elements_sanitized(self) -> None:
        result = _sanitize_nested_value(["a\rb", "c\nd"])
        assert result == ["ab", "cd"]

    def test_tuple_elements_sanitized(self) -> None:
        result = _sanitize_nested_value(("x\ry",))
        assert result == ("xy",)

    def test_dict_values_sanitized(self) -> None:
        result = _sanitize_nested_value({"key": "val\nue"})
        assert result == {"key": "value"}

    def test_non_string_passthrough(self) -> None:
        assert _sanitize_nested_value(42) == 42
        assert _sanitize_nested_value(3.14) == 3.14
        assert _sanitize_nested_value(None) is None


class TestFuzzValidateSafeString:
    def test_valid_dataset_id(self) -> None:
        _validate_safe_string("robot-01.data_v2", pattern=_SAFE_DATASET_ID_PATTERN, label="test")

    def test_null_byte_rejected(self) -> None:
        from fastapi import HTTPException

        try:
            _validate_safe_string("robot\x00evil", pattern=_SAFE_DATASET_ID_PATTERN, label="test")
        except HTTPException as exc:
            assert exc.status_code == 400
            return
        raise AssertionError("Expected HTTPException for null byte")

    def test_traversal_rejected(self) -> None:
        from fastapi import HTTPException

        try:
            _validate_safe_string("../etc/passwd", pattern=_SAFE_DATASET_ID_PATTERN, label="test")
        except HTTPException as exc:
            assert exc.status_code == 400
            return
        raise AssertionError("Expected HTTPException for path traversal")

    def test_valid_camera_name(self) -> None:
        _validate_safe_string("front_left.rgb", pattern=_SAFE_CAMERA_NAME_PATTERN, label="test")

    def test_pattern_mismatch_rejected(self) -> None:
        from fastapi import HTTPException

        try:
            _validate_safe_string("!@#$%^&*()", pattern=_SAFE_DATASET_ID_PATTERN, label="test")
        except HTTPException as exc:
            assert exc.status_code == 400
            return
        raise AssertionError("Expected HTTPException for invalid pattern")


class TestFuzzDatasetIdToBlobPrefix:
    def test_double_dash_replaced(self) -> None:
        assert _dataset_id_to_blob_prefix("group--dataset") == "group/dataset"

    def test_multiple_separators(self) -> None:
        assert _dataset_id_to_blob_prefix("a--b--c") == "a/b/c"

    def test_no_separator(self) -> None:
        assert _dataset_id_to_blob_prefix("simple-name") == "simple-name"

    def test_empty_string(self) -> None:
        assert _dataset_id_to_blob_prefix("") == ""


class TestFuzzDateTimeEncoder:
    def test_datetime_serialized(self) -> None:
        dt = datetime(2026, 3, 15, 14, 30, 22, tzinfo=timezone.utc)
        result = json.loads(json.dumps({"ts": dt}, cls=_DateTimeEncoder))
        assert result["ts"] == "2026-03-15T14:30:22+00:00"

    def test_non_datetime_passthrough(self) -> None:
        result = json.loads(json.dumps({"val": "hello", "num": 42}, cls=_DateTimeEncoder))
        assert result["val"] == "hello"
        assert result["num"] == 42

    def test_non_serializable_raises(self) -> None:
        try:
            json.dumps({"bad": set()}, cls=_DateTimeEncoder)
        except TypeError:
            return
        raise AssertionError("Expected TypeError for non-serializable set")


# ================================================================
# Atheris entry point
# ================================================================

if __name__ == "__main__" and FUZZING:
    _crash_dir = _REPO_ROOT / "logs" / "fuzz-crashes"
    _crash_dir.mkdir(parents=True, exist_ok=True)

    _corpus_dir = Path(__file__).parent / "fuzz-corpus"
    _argv = sys.argv + [f"-artifact_prefix={_crash_dir}/"]
    if _corpus_dir.is_dir() and str(_corpus_dir) not in sys.argv:
        _argv.append(str(_corpus_dir))

    atheris.instrument_all()
    atheris.Setup(_argv, fuzz_dispatch)
    atheris.Fuzz()
