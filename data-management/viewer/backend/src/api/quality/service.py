"""Deterministic quality checks and report orchestration."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import numpy as np
from pydantic import JsonValue

from ..models.reviews import QualityCheckResult, QualityOutcome, QualityReport
from .adapters import SourceAdapter, SourceSnapshot
from .profiles import QualityProfile


class QualityService:
    """Run one versioned profile against a strict source adapter."""

    async def run(
        self,
        *,
        adapter: SourceAdapter,
        dataset_root: Path,
        dataset_id: str,
        episode_index: int,
        profile: QualityProfile,
        run_id: str,
        actor_id: str,
        created_at: datetime,
    ) -> QualityReport:
        snapshot = await asyncio.to_thread(adapter.inspect, dataset_root, dataset_id, episode_index, profile)
        checks = (
            self._source_check(snapshot),
            self._timestamp_check(snapshot, profile),
            self._stream_check(snapshot, profile),
            self._frame_check(snapshot, profile),
            self._calibration_check(snapshot, profile),
            self._metadata_check(snapshot, profile),
            self._label_check(snapshot, profile),
            self._readback_check(snapshot),
        )
        return QualityReport(
            run_id=run_id,
            check_set_version=profile.version,
            source=snapshot.source,
            actor_id=actor_id,
            created_at=created_at,
            episode_checks=checks,
            package_checks=(),
        )

    @staticmethod
    def _source_check(snapshot: SourceSnapshot) -> QualityCheckResult:
        return _result(
            "source.identity",
            required=True,
            reasons=(),
            measurements={"file_count": len(snapshot.source.files)},
        )

    @staticmethod
    def _timestamp_check(snapshot: SourceSnapshot, profile: QualityProfile) -> QualityCheckResult:
        reasons: list[str] = []
        timestamps = snapshot.timestamps
        finite = timestamps is not None and bool(np.isfinite(timestamps).all())
        monotonic = False
        deltas = np.asarray([], dtype=np.float64)
        if timestamps is None or len(timestamps) != snapshot.frame_count:
            reasons.append("timestamps-missing")
        else:
            if not finite:
                reasons.append("timestamps-non-finite")
            deltas = np.diff(timestamps)
            monotonic = not len(deltas) or bool((deltas > 0).all())
            if not monotonic:
                reasons.append("timestamps-not-monotonic")
            expected_delta = 1.0 / profile.fps
            if len(deltas) and not np.allclose(
                deltas,
                expected_delta,
                atol=profile.timestamp_tolerance_seconds,
                rtol=0,
            ):
                reasons.append("timestamp-delta-out-of-tolerance")
        baseline_duration = snapshot.video_observations[0].duration_seconds if snapshot.video_observations else None
        alignment_tolerance = profile.timestamp_tolerance_seconds
        if snapshot.video_observations:
            alignment_tolerance += profile.video_frame_count_tolerance / profile.fps
        stream_measurements = []
        for observation in snapshot.video_observations:
            duration_delta = (
                abs(observation.duration_seconds - baseline_duration) if baseline_duration is not None else 0.0
            )
            aligned = duration_delta <= alignment_tolerance
            if not aligned:
                reasons.append("stream-duration-out-of-tolerance")
            stream_measurements.append(
                {
                    "feature_name": observation.feature_name,
                    "start_seconds": observation.window_start_seconds,
                    "end_seconds": observation.window_end_seconds,
                    "sample_count": observation.frame_count,
                    "duration_seconds": observation.duration_seconds,
                    "duration_delta_seconds": duration_delta,
                    "aligned": aligned,
                }
            )
        return _result(
            "timestamps.valid",
            required=True,
            reasons=reasons,
            measurements={
                "sample_count": len(timestamps) if timestamps is not None else 0,
                "start_seconds": _finite_endpoint(timestamps, 0),
                "end_seconds": _finite_endpoint(timestamps, -1),
                "observed_delta_min_seconds": _finite_stat(deltas, np.min),
                "observed_delta_max_seconds": _finite_stat(deltas, np.max),
                "finite": finite,
                "monotonic": monotonic,
                "streams": stream_measurements,
            },
            thresholds={
                "expected_delta_seconds": 1.0 / profile.fps,
                "alignment_tolerance_seconds": alignment_tolerance,
            },
        )

    @staticmethod
    def _stream_check(snapshot: SourceSnapshot, profile: QualityProfile) -> QualityCheckResult:
        reasons: list[str] = []
        missing_optional: list[str] = []
        required_measurements: dict[str, JsonValue] = {}
        optional_measurements: dict[str, JsonValue] = {}
        declared_features = snapshot.metadata.get("features", {})
        for requirement in profile.required_features:
            if requirement.dtype == "video":
                declaration = declared_features.get(requirement.name)
                observation = next(
                    (item for item in snapshot.video_observations if item.feature_name == requirement.name),
                    None,
                )
                required_measurements[requirement.name] = {
                    "observed": declaration is not None,
                    "expected_dtype": requirement.dtype,
                    "observed_dtype": declaration.get("dtype") if declaration else None,
                    "expected_shape": list(requirement.shape),
                    "observed_shape": list(declaration.get("shape", ())) if declaration else None,
                    "row_count": observation.frame_count if observation else 0,
                    "null_count": 0,
                    "non_finite_count": 0,
                }
                if declaration is None:
                    reasons.append("required-feature-missing")
                    continue
                if declaration.get("dtype") != requirement.dtype:
                    reasons.append("feature-dtype-mismatch")
                if tuple(declaration.get("shape", ())) != requirement.shape:
                    reasons.append("feature-shape-mismatch")
                continue
            observation = snapshot.features.get(requirement.name)
            required_measurements[requirement.name] = _feature_measurement(requirement, observation)
            if observation is None:
                reasons.append("required-feature-missing")
                continue
            if observation.dtype != requirement.dtype:
                reasons.append("feature-dtype-mismatch")
            if observation.shape != requirement.shape:
                reasons.append("feature-shape-mismatch")
            if len(observation.values) != snapshot.frame_count:
                reasons.append("feature-row-count-mismatch")
            try:
                if not np.isfinite(observation.values).all():
                    reasons.append("feature-non-finite")
            except TypeError:
                if any(value is None for value in observation.values.flat):
                    reasons.append("feature-null")
        for requirement in profile.optional_features:
            observation = snapshot.features.get(requirement.name)
            declaration = declared_features.get(requirement.name)
            if observation is None and declaration is None:
                missing_optional.append(requirement.name)
                optional_measurements[requirement.name] = {"disposition": "missing"}
            else:
                optional_measurements[requirement.name] = {
                    "disposition": "observed",
                    **_feature_measurement(requirement, observation),
                }
        return _result(
            "streams.complete",
            required=True,
            reasons=reasons,
            measurements={
                "frame_count": snapshot.frame_count,
                "required": required_measurements,
                "optional": optional_measurements,
                "missing_optional": missing_optional,
            },
        )

    @staticmethod
    def _frame_check(snapshot: SourceSnapshot, profile: QualityProfile) -> QualityCheckResult:
        reasons: list[str] = []
        if snapshot.frame_indices is None or len(snapshot.frame_indices) != snapshot.frame_count:
            reasons.append("frame-index-missing")
        elif not np.array_equal(snapshot.frame_indices, np.arange(snapshot.frame_count)):
            reasons.append("frame-index-gap")
        declared_frames = snapshot.metadata.get("episode_frame_count")
        video_measurements = []
        for observation in snapshot.video_observations:
            video_measurements.append(
                {
                    "feature_name": observation.feature_name,
                    "frame_count": observation.frame_count,
                    "duration_seconds": observation.duration_seconds,
                    "relative_path": observation.relative_path,
                }
            )
            if (
                declared_frames is not None
                and abs(observation.frame_count - int(declared_frames)) > profile.video_frame_count_tolerance
            ):
                reasons.append("video-frame-count-mismatch")
        return _result(
            "frames.contiguous",
            required=True,
            reasons=reasons,
            measurements={
                "tabular_row_count": snapshot.frame_count,
                "frame_index_count": len(snapshot.frame_indices) if snapshot.frame_indices is not None else 0,
                "declared_frame_count": int(declared_frames) if declared_frames is not None else None,
                "videos": video_measurements,
            },
            thresholds={"video_frame_count_tolerance": profile.video_frame_count_tolerance},
        )

    @staticmethod
    def _calibration_check(snapshot: SourceSnapshot, profile: QualityProfile) -> QualityCheckResult:
        if profile.calibration is None:
            return QualityCheckResult(
                check_id="calibration.valid",
                required=False,
                outcome=QualityOutcome.NOT_APPLICABLE,
            )
        reasons: list[str] = []
        calibration = snapshot.calibration
        if calibration is None:
            reasons.append("calibration-missing")
        else:
            if calibration.get("schema_version") != profile.calibration.schema_version:
                reasons.append("calibration-schema-mismatch")
            sensors = set(calibration.get("sensors", []))
            if not set(profile.calibration.required_sensors).issubset(sensors):
                reasons.append("calibration-sensor-missing")
        return _result(
            "calibration.valid",
            required=True,
            reasons=reasons,
            measurements={
                "relative_path": profile.calibration.relative_path,
                "size_bytes": snapshot.calibration_file.size_bytes if snapshot.calibration_file else 0,
                "sha256": snapshot.calibration_file.sha256 if snapshot.calibration_file else None,
                "schema_version": calibration.get("schema_version") if calibration else None,
                "required_sensors": list(profile.calibration.required_sensors),
                "observed_sensors": sorted(str(sensor) for sensor in calibration.get("sensors", []))
                if calibration
                else [],
            },
        )

    @staticmethod
    def _metadata_check(snapshot: SourceSnapshot, profile: QualityProfile) -> QualityCheckResult:
        reasons = [
            "metadata-file-missing"
            for path in profile.required_metadata_files
            if path not in snapshot.available_artifacts
        ]
        declared_frames = snapshot.metadata.get("episode_frame_count")
        if declared_frames is not None and int(declared_frames) != snapshot.frame_count:
            reasons.append("metadata-frame-count-mismatch")
        return _result(
            "metadata.consistent",
            required=True,
            reasons=reasons,
            measurements={
                "required_artifacts": list(profile.required_metadata_files),
                "observed_artifacts": [
                    path for path in profile.required_metadata_files if path in snapshot.available_artifacts
                ],
                "observed_frame_count": snapshot.frame_count,
                "declared_frame_count": int(declared_frames) if declared_frames is not None else None,
            },
        )

    @staticmethod
    def _label_check(snapshot: SourceSnapshot, profile: QualityProfile) -> QualityCheckResult:
        reasons = ["task-label-missing"] if profile.require_task_label and not snapshot.task_labels else []
        return _result(
            "labels.valid",
            required=profile.require_task_label,
            reasons=reasons,
            measurements={
                "label_count": len(snapshot.task_labels),
                "nonempty_label_count": sum(bool(label.strip()) for label in snapshot.task_labels),
            },
        )

    @staticmethod
    def _readback_check(snapshot: SourceSnapshot) -> QualityCheckResult:
        reasons = ["format-readback-failed"] if snapshot.readback_errors else []
        return _result(
            "format.readback",
            required=True,
            reasons=reasons,
            measurements={"errors": list(snapshot.readback_errors)},
        )


def required_checks_pass(report: QualityReport) -> bool:
    """Return whether every required check in a report passed."""
    return all(check.outcome == QualityOutcome.PASS for check in report.episode_checks if check.required)


def _result(
    check_id: str,
    *,
    required: bool,
    reasons: list[str] | tuple[str, ...],
    measurements: dict[str, JsonValue] | None = None,
    thresholds: dict[str, JsonValue] | None = None,
) -> QualityCheckResult:
    return QualityCheckResult(
        check_id=check_id,
        required=required,
        outcome=QualityOutcome.FAIL if reasons else QualityOutcome.PASS,
        measurements=measurements or {},
        thresholds=thresholds or {},
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def _feature_measurement(requirement, observation) -> dict[str, JsonValue]:
    null_count = 0
    non_finite_count = 0
    if observation is not None:
        try:
            non_finite_count = int(np.size(observation.values) - np.count_nonzero(np.isfinite(observation.values)))
        except TypeError:
            null_count = sum(value is None for value in observation.values.flat)
    return {
        "observed": observation is not None,
        "expected_dtype": requirement.dtype,
        "observed_dtype": observation.dtype if observation else None,
        "expected_shape": list(requirement.shape),
        "observed_shape": list(observation.shape) if observation else None,
        "row_count": len(observation.values) if observation else 0,
        "null_count": null_count,
        "non_finite_count": non_finite_count,
    }


def _finite_endpoint(values: np.ndarray | None, index: int) -> float | None:
    if values is None or not len(values) or not np.isfinite(values[index]):
        return None
    return float(values[index])


def _finite_stat(values: np.ndarray, operation) -> float | None:
    return float(operation(values)) if len(values) and np.isfinite(values).all() else None
