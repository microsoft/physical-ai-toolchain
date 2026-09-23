"""Behavior tests for immutable local release publication."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.api.release.local_publisher import LocalReleasePublisher, PublicationConflictError


@pytest.fixture(autouse=True)
def verified_release(monkeypatch) -> None:
    monkeypatch.setattr("src.api.release.local_publisher.verify_release", lambda _root: None)


def test_given_preexisting_destination_when_published_then_conflict_does_not_overwrite(tmp_path: Path) -> None:
    # Arrange
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "payload.bin").write_bytes(b"new")
    releases = tmp_path / "releases"
    destination = releases / "sample-dataset" / "release-1"
    destination.mkdir(parents=True)
    (destination / "payload.bin").write_bytes(b"existing")

    # Act & Assert
    with pytest.raises(PublicationConflictError, match="release-1"):
        LocalReleasePublisher(releases).publish("sample-dataset", "release-1", staging, owner="job-1")
    assert (destination / "payload.bin").read_bytes() == b"existing"
    assert staging.is_dir()


def test_given_destination_appears_during_promotion_when_published_then_it_is_not_replaced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Arrange
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "payload.bin").write_bytes(b"new")
    releases = tmp_path / "releases"
    destination = releases / "sample-dataset" / "release-1"
    original_copytree = shutil.copytree

    def race_copytree(source: Path, target: Path) -> Path:
        target.mkdir()
        (target / "payload.bin").write_bytes(b"racer")
        return original_copytree(source, target)

    monkeypatch.setattr(shutil, "copytree", race_copytree)

    # Act & Assert
    with pytest.raises(PublicationConflictError, match="release-1"):
        LocalReleasePublisher(releases).publish("sample-dataset", "release-1", staging, owner="job-1")
    assert (destination / "payload.bin").read_bytes() == b"racer"
    assert staging.is_dir()


def test_given_verified_staging_when_published_then_marker_is_last_and_release_is_visible(tmp_path: Path) -> None:
    # Arrange
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "payload.bin").write_bytes(b"release")
    publisher = LocalReleasePublisher(tmp_path / "releases")

    # Act
    destination = publisher.publish("sample-dataset", "release-1", staging, owner="job-1")

    # Assert
    assert destination == tmp_path / "releases" / "sample-dataset" / "release-1"
    assert (destination / ".published.json").is_file()
    assert publisher.list_published_releases() == [("sample-dataset", "release-1")]
    assert not staging.exists()
