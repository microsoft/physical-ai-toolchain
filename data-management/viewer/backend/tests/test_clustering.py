"""
Unit tests for the EpisodeClusterer service.

Covers clustering behavior with and without sklearn and result dataclasses.
"""

from __future__ import annotations

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

    def test_single_frame_trajectories_are_clustered(self, clusterer):
        pytest.importorskip("sklearn")
        trajectories = [np.full((1, 7), value, dtype=float) for value in (0.0, 0.1, 10.0, 10.1)]

        result = clusterer.cluster(trajectories, num_clusters=2)

        assert result.num_clusters == 2
        assert [assignment.episode_index for assignment in result.assignments] == list(range(4))
        assert sorted(result.cluster_sizes.values()) == [2, 2]

    def test_joints_after_seventh_do_not_affect_assignments(self, clusterer):
        pytest.importorskip("sklearn")
        trajectories = [np.full((10, 7), value, dtype=float) for value in (0.0, 0.1, 10.0, 10.1)]
        trajectories_with_extra_joints = [
            np.hstack([trajectory, np.full((10, 5), 1_000.0 + index)]) for index, trajectory in enumerate(trajectories)
        ]

        baseline = clusterer.cluster(trajectories, num_clusters=2)
        with_extra_joints = clusterer.cluster(trajectories_with_extra_joints, num_clusters=2)

        assert [assignment.cluster_id for assignment in with_extra_joints.assignments] == [
            assignment.cluster_id for assignment in baseline.assignments
        ]
        assert with_extra_joints.cluster_sizes == baseline.cluster_sizes

    def test_fallback_when_sklearn_missing(self, clusterer, synthetic_trajectories, monkeypatch):
        for mod in list(sys.modules):
            if mod == "sklearn" or mod.startswith("sklearn."):
                monkeypatch.delitem(sys.modules, mod, raising=False)

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sklearn" or name.startswith("sklearn."):
                raise ImportError(f"blocked sklearn import: {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        first = clusterer.cluster(synthetic_trajectories, num_clusters=2)
        second = clusterer.cluster(synthetic_trajectories, num_clusters=2)

        assert first.num_clusters == 2
        assert len(first.assignments) == len(synthetic_trajectories)
        assert first.silhouette_score == 0.5
        assert sum(first.cluster_sizes.values()) == len(synthetic_trajectories)
        assert [a.cluster_id for a in first.assignments] == [a.cluster_id for a in second.assignments]
        assert first.cluster_sizes == second.cluster_sizes

    def test_fallback_caps_clusters_to_trajectory_count(self, clusterer, monkeypatch):
        for mod in list(sys.modules):
            if mod == "sklearn" or mod.startswith("sklearn."):
                monkeypatch.delitem(sys.modules, mod, raising=False)

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sklearn" or name.startswith("sklearn."):
                raise ImportError(f"blocked sklearn import: {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        result = clusterer.cluster([np.zeros((5, 7)), np.ones((5, 7))], num_clusters=10)

        assert result.num_clusters == 2
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
