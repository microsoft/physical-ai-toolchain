# cspell:ignore frompyfunc prec
"""Shared numerical primitives for trajectory metrics and anomaly detection."""

from __future__ import annotations

from decimal import Decimal, localcontext

import numpy as np
from numpy.typing import NDArray

type MetricArray = NDArray[np.float64] | NDArray[np.object_]

_FLOAT_MAX = np.finfo(np.float64).max


def decimal_array(values: MetricArray) -> NDArray[np.object_]:
    """Preserve existing decimals and convert binary floats without clipping."""
    return np.frompyfunc(lambda value: value if isinstance(value, Decimal) else Decimal(float(value)), 1, 1)(values)


def time_deltas(timestamps: NDArray[np.float64]) -> MetricArray:
    """Compute intervals, retaining the established nonpositive-interval fallback."""
    try:
        with np.errstate(over="raise", invalid="raise"):
            intervals = np.diff(timestamps)
            return np.where(intervals > 0, intervals, 1e-6)
    except FloatingPointError:
        with localcontext() as context:
            context.prec = 34
            intervals = np.diff(decimal_array(timestamps))
            return np.where(intervals > 0, intervals, Decimal("0.000001"))


def differentiate(values: MetricArray, intervals: MetricArray) -> MetricArray:
    """Subtract before dividing, widening only unsafe float arithmetic."""
    if values.dtype != object and intervals.dtype != object:
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                return np.diff(values, axis=0) / intervals[:, np.newaxis]
        except FloatingPointError:
            pass
    with localcontext() as context:
        context.prec = 34
        return np.diff(decimal_array(values), axis=0) / decimal_array(intervals)[:, np.newaxis]


def decimal_row_norms(values: MetricArray) -> NDArray[np.object_]:
    """Return row-scaled norms with enough exponent range for finite derivatives."""
    with localcontext() as context:
        context.prec = 34
        norms = []
        for row in values:
            scale = max(abs(value) for value in row)
            norm = 0.0 if scale == 0 else float(np.linalg.norm(np.asarray(row / scale, dtype=np.float64)))
            norms.append(Decimal(scale) * Decimal(norm))
        return np.asarray(norms, dtype=object)


def float_row_norms(values: NDArray[np.float64]) -> NDArray[np.float64] | None:
    """Return vectorized row norms, or None when float64 cannot represent them exactly enough."""
    if values.size == 0 or not np.all(np.isfinite(values)):
        return None
    scale = np.max(np.abs(values), axis=1)
    if np.any(scale > _FLOAT_MAX / np.sqrt(values.shape[1])):
        return None
    unit_scale = np.where(scale == 0, 1.0, scale)
    scaled = values / unit_scale[:, np.newaxis]
    return scale * np.sqrt(np.sum(scaled * scaled, axis=1))


def row_norms(values: MetricArray) -> MetricArray:
    """Return float64 row norms for ordinary inputs and Decimal row norms otherwise."""
    norms = float_row_norms(values) if values.dtype == np.float64 else None
    return decimal_row_norms(values) if norms is None else norms
