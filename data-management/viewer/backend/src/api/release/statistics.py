"""Generate deterministic descriptive statistics from staged release bytes."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path

from pydantic import JsonValue

from ..models.releases import (
    ReleaseEpisodeStatistics,
    ReleaseManifest,
    ReleaseStatistics,
    ReleaseStatisticsProfile,
    canonical_json_bytes,
)
from ..services.lerobot_loader import LeRobotLoader
from ..services.trajectory_analysis import TrajectoryAnalyzer

RELEASE_STATISTICS_PATH = "metadata/release-statistics.json"


def generate_release_statistics(
    package_root: Path,
    feature_schema: dict[str, JsonValue],
    *,
    episode_count: int,
) -> ReleaseStatistics:
    """Analyze every episode by reading the staged LeRobot v3 package."""
    profile = ReleaseStatisticsProfile()
    analyzer = TrajectoryAnalyzer(
        velocity_threshold=profile.velocity_threshold,
        hesitation_min_frames=profile.hesitation_min_frames,
        jitter_frequency_threshold=profile.jitter_frequency_threshold,
        smoothness_mode=profile.smoothness_mode,
    )
    loader = LeRobotLoader(package_root)
    episodes = []
    for release_episode_index in range(episode_count):
        episode = loader.load_episode(release_episode_index)
        metrics = analyzer.analyze(episode.joint_positions, episode.timestamps)
        duration_seconds = float(episode.timestamps[-1] - episode.timestamps[0]) if episode.length > 1 else 0.0
        episodes.append(
            ReleaseEpisodeStatistics(
                release_episode_index=release_episode_index,
                frame_count=episode.length,
                duration_seconds=max(0.0, duration_seconds),
                smoothness=metrics.smoothness,
                normalized_smoothness=metrics.normalized_smoothness,
                efficiency=metrics.efficiency,
                jitter=metrics.jitter,
                hesitation_count=metrics.hesitation_count,
                correction_count=metrics.correction_count,
                overall_score=metrics.overall_score,
                flags=tuple(metrics.flags),
            )
        )
    return ReleaseStatistics(
        statistics_profile=profile,
        tool_versions={"numpy": version("numpy"), "scipy": version("scipy")},
        feature_schema_sha256=_feature_schema_sha256(feature_schema),
        episodes=tuple(episodes),
    )


def write_release_statistics(package_root: Path, manifest: ReleaseManifest) -> None:
    """Write canonical release statistics before integrity finalization."""
    statistics = generate_release_statistics(
        package_root,
        manifest.feature_schema,
        episode_count=manifest.episode_count,
    )
    path = package_root / RELEASE_STATISTICS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(statistics))


def _feature_schema_sha256(feature_schema: dict[str, JsonValue]) -> str:
    payload = json.dumps(
        feature_schema,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()
