"""
Annotation service for managing episode annotations.

Provides CRUD operations for annotations and aggregation logic
for annotation summaries.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

from pydantic import JsonValue

from ..models.annotations import (
    AnnotationSummary,
    AutoQualityAnalysis,
    ComputedQualityMetrics,
    EpisodeAnnotation,
    EpisodeAnnotationFile,
    TrajectoryFlag,
)
from ..models.contributions import ContributionLedger, MachineOrigin
from ..models.datasources import EpisodeData
from ..models.episode_edits import EpisodeEditOperations, SavedEpisodeEdits
from ..storage import LocalStorageAdapter, RevisionConflictError, StorageAdapter, StorageError, VersionedValue
from ..storage.paths import edit_resource_scope

logger = logging.getLogger(__name__)


def _annotation_fields(value: JsonValue, prefix: str = "") -> dict[str, JsonValue]:
    if prefix.endswith("/subtask_instructions") and isinstance(value, list):
        return {f"{prefix}/{item['id']}": item["text"] for item in value}
    if not isinstance(value, dict):
        return {prefix: value}
    fields = {}
    for key, child in value.items():
        fields.update(_annotation_fields(child, f"{prefix}/{key}" if prefix else key))
    return fields


class EditSourceChangedError(ValueError):
    """Saved edits belong to an older source generation."""

    def __init__(self, etag: str | None) -> None:
        super().__init__("Saved edit source revision changed")
        self.etag = etag


class AnnotationService:
    """
    Service for annotation CRUD and aggregation operations.

    Handles saving, retrieving, and summarizing episode annotations
    across storage backends.
    """

    def __init__(self, base_path: str = "./data", storage_adapter: StorageAdapter | None = None):
        """
        Initialize the annotation service.

        Args:
            base_path: Base path for local annotation storage. Ignored when
                       storage_adapter is provided.
            storage_adapter: Pre-configured storage adapter. When provided,
                             base_path is ignored and this adapter is used directly.
                             Supports LocalStorageAdapter or AzureBlobStorageAdapter.
        """
        if storage_adapter is not None:
            self._storage = storage_adapter
        else:
            self._storage = LocalStorageAdapter(base_path)

    async def get_annotation(self, dataset_id: str, episode_idx: int) -> EpisodeAnnotationFile | None:
        """
        Get annotations for an episode.

        Args:
            dataset_id: Dataset identifier.
            episode_idx: Episode index.

        Returns:
            EpisodeAnnotationFile if annotations exist, None otherwise.
        """
        return await self._storage.get_annotation(dataset_id, episode_idx)

    async def get_annotation_versioned(
        self,
        dataset_id: str,
        episode_idx: int,
    ) -> VersionedValue[EpisodeAnnotationFile]:
        """Get an annotation resource with its strong validator."""
        return await self._storage.get_annotation_versioned(dataset_id, episode_idx)

    async def get_saved_edits(
        self,
        dataset_id: str,
        episode_idx: int,
        *,
        author_id: str,
        source_id: str,
        source_revision: str,
    ) -> VersionedValue[SavedEpisodeEdits]:
        """Read one source/author descriptor with its independent resource revision."""
        logger.debug(
            "Reading saved edits dataset=%s episode=%d",
            dataset_id.replace("\r", "").replace("\n", ""),
            int(episode_idx),
        )
        try:
            current = await self._storage.get_annotation_versioned(
                dataset_id,
                episode_idx,
                resource_scope=edit_resource_scope(source_id, author_id),
            )
        except StorageError as error:
            logger.error("Saved edit read failed: %s", type(error).__name__)
            raise
        descriptor = current.value.saved_edits.get(source_id, {}).get(author_id) if current.value else None
        if descriptor is not None:
            if (descriptor.dataset_id, descriptor.episode_index, descriptor.source_id, descriptor.author_id) != (
                dataset_id,
                episode_idx,
                source_id,
                author_id,
            ):
                logger.warning("Saved edit scope mismatch")
                raise ValueError("Saved edit scope mismatch")
            if descriptor.source_revision != source_revision:
                logger.warning("Saved edit source revision changed")
                raise EditSourceChangedError(current.etag)
        return VersionedValue(value=descriptor, etag=current.etag)

    async def save_edits(
        self,
        dataset_id: str,
        episode_idx: int,
        operations: EpisodeEditOperations,
        *,
        author_id: str,
        source_id: str,
        source_revision: str,
        frame_count: int,
        cameras: set[str],
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> VersionedValue[SavedEpisodeEdits]:
        """Conditionally replace one descriptor, retaining every other contribution."""
        if (if_match is None and not if_none_match) or (if_match is not None and if_none_match):
            raise ValueError("Exactly one revision precondition is required")
        if operations.datasetId != dataset_id or operations.episodeIndex != episode_idx:
            raise ValueError("Edit operation identity does not match the resource")
        try:
            operations.validate_context(frame_count, cameras)
        except ValueError:
            logger.warning("Invalid saved edit operations")
            raise
        descriptor = SavedEpisodeEdits(
            dataset_id=dataset_id,
            episode_index=episode_idx,
            source_id=source_id,
            source_revision=source_revision,
            author_id=author_id,
            operations=operations,
            updated_at=datetime.now(UTC),
        )
        try:
            scope = edit_resource_scope(source_id, author_id)
            current = await self._storage.get_annotation_versioned(dataset_id, episode_idx, resource_scope=scope)
            envelope = current.value or EpisodeAnnotationFile(dataset_id=dataset_id, episode_index=episode_idx)
            envelope.saved_edits.setdefault(source_id, {})[author_id] = descriptor
            etag = await self._storage.save_annotation(
                dataset_id,
                episode_idx,
                envelope,
                resource_scope=scope,
                if_match=if_match,
                if_none_match=if_none_match,
            )
        except RevisionConflictError:
            logger.warning("Saved edit revision conflict")
            raise
        except StorageError as error:
            logger.error("Saved edit write failed: %s", type(error).__name__)
            raise
        logger.info(
            "Saved edits dataset=%s episode=%d",
            dataset_id.replace("\r", "").replace("\n", ""),
            int(episode_idx),
        )
        return VersionedValue(value=descriptor, etag=etag)

    async def save_annotation(
        self,
        dataset_id: str,
        episode_idx: int,
        annotation: EpisodeAnnotation,
        *,
        machine_origin: MachineOrigin | None = None,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> VersionedValue[EpisodeAnnotationFile]:
        """
        Save or update an annotation for an episode.

        If the annotator already has an annotation for this episode,
        it will be replaced. Otherwise, a new annotation is added.

        Args:
            dataset_id: Dataset identifier.
            episode_idx: Episode index.
            annotation: Annotation to save.

        Returns:
            Updated EpisodeAnnotationFile.
        """
        # Get existing annotation file or create new one
        current = await self._storage.get_annotation_versioned(dataset_id, episode_idx)
        annotation_file = current.value
        if annotation_file is None:
            annotation_file = EpisodeAnnotationFile(
                episode_index=episode_idx,
                dataset_id=dataset_id,
            )

        previous = next(
            (item for item in annotation_file.annotations if item.annotator_id == annotation.annotator_id), None
        )
        excluded = {"annotator_id", "timestamp"}
        previous_fields = _annotation_fields(previous.model_dump(mode="json", exclude=excluded)) if previous else {}
        proposed_fields = _annotation_fields(annotation.model_dump(mode="json", exclude=excluded))
        ledger = annotation_file.provenance.setdefault(annotation.annotator_id, ContributionLedger())
        adopted_fields = {
            "language_instruction/instruction"
            if field == "instruction"
            else f"language_instruction/subtask_instructions/{field.removeprefix('subtasks/')}": origin
            for field, origin in annotation.instruction_adoption.items()
        }
        if adopted_fields and (machine_origin is not None or not adopted_fields.keys() <= proposed_fields.keys()):
            logger.warning("Rejected invalid instruction adoption scope")
            raise ValueError("Invalid instruction adoption scope")
        if machine_origin is not None and any(
            ledger.resolve(field, human_author_id=annotation.annotator_id).origin == "human"
            for field in previous_fields.keys() - proposed_fields.keys()
        ):
            logger.warning("Rejected machine proposal removing human-authored annotation fields")
            raise ValueError("Machine proposal would remove human-authored fields")
        ledger.record_changes(
            {field: value for field, value in previous_fields.items() if field not in adopted_fields},
            {field: value for field, value in proposed_fields.items() if field not in adopted_fields},
            author_id=annotation.annotator_id,
            machine_origin=machine_origin,
        )
        for field, origin in adopted_fields.items():
            ledger.record_changes(
                {field: previous_fields[field]} if field in previous_fields else {},
                {field: proposed_fields[field]},
                author_id=annotation.annotator_id,
                origin=origin,
            )
            ledger.accept(ledger.contributions[-1].id, annotation.annotator_id)
        annotation = annotation.model_copy(update={"instruction_adoption": {}})
        if machine_origin is not None or adopted_fields:
            effective = annotation.model_dump(mode="json")
            for field in sorted(proposed_fields, key=lambda name: (name.count("/"), name)):
                resolved = ledger.resolve(field, human_author_id=annotation.annotator_id)
                target = effective
                parts = field.split("/")
                if len(parts) == 3 and parts[1] == "subtask_instructions":
                    for item in effective[parts[0]][parts[1]]:
                        if item["id"] == parts[2]:
                            item["text"] = resolved.value
                    continue
                for part in parts[:-1]:
                    if not isinstance(target.get(part), dict):
                        target[part] = {}
                    target = target[part]
                target[parts[-1]] = resolved.value
            annotation = EpisodeAnnotation.model_validate(effective)
        logger.debug(
            "Recording annotation provenance dataset=%s episode=%d origin=%s",
            dataset_id.replace("\r", "").replace("\n", ""),
            int(episode_idx),
            "machine" if machine_origin else "human",
        )

        # Find and update existing annotation from same annotator, or append
        updated = False
        for i, existing in enumerate(annotation_file.annotations):
            if existing.annotator_id == annotation.annotator_id:
                annotation_file.annotations[i] = annotation
                updated = True
                break

        if not updated:
            annotation_file.annotations.append(annotation)

        # Save updated file
        etag = await self._storage.save_annotation(
            dataset_id,
            episode_idx,
            annotation_file,
            if_match=if_match,
            if_none_match=if_none_match,
        )
        return VersionedValue(value=annotation_file, etag=etag)

    async def delete_annotation(
        self,
        dataset_id: str,
        episode_idx: int,
        annotator_id: str | None = None,
        *,
        if_match: str | None = None,
    ) -> bool:
        """
        Delete annotations for an episode.

        Args:
            dataset_id: Dataset identifier.
            episode_idx: Episode index.
            annotator_id: If provided, only delete this annotator's contribution.
                         If None, delete all annotations.

        Returns:
            True if annotations were deleted, False otherwise.
        """
        # Remove specific annotator's contribution
        annotation_file = await self._storage.get_annotation(dataset_id, episode_idx)
        if annotation_file is None:
            return False

        if annotator_id is None and not annotation_file.saved_edits:
            return await self._storage.delete_annotation(dataset_id, episode_idx, if_match=if_match)

        original_count = len(annotation_file.annotations)
        annotation_file.annotations = [
            annotation
            for annotation in annotation_file.annotations
            if annotator_id is not None and annotation.annotator_id != annotator_id
        ]

        if len(annotation_file.annotations) == original_count:
            return False  # Annotator not found

        for author, ledger in annotation_file.provenance.items():
            if annotator_id is None or author == annotator_id:
                ledger.withdraw([item.id for item in ledger.contributions])

        if len(annotation_file.annotations) == 0 and not annotation_file.saved_edits:
            # No annotations left, delete file
            return await self._storage.delete_annotation(dataset_id, episode_idx, if_match=if_match)

        # Save updated file
        await self._storage.save_annotation(dataset_id, episode_idx, annotation_file, if_match=if_match)
        return True

    async def run_auto_analysis(self, dataset_id: str, episode_idx: int, episode: EpisodeData) -> AutoQualityAnalysis:
        """
        Run automatic quality analysis on an episode.

        Analyzes trajectory data to compute quality metrics and detect
        potential issues.

        Args:
            dataset_id: Dataset identifier.
            episode_idx: Episode index.
            episode: Episode data containing trajectory information.

        Returns:
            AutoQualityAnalysis with computed metrics and suggestions.
        """
        # Compute trajectory metrics
        trajectory = episode.trajectory_data
        flags: list[TrajectoryFlag] = []

        if len(trajectory) < 2:
            # Not enough data for analysis
            return AutoQualityAnalysis(
                episode_index=episode_idx,
                computed=ComputedQualityMetrics(
                    smoothness_score=0.5,
                    efficiency_score=0.5,
                    jitter_metric=0.0,
                    hesitation_count=0,
                    correction_count=0,
                ),
                suggested_rating=3,
                confidence=0.0,
                flags=[],
            )

        # Calculate smoothness from velocity changes
        velocities = [sum(abs(v) for v in point.joint_velocities) for point in trajectory]

        # Detect jitter (high-frequency velocity changes)
        jitter_count = 0
        for i in range(1, len(velocities)):
            if abs(velocities[i] - velocities[i - 1]) > 1.0:
                jitter_count += 1

        jitter_metric = jitter_count / len(velocities) if velocities else 0.0
        if jitter_metric > 0.3:
            flags.append(TrajectoryFlag.JITTERY)

        smoothness_score = max(0.0, 1.0 - jitter_metric)

        # Detect hesitations (low velocity periods)
        hesitation_count = 0
        low_velocity_streak = 0
        for vel in velocities:
            if vel < 0.1:
                low_velocity_streak += 1
            else:
                if low_velocity_streak > 10:
                    hesitation_count += 1
                low_velocity_streak = 0

        if hesitation_count > 2:
            flags.append(TrajectoryFlag.HESITATION)

        # Detect corrections (direction reversals)
        correction_count = 0
        for i in range(2, len(trajectory)):
            prev_delta = [
                trajectory[i - 1].joint_positions[j] - trajectory[i - 2].joint_positions[j]
                for j in range(len(trajectory[i].joint_positions))
            ]
            curr_delta = [
                trajectory[i].joint_positions[j] - trajectory[i - 1].joint_positions[j]
                for j in range(len(trajectory[i].joint_positions))
            ]
            # Check for sign changes (direction reversal)
            reversals = sum(1 for p, c in zip(prev_delta, curr_delta) if p * c < 0)
            if reversals > len(prev_delta) // 2:
                correction_count += 1

        if correction_count > 5:
            flags.append(TrajectoryFlag.CORRECTION_HEAVY)

        # Calculate efficiency (path length vs. direct distance)
        # Simplified: assume efficiency based on trajectory length
        efficiency_score = max(0.0, 1.0 - (len(trajectory) / 1000.0))

        # Compute suggested rating
        avg_score = (smoothness_score + efficiency_score) / 2
        if avg_score >= 0.8:
            suggested_rating = 5
        elif avg_score >= 0.6:
            suggested_rating = 4
        elif avg_score >= 0.4:
            suggested_rating = 3
        elif avg_score >= 0.2:
            suggested_rating = 2
        else:
            suggested_rating = 1

        # Confidence based on data quality
        confidence = min(1.0, len(trajectory) / 100.0)

        return AutoQualityAnalysis(
            episode_index=episode_idx,
            computed=ComputedQualityMetrics(
                smoothness_score=smoothness_score,
                efficiency_score=efficiency_score,
                jitter_metric=jitter_metric,
                hesitation_count=hesitation_count,
                correction_count=correction_count,
            ),
            suggested_rating=suggested_rating,
            confidence=confidence,
            flags=flags,
        )

    async def get_summary(self, dataset_id: str, total_episodes: int) -> AnnotationSummary:
        """
        Get aggregated annotation metrics for a dataset.

        Args:
            dataset_id: Dataset identifier.
            total_episodes: Total number of episodes in dataset.

        Returns:
            AnnotationSummary with aggregated metrics.
        """
        # Get list of annotated episodes
        annotated_indices = await self._storage.list_annotated_episodes(dataset_id)

        # Initialize counters
        task_completeness_dist: dict[str, int] = defaultdict(int)
        quality_score_dist: dict[int, int] = defaultdict(int)
        anomaly_type_counts: dict[str, int] = defaultdict(int)

        # Aggregate metrics from each annotation
        for idx in annotated_indices:
            annotation_file = await self._storage.get_annotation(dataset_id, idx)
            if annotation_file is None:
                continue

            for annotation in annotation_file.annotations:
                # Count task completeness ratings
                rating = annotation.task_completeness.rating
                task_completeness_dist[rating] += 1

                # Count quality scores
                score = annotation.trajectory_quality.overall_score
                quality_score_dist[score] += 1

                # Count anomaly types
                for anomaly in annotation.anomalies.anomalies:
                    anomaly_type_counts[anomaly.type] += 1

        return AnnotationSummary(
            dataset_id=dataset_id,
            total_episodes=total_episodes,
            annotated_episodes=len(annotated_indices),
            task_completeness_distribution=dict(task_completeness_dist),
            quality_score_distribution=dict(quality_score_dist),
            anomaly_type_counts=dict(anomaly_type_counts),
        )


# Global service instance
_annotation_service: AnnotationService | None = None


def get_annotation_service() -> AnnotationService:
    """
    Get the global annotation service instance.

    On first call, reads application config and creates the appropriate
    storage adapter (local filesystem or Azure Blob Storage).

    Returns:
        AnnotationService singleton.
    """
    global _annotation_service
    if _annotation_service is None:
        from ..config import create_annotation_storage, get_app_config

        config = get_app_config()
        storage = create_annotation_storage(config)
        _annotation_service = AnnotationService(storage_adapter=storage)
    return _annotation_service
