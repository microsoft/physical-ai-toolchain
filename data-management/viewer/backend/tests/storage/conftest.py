"""Shared test fixtures for storage adapter tests."""

from datetime import datetime

from src.api.models.annotations import (
    AnomalyAnnotation,
    ConfidenceLevel,
    DataQualityAnnotation,
    DataQualityLevel,
    EpisodeAnnotation,
    EpisodeAnnotationFile,
    QualityScore,
    TaskCompletenessAnnotation,
    TaskCompletenessRating,
    TrajectoryQualityAnnotation,
    TrajectoryQualityMetrics,
)


def create_test_annotation(episode_index: int, user_id: str = "test-user") -> EpisodeAnnotationFile:
    """Create a test annotation file."""
    now = datetime.utcnow()
    annotation = EpisodeAnnotation(
        annotator_id=user_id,
        timestamp=now,
        task_completeness=TaskCompletenessAnnotation(
            rating=TaskCompletenessRating.SUCCESS,
            confidence=ConfidenceLevel.FIVE,
            completion_percentage=100,
        ),
        trajectory_quality=TrajectoryQualityAnnotation(
            overall_score=QualityScore.FIVE,
            metrics=TrajectoryQualityMetrics(
                smoothness=QualityScore.FIVE,
                efficiency=QualityScore.FIVE,
                safety=QualityScore.FIVE,
                precision=QualityScore.FIVE,
            ),
        ),
        data_quality=DataQualityAnnotation(overall_quality=DataQualityLevel.GOOD),
        anomalies=AnomalyAnnotation(),
        notes="Test annotation",
    )
    return EpisodeAnnotationFile(
        schema_version="1.0",
        episode_index=episode_index,
        dataset_id="test-dataset",
        annotations=[annotation],
    )
