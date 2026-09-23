"""Behavior tests for durable release jobs and transitions."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.api.release.jobs import JobConflictError, JobState, ReleaseJobStore


def test_given_job_when_advanced_then_validated_transitions_and_events_are_persisted(tmp_path: Path) -> None:
    # Arrange
    store = ReleaseJobStore(tmp_path)
    job = store.submit(
        idempotency_key="key-1",
        release_id="release-1",
        actor_id="publisher",
        request={"dataset_id": "sample-dataset", "episodes": [2, 1]},
    )

    # Act
    for state in (JobState.RUNNING, JobState.VERIFYING, JobState.PUBLISHING, JobState.SUCCEEDED):
        job = store.transition(job.job_id, state)

    # Assert
    assert job.state is JobState.SUCCEEDED
    assert (tmp_path / "sample-dataset" / job.job_id / "job.json").is_file()
    assert (tmp_path / "sample-dataset" / job.job_id / "events.jsonl").is_file()
    assert [event.status for event in store.list_events(job.job_id)] == [
        "queued",
        "running",
        "verifying",
        "publishing",
        "succeeded",
    ]
    with pytest.raises(ValueError, match="transition"):
        store.transition(job.job_id, JobState.RUNNING)


def test_given_duplicate_submission_when_reused_then_same_request_reuses_and_changed_input_conflicts(
    tmp_path: Path,
) -> None:
    # Arrange
    store = ReleaseJobStore(tmp_path)
    first = store.submit(
        idempotency_key="key-1",
        release_id="release-1",
        actor_id="publisher",
        request={"dataset_id": "sample-dataset", "options": {"fps": 10}, "episodes": [1, 2]},
    )

    # Act
    reused = store.submit(
        idempotency_key="key-1",
        release_id="release-1",
        actor_id="publisher",
        request={"dataset_id": "sample-dataset", "episodes": [1, 2], "options": {"fps": 10}},
    )

    # Assert
    assert reused.job_id == first.job_id
    with pytest.raises(JobConflictError, match="idempotency key"):
        store.submit(
            idempotency_key="key-1",
            release_id="release-1",
            actor_id="publisher",
            request={"dataset_id": "sample-dataset", "episodes": [3]},
        )
    with pytest.raises(JobConflictError, match="release ID"):
        store.submit(
            idempotency_key="key-2",
            release_id="release-1",
            actor_id="publisher",
            request={"dataset_id": "sample-dataset", "episodes": [1, 2]},
        )


def test_given_restart_when_reconciled_then_published_succeeds_and_safe_staging_requeues(tmp_path: Path) -> None:
    # Arrange
    store = ReleaseJobStore(tmp_path / "jobs")
    published = store.submit(
        idempotency_key="published-key",
        release_id="published-release",
        actor_id="publisher",
        request={"dataset_id": "sample-dataset", "episodes": [1]},
    )
    staged = store.submit(
        idempotency_key="staged-key",
        release_id="staged-release",
        actor_id="publisher",
        request={"dataset_id": "sample-dataset", "episodes": [2]},
    )
    store.transition(published.job_id, JobState.RUNNING)
    store.transition(published.job_id, JobState.VERIFYING)
    store.transition(published.job_id, JobState.PUBLISHING)
    store.transition(staged.job_id, JobState.RUNNING)
    staging_root = tmp_path / "staging"
    (staging_root / "sample-dataset" / staged.job_id).mkdir(parents=True)

    # Act
    reopened = ReleaseJobStore(tmp_path / "jobs")
    reconciled = reopened.reconcile(
        staging_root=staging_root,
        is_published=lambda dataset_id, release_id: (
            dataset_id == "sample-dataset" and release_id == "published-release"
        ),
    )

    # Assert
    assert {job.release_id: job.state for job in reconciled} == {
        "published-release": JobState.SUCCEEDED,
        "staged-release": JobState.QUEUED,
    }


def test_given_cancellation_when_checkpointed_then_publication_is_not_entered(tmp_path: Path) -> None:
    # Arrange
    store = ReleaseJobStore(tmp_path)
    job = store.submit(
        idempotency_key="key-1",
        release_id="release-1",
        actor_id="publisher",
        request={"dataset_id": "sample-dataset", "episodes": [1]},
    )
    store.transition(job.job_id, JobState.RUNNING)
    store.request_cancellation(job.job_id)

    # Act
    cancelled = store.cancellation_checkpoint(job.job_id)

    # Assert
    assert cancelled is True
    assert store.get(job.job_id).state is JobState.CANCELLED
    with pytest.raises(ValueError, match="transition"):
        store.transition(job.job_id, JobState.PUBLISHING)
