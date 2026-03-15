"""Hypothesis property-based tests for _extract_from_value."""

import numpy as np
from conftest import load_training_module
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

metrics_module = load_training_module("metrics_under_test", "training/utils/metrics.py")
_extract_from_value = metrics_module._extract_from_value


class FakeTensor:
    def __init__(self, value, numel_val=1, values=None):
        self._value = value
        self._numel = numel_val
        self._values = values

    def item(self):
        return self._value

    def numel(self):
        return self._numel

    def mean(self):
        m = sum(self._values) / len(self._values)
        return FakeTensor(m)

    def std(self):
        m = sum(self._values) / len(self._values)
        variance = sum((x - m) ** 2 for x in self._values) / len(self._values)
        return FakeTensor(variance**0.5)

    def min(self):
        return FakeTensor(min(self._values))

    def max(self):
        return FakeTensor(max(self._values))


class NumpyArrayLike:
    """Wraps a numpy array without exposing ``item``, routing to the numpy branch."""

    def __init__(self, arr: np.ndarray):
        self._arr = arr

    def mean(self):
        return np.mean(self._arr)

    def __len__(self):
        return len(self._arr)

    def __array__(self, dtype=None):
        return self._arr if dtype is None else self._arr.astype(dtype)


_NUMERIC = st.one_of(
    st.floats(allow_nan=False, allow_infinity=False),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.booleans(),
)

_NUMPY_ARRAYS = arrays(
    dtype=np.float64,
    shape=st.integers(2, 100),
    elements=st.floats(-1e6, 1e6, allow_nan=False, allow_infinity=False),
)


@given(value=_NUMERIC)
def test_extract_numeric_produces_float(value):
    """Numeric inputs always produce float values."""
    metrics: dict[str, float] = {}
    _extract_from_value("x", value, metrics)
    assert all(isinstance(v, float) for v in metrics.values())


@given(name=st.text(min_size=1))
def test_extract_none_produces_empty(name):
    """None input produces empty dict for arbitrary metric names."""
    metrics: dict[str, float] = {}
    _extract_from_value(name, None, metrics)
    assert metrics == {}


@given(arr=_NUMPY_ARRAYS)
def test_extract_numpy_array_produces_statistics(arr):
    """Multi-element arrays routed to numpy branch produce 4 statistic keys."""
    metrics: dict[str, float] = {}
    _extract_from_value("x", NumpyArrayLike(arr), metrics)
    assert len(metrics) == 4
    suffixes = {k.split("/", 1)[1] for k in metrics}
    assert suffixes == {"mean", "std", "min", "max"}
    assert all(isinstance(v, float) for v in metrics.values())


@given(value=st.floats(allow_nan=False, allow_infinity=False).map(lambda x: [x]))
def test_extract_single_element_sequence(value):
    """Single-element lists produce exactly one key."""
    metrics: dict[str, float] = {}
    _extract_from_value("x", value, metrics)
    assert len(metrics) == 1
    assert "x" in metrics
    assert isinstance(metrics["x"], float)


@given(value=st.floats(allow_nan=False, allow_infinity=False).map(lambda x: FakeTensor(x, values=[x])))
def test_extract_tensor_scalar_produces_single(value):
    """Scalar tensor (numel=1) produces exactly one key."""
    metrics: dict[str, float] = {}
    _extract_from_value("x", value, metrics)
    assert len(metrics) == 1
    assert "x" in metrics
    assert isinstance(metrics["x"], float)


@given(
    values=st.lists(
        st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
        min_size=2,
        max_size=50,
    )
)
def test_extract_tensor_array_produces_statistics(values):
    """Multi-element tensors produce four statistic keys with valid ordering."""
    tensor = FakeTensor(values[0], numel_val=len(values), values=values)
    metrics: dict[str, float] = {}
    _extract_from_value("t", tensor, metrics)
    assert set(metrics) == {"t/mean", "t/std", "t/min", "t/max"}
    assert metrics["t/min"] <= metrics["t/mean"] + 1e-9
    assert metrics["t/mean"] <= metrics["t/max"] + 1e-9
    assert all(isinstance(v, float) for v in metrics.values())


class RuntimeErrorTensor:
    """Tensor-like object whose .item() raises RuntimeError (zero-element tensor)."""

    def __init__(self, numel_val: int = 0):
        self._numel = numel_val

    def item(self):
        raise RuntimeError("a]Tensor with 0 elements cannot be converted to scalar")

    def numel(self):
        return self._numel


@given(name=st.text(min_size=1))
def test_extract_runtime_error_is_handled(name):
    """RuntimeError from .item() is caught — produces empty metrics dict."""
    metrics: dict[str, float] = {}
    _extract_from_value(name, RuntimeErrorTensor(numel_val=1), metrics)
    assert metrics == {}
