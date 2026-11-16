"""Feature statistics for release metadata: validated episode summaries and count-weighted aggregation.

NumPy is imported inside each helper so that planning and core imports stay free of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from physical_ai_curation.release.media import ReleasePlanError

MOMENT_KEYS = ("min", "max", "mean", "std", "count")
QUANTILE_LEVELS = (("q01", 0.01), ("q10", 0.10), ("q50", 0.50), ("q90", 0.90), ("q99", 0.99))
QUANTILE_KEYS = tuple(key for key, _ in QUANTILE_LEVELS)
IMAGE_SHAPE = (3, 1, 1)
AGGREGATION_ALGORITHM = "count-weighted-population-moments-v1"
QUANTILE_APPROXIMATION = "quantiles are count-weighted averages of per-episode quantiles, not exact pooled quantiles"


@dataclass(frozen=True, slots=True)
class StatisticsResult:
    values: dict[str, object]
    algorithm: str
    approximation: str | None


def numeric_feature_statistics(values: object) -> dict[str, object]:
    import numpy as np

    try:
        raw = np.asarray(values)
    except (TypeError, ValueError) as error:
        raise ReleasePlanError("feature values must be a rectangular numeric array") from error
    if raw.dtype.kind not in "iuf" or raw.ndim == 0 or raw.shape[0] < 1:
        raise ReleasePlanError("feature values must be numeric with a nonempty leading sample axis")
    samples = raw.astype(np.float64)
    if samples.ndim == 1:
        samples = samples.reshape(-1, 1)
    if not np.isfinite(samples).all():
        raise ReleasePlanError("feature values must be finite")
    summary = {
        "min": np.min(samples, axis=0),
        "max": np.max(samples, axis=0),
        "mean": np.mean(samples, axis=0),
        "std": np.std(samples, axis=0),
    }
    for key, level in QUANTILE_LEVELS:
        summary[key] = np.quantile(samples, level, axis=0)
    result = {key: value.tolist() for key, value in summary.items()}
    result["count"] = [int(samples.shape[0])]
    _validated_arrays(result, expected_count=int(samples.shape[0]))
    return result


def validate_feature_statistics(summary: Mapping[str, object], *, expected_count: int | None) -> None:
    _validated_arrays(summary, expected_count=expected_count)


def aggregate_feature_statistics(
    summaries: Sequence[Mapping[str, object]], *, expected_counts: Sequence[int]
) -> StatisticsResult:
    import numpy as np

    if not summaries:
        raise ReleasePlanError("at least one episode summary is required")
    if len(expected_counts) != len(summaries):
        raise ReleasePlanError("expected_counts must provide one count per summary")
    validated = [
        _validated_arrays(summary, expected_count=count)
        for summary, count in zip(summaries, expected_counts, strict=True)
    ]
    keys = set(validated[0])
    if any(set(arrays) != keys for arrays in validated):
        raise ReleasePlanError("episode summaries must share one statistic key set")
    if len({arrays["mean"].shape for arrays in validated}) != 1:
        raise ReleasePlanError("episode summaries must share one feature shape")

    counts = np.array([arrays["count"][0] for arrays in validated], dtype=np.float64)
    total = counts.sum()
    weights = counts.reshape((-1,) + (1,) * validated[0]["mean"].ndim)
    stack = {key: np.stack([arrays[key] for arrays in validated]) for key in keys - {"count"}}
    mean = (weights * stack["mean"]).sum(axis=0) / total
    with np.errstate(over="ignore", invalid="ignore"):
        variance = (weights * (stack["std"] ** 2 + (stack["mean"] - mean) ** 2)).sum(axis=0) / total
    values = {
        "min": stack["min"].min(axis=0).tolist(),
        "max": stack["max"].max(axis=0).tolist(),
        "mean": mean.tolist(),
        "std": np.sqrt(variance).tolist(),
        "count": [int(total)],
    }
    if set(QUANTILE_KEYS) <= keys:
        for key in QUANTILE_KEYS:
            values[key] = ((weights * stack[key]).sum(axis=0) / total).tolist()
        approximation = QUANTILE_APPROXIMATION
    else:
        approximation = None
    _validated_arrays(values, expected_count=sum(int(summary["count"][0]) for summary in summaries))
    return StatisticsResult(values=values, algorithm=AGGREGATION_ALGORITHM, approximation=approximation)


def _strictly_above(low: Any, high: Any) -> bool:
    import numpy as np

    return bool(((low > high) & ~np.isclose(low, high, rtol=1e-12, atol=1e-15)).any())


def _numeric_array(key: str, value: object, *, integer: bool = False) -> Any:
    import numpy as np

    try:
        array = np.asarray(value)
    except (TypeError, ValueError) as error:
        raise ReleasePlanError(f"statistic {key} must be a rectangular numeric array") from error
    if array.dtype.kind not in ("iu" if integer else "iuf"):
        raise ReleasePlanError(f"statistic {key} must contain {'integers' if integer else 'numbers'}")
    array = array.astype(np.float64)
    if not np.isfinite(array).all():
        raise ReleasePlanError(f"statistic {key} must be finite")
    return array


def _validated_arrays(summary: Mapping[str, object], *, expected_count: int | None) -> dict[str, Any]:
    if expected_count is not None and (type(expected_count) is not int or expected_count < 1):
        raise ReleasePlanError("expected_count must be a positive integer or None")
    keys = set(summary)
    if keys not in (set(MOMENT_KEYS), set(MOMENT_KEYS) | set(QUANTILE_KEYS)):
        raise ReleasePlanError(f"statistic keys must be moments with optional complete quantiles, got {sorted(keys)}")
    arrays = {key: _numeric_array(key, summary[key], integer=key == "count") for key in summary}
    count = arrays["count"]
    if count.shape != (1,) or count[0] < 1 or count[0] > 2**53:
        raise ReleasePlanError("count must be a single positive integer [N]")
    if expected_count is not None and int(count[0]) != expected_count:
        raise ReleasePlanError(f"count {int(count[0])} does not match expected episode count {expected_count}")
    shape = arrays["mean"].shape
    if shape != IMAGE_SHAPE and not (len(shape) == 1 and shape[0] >= 1):
        raise ReleasePlanError(f"feature shape {shape} must be [1], [D], or image {IMAGE_SHAPE}")
    if any(arrays[key].shape != shape for key in keys - {"count"}):
        raise ReleasePlanError("statistic arrays must share one shape")
    if (arrays["std"] < 0).any():
        raise ReleasePlanError("std must be nonnegative")
    if _strictly_above(arrays["min"], arrays["mean"]) or _strictly_above(arrays["mean"], arrays["max"]):
        raise ReleasePlanError("min, mean, and max must be ordered")
    if set(QUANTILE_KEYS) <= keys:
        levels = [arrays[key] for key in QUANTILE_KEYS]
        if any(_strictly_above(low, high) for low, high in pairwise(levels)):
            raise ReleasePlanError("quantiles must be nondecreasing")
        if any(_strictly_above(arrays["min"], level) or _strictly_above(level, arrays["max"]) for level in levels):
            raise ReleasePlanError("quantiles must lie within min and max")
    return arrays
