"""
Unit tests for the EpisodeClusterer service.

Covers feature extraction edge cases, the sklearn happy path,
the no-sklearn fallback (`_simple_clustering`), and dataclass shape.
"""

import builtins
import sys

import numpy as np
import pytest

from src.api.services.clustering import (
    ClusterAssignment,
    ClusteringResult,
    EpisodeClusterer,
)


@pytest.fixture
def clusterer():
    return EpisodeClusterer(max_clusters=5, min_cluster_size=2)


@pytest.fixture
def synthetic_trajectories():
    """Two well-separated trajectory groups in 7-DoF joint space."""
    rng = np.random.default_rng(0)
    group_a = [rng.normal(loc=0.0, scale=0.1, size=(50, 7)) for _ in range(6)]
    group_b = [rng.normal(loc=5.0, scale=0.1, size=(50, 7)) for _ in range(6)]
    return group_a + group_b


class TestExtractFeatures:
    def test_empty_trajectory_returns_zero_vector(self, clusterer):
        feats = clusterer._extract_features(np.zeros((0, 7)))
        assert feats.shape == (20,)
        assert np.all(feats == 0)

    def test_single_frame_returns_fixed_size(self, clusterer):
        feats = clusterer._extract_features(np.ones((1, 7)))
        assert feats.shape == (31,)
        # Path length and displacement are zero with single frame.
        assert feats[-3] == 0.0  # path length
        assert feats[-1] == 0.0  # displacement

    def test_seven_joint_features_populated(self, clusterer):
        traj = np.tile(np.arange(7, dtype=float), (20, 1))
        feats = clusterer._extract_features(traj)
        assert feats.shape == (31,)
        # Duration is the second-to-last entry.
        assert feats[-2] == 20.0

    def test_more_than_seven_joints_truncated(self, clusterer):
        traj = np.zeros((10, 12))
        feats = clusterer._extract_features(traj)
        assert feats.shape == (31,)


class TestCluster:
    def test_empty_input_short_circuits(self, clusterer):
        result = clusterer.cluster([])
        assert isinstance(result, ClusteringResult)
        assert result.num_clusters == 1
        assert result.assignments == []
        assert result.cluster_sizes == {0: 0}
        assert result.silhouette_score == 1.0

    def test_single_trajectory_short_circuits(self, clusterer):
        result = clusterer.cluster([np.zeros((10, 7))])
        assert result.num_clusters == 1
        assert len(result.assignments) == 1
        assert result.assignments[0].cluster_id == 0
        assert result.assignments[0].similarity_score == 1.0
        assert result.cluster_sizes == {0: 1}

    def test_multi_trajectory_with_sklearn(self, clusterer, synthetic_trajectories):
        pytest.importorskip("sklearn")
        result = clusterer.cluster(synthetic_trajectories, num_clusters=2)
        assert result.num_clusters == 2
        assert len(result.assignments) == len(synthetic_trajectories)
        assert sum(result.cluster_sizes.values()) == len(synthetic_trajectories)
        # Episode indices are unique and sorted.
        indices = [a.episode_index for a in result.assignments]
        assert indices == sorted(indices)
        assert len(set(indices)) == len(indices)
        # Similarity scores are bounded.
        for a in result.assignments:
            assert 0.0 <= a.similarity_score <= 1.0

    def test_auto_select_num_clusters(self, clusterer, synthetic_trajectories):
        pytest.importorskip("sklearn")
        result = clusterer.cluster(synthetic_trajectories)
        assert 2 <= result.num_clusters <= clusterer.max_clusters

    def test_fallback_when_sklearn_missing(self, clusterer, synthetic_trajectories, monkeypatch):
        # Block any sklearn import for the duration of the test.
        for mod in list(sys.modules):
            if mod == "sklearn" or mod.startswith("sklearn."):
                monkeypatch.delitem(sys.modules, mod, raising=False)

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sklearn" or name.startswith("sklearn."):
                raise ImportError(f"blocked sklearn import: {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        result = clusterer.cluster(synthetic_trajectories, num_clusters=2)
        assert result.num_clusters == 2
        assert len(result.assignments) == len(synthetic_trajectories)
        # Fallback assigns deterministic similarity score of 0.5.
        assert result.silhouette_score == 0.5
        assert sum(result.cluster_sizes.values()) == len(synthetic_trajectories)


class TestSimpleClustering:
    def test_simple_clustering_deterministic(self, clusterer):
        rng = np.random.default_rng(123)
        features = rng.normal(size=(20, 31))
        first = clusterer._simple_clustering(features, num_clusters=3)
        second = clusterer._simple_clustering(features, num_clusters=3)
        assert [a.cluster_id for a in first.assignments] == [a.cluster_id for a in second.assignments]
        assert first.cluster_sizes == second.cluster_sizes

    def test_simple_clustering_caps_centroids_to_sample_count(self, clusterer):
        features = np.zeros((2, 31))
        result = clusterer._simple_clustering(features, num_clusters=10)
        # Cannot have more centroids than samples.
        assert result.num_clusters <= 2
        assert sum(result.cluster_sizes.values()) == 2


class TestDataclasses:
    def test_cluster_assignment_fields(self):
        a = ClusterAssignment(episode_index=3, cluster_id=1, similarity_score=0.8)
        assert a.episode_index == 3
        assert a.cluster_id == 1
        assert a.similarity_score == 0.8

    def test_clustering_result_fields(self):
        r = ClusteringResult(num_clusters=2, assignments=[], cluster_sizes={0: 5}, silhouette_score=0.7)
        assert r.num_clusters == 2
        assert r.assignments == []
        assert r.cluster_sizes == {0: 5}
        assert r.silhouette_score == 0.7
