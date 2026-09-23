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
            self._frame_check(snapshot),
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
        if timestamps is None or len(timestamps) != snapshot.frame_count:
            reasons.append("timestamps-missing")
        else:
            if not np.isfinite(timestamps).all():
                reasons.append("timestamps-non-finite")
            deltas = np.diff(timestamps)
            if len(deltas) and not (deltas > 0).all():
                reasons.append("timestamps-not-monotonic")
            expected_delta = 1.0 / profile.fps
            if len(deltas) and not np.allclose(
                deltas,
                expected_delta,
                atol=profile.timestamp_tolerance_seconds,
                rtol=0,
            ):
                reasons.append("timestamp-delta-out-of-tolerance")
        return _result(
            "timestamps.valid",
            required=True,
            reasons=reasons,
            thresholds={"fps": profile.fps, "tolerance_seconds": profile.timestamp_tolerance_seconds},
        )

    @staticmethod
    def _stream_check(snapshot: SourceSnapshot, profile: QualityProfile) -> QualityCheckResult:
        reasons: list[str] = []
        missing_optional: list[str] = []
        declared_features = snapshot.metadata.get("features", {})
        for requirement in profile.required_features:
            if requirement.dtype == "video":
                declaration = declared_features.get(requirement.name)
                if declaration is None:
                    reasons.append("required-feature-missing")
                    continue
                if declaration.get("dtype") != requirement.dtype:
                    reasons.append("feature-dtype-mismatch")
                if tuple(declaration.get("shape", ())) != requirement.shape:
                    reasons.append("feature-shape-mismatch")
                continue
            observation = snapshot.features.get(requirement.name)
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
            if requirement.name not in snapshot.features and requirement.name not in declared_features:
                missing_optional.append(requirement.name)
        return _result(
            "streams.complete",
            required=True,
            reasons=reasons,
            measurements={"frame_count": snapshot.frame_count, "missing_optional": missing_optional},
        )

    @staticmethod
    def _frame_check(snapshot: SourceSnapshot) -> QualityCheckResult:
        reasons: list[str] = []
        if snapshot.frame_indices is None or len(snapshot.frame_indices) != snapshot.frame_count:
            reasons.append("frame-index-missing")
        elif not np.array_equal(snapshot.frame_indices, np.arange(snapshot.frame_count)):
            reasons.append("frame-index-gap")
        return _result("frames.contiguous", required=True, reasons=reasons)

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
        return _result("calibration.valid", required=True, reasons=reasons)

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
        return _result("metadata.consistent", required=True, reasons=reasons)

    @staticmethod
    def _label_check(snapshot: SourceSnapshot, profile: QualityProfile) -> QualityCheckResult:
        reasons = ["task-label-missing"] if profile.require_task_label and not snapshot.task_labels else []
        return _result("labels.valid", required=profile.require_task_label, reasons=reasons)

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
