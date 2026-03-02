"""
Annotation service for managing episode annotations.

Provides CRUD operations for annotations and aggregation logic
for annotation summaries.
"""

from collections import defaultdict

from ..models.annotations import (
    AnnotationSummary,
    AutoQualityAnalysis,
    ComputedQualityMetrics,
    EpisodeAnnotation,
    EpisodeAnnotationFile,
    TrajectoryFlag,
)
from ..models.datasources import EpisodeData
from ..storage import LocalStorageAdapter


class AnnotationService:
    """
    Service for annotation CRUD and aggregation operations.

    Handles saving, retrieving, and summarizing episode annotations
    across storage backends.
    """

    def __init__(self, base_path: str = "./data"):
        """
        Initialize the annotation service.

        Args:
            base_path: Base path for annotation storage.
        """
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

    async def save_annotation(
        self, dataset_id: str, episode_idx: int, annotation: EpisodeAnnotation
    ) -> EpisodeAnnotationFile:
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
        annotation_file = await self._storage.get_annotation(dataset_id, episode_idx)
        if annotation_file is None:
            annotation_file = EpisodeAnnotationFile(
                episode_index=episode_idx,
                dataset_id=dataset_id,
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
        await self._storage.save_annotation(dataset_id, episode_idx, annotation_file)
        return annotation_file

    async def delete_annotation(self, dataset_id: str, episode_idx: int, annotator_id: str | None = None) -> bool:
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
        if annotator_id is None:
            # Delete entire annotation file
            return await self._storage.delete_annotation(dataset_id, episode_idx)

        # Remove specific annotator's contribution
        annotation_file = await self._storage.get_annotation(dataset_id, episode_idx)
        if annotation_file is None:
            return False

        original_count = len(annotation_file.annotations)
        annotation_file.annotations = [a for a in annotation_file.annotations if a.annotator_id != annotator_id]

        if len(annotation_file.annotations) == original_count:
            return False  # Annotator not found

        if len(annotation_file.annotations) == 0:
            # No annotations left, delete file
            return await self._storage.delete_annotation(dataset_id, episode_idx)

        # Save updated file
        await self._storage.save_annotation(dataset_id, episode_idx, annotation_file)
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

    Returns:
        AnnotationService singleton.
    """
    global _annotation_service
    if _annotation_service is None:
        _annotation_service = AnnotationService()
    return _annotation_service
