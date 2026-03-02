"""
Episode similarity clustering service.

Uses trajectory features and hierarchical clustering to group
similar episodes together.
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass
class ClusterAssignment:
    """Cluster assignment for an episode."""

    episode_index: int
    """Index of the episode."""

    cluster_id: int
    """Assigned cluster ID."""

    similarity_score: float
    """Similarity to cluster centroid (0-1)."""


@dataclass
class ClusteringResult:
    """Result of episode clustering."""

    num_clusters: int
    """Number of clusters found."""

    assignments: list[ClusterAssignment]
    """Cluster assignments for each episode."""

    cluster_sizes: dict[int, int]
    """Number of episodes in each cluster."""

    silhouette_score: float
    """Overall clustering quality score."""


class EpisodeClusterer:
    """
    Clusters similar episodes based on trajectory features.

    Uses hierarchical clustering with Ward linkage to group
    episodes with similar motion patterns.

    Example:
        >>> clusterer = EpisodeClusterer()
        >>> trajectories = [np.random.randn(100, 7) for _ in range(50)]
        >>> result = clusterer.cluster(trajectories)
        >>> print(f"Found {result.num_clusters} clusters")
    """

    def __init__(
        self,
        max_clusters: int = 10,
        min_cluster_size: int = 3,
    ) -> None:
        """
        Initialize the clusterer.

        Args:
            max_clusters: Maximum number of clusters to consider.
            min_cluster_size: Minimum episodes per cluster.
        """
        self.max_clusters = max_clusters
        self.min_cluster_size = min_cluster_size

    def cluster(
        self,
        trajectories: list[NDArray[np.float64]],
        num_clusters: int | None = None,
    ) -> ClusteringResult:
        """
        Cluster a list of trajectories.

        Args:
            trajectories: List of trajectory arrays, each (N, num_joints).
            num_clusters: Optional fixed number of clusters.

        Returns:
            ClusteringResult with assignments and quality metrics.
        """
        if len(trajectories) < 2:
            return ClusteringResult(
                num_clusters=1,
                assignments=[
                    ClusterAssignment(episode_index=i, cluster_id=0, similarity_score=1.0)
                    for i in range(len(trajectories))
                ],
                cluster_sizes={0: len(trajectories)},
                silhouette_score=1.0,
            )

        # Extract features from each trajectory
        features = np.array([self._extract_features(t) for t in trajectories])

        # Use sklearn if available, otherwise fall back to simple k-means
        try:
            from sklearn.cluster import AgglomerativeClustering
            from sklearn.metrics import silhouette_score
            from sklearn.preprocessing import StandardScaler

            # Normalize features
            scaler = StandardScaler()
            features_normalized = scaler.fit_transform(features)

            # Find optimal number of clusters
            if num_clusters is None:
                num_clusters = self._find_optimal_clusters(features_normalized, silhouette_score)

            # Perform clustering
            clustering = AgglomerativeClustering(
                n_clusters=num_clusters,
                linkage="ward",
            )
            labels = clustering.fit_predict(features_normalized)

            # Compute silhouette score
            sil_score = silhouette_score(features_normalized, labels) if len(set(labels)) > 1 else 1.0

            # Compute similarity scores (distance to cluster centroid)
            assignments = []
            cluster_sizes: dict[int, int] = {}

            for cluster_id in range(num_clusters):
                cluster_mask = labels == cluster_id
                cluster_features = features_normalized[cluster_mask]
                cluster_indices = np.where(cluster_mask)[0]

                if len(cluster_features) > 0:
                    centroid = np.mean(cluster_features, axis=0)
                    distances = np.linalg.norm(cluster_features - centroid, axis=1)
                    max_dist = np.max(distances) if np.max(distances) > 0 else 1.0
                    similarities = 1.0 - (distances / (max_dist + 1e-10))

                    for idx, sim in zip(cluster_indices, similarities):
                        assignments.append(
                            ClusterAssignment(
                                episode_index=int(idx),
                                cluster_id=int(cluster_id),
                                similarity_score=float(sim),
                            )
                        )

                    cluster_sizes[cluster_id] = len(cluster_indices)

            # Sort by episode index
            assignments.sort(key=lambda a: a.episode_index)

            return ClusteringResult(
                num_clusters=num_clusters,
                assignments=assignments,
                cluster_sizes=cluster_sizes,
                silhouette_score=float(sil_score),
            )

        except ImportError:
            # Fallback without sklearn
            return self._simple_clustering(features, num_clusters or 3)

    def _extract_features(self, trajectory: NDArray[np.float64]) -> NDArray[np.float64]:
        """
        Extract summary features from a trajectory.

        Features include:
        - Mean, std, min, max for each joint
        - Path length
        - Duration (in frames)
        - Total displacement
        """
        if len(trajectory) == 0:
            return np.zeros(20)  # Default feature size

        num_joints = trajectory.shape[1] if len(trajectory.shape) > 1 else 1

        features = []

        # Per-joint statistics
        for j in range(min(num_joints, 7)):  # Limit to 7 joints
            joint_data = trajectory[:, j] if len(trajectory.shape) > 1 else trajectory
            features.extend(
                [
                    np.mean(joint_data),
                    np.std(joint_data),
                    np.min(joint_data),
                    np.max(joint_data),
                ]
            )

        # Pad if fewer joints
        while len(features) < 28:
            features.append(0.0)

        # Path length
        if len(trajectory) > 1:
            path_segments = np.diff(trajectory, axis=0)
            path_length = np.sum(np.linalg.norm(path_segments, axis=1))
        else:
            path_length = 0.0
        features.append(path_length)

        # Duration
        features.append(float(len(trajectory)))

        # Total displacement
        displacement = np.linalg.norm(trajectory[-1] - trajectory[0]) if len(trajectory) > 1 else 0.0
        features.append(displacement)

        return np.array(features[:31])  # Fixed size

    def _find_optimal_clusters(
        self,
        features: NDArray[np.float64],
        silhouette_score_fn,
    ) -> int:
        """
        Find optimal number of clusters using silhouette score.
        """
        from sklearn.cluster import AgglomerativeClustering

        best_score = -1.0
        best_k = 2

        max_k = min(self.max_clusters, len(features) - 1)

        for k in range(2, max_k + 1):
            clustering = AgglomerativeClustering(n_clusters=k, linkage="ward")
            labels = clustering.fit_predict(features)

            if len(set(labels)) > 1:
                score = silhouette_score_fn(features, labels)
                if score > best_score:
                    best_score = score
                    best_k = k

        return best_k

    def _simple_clustering(
        self,
        features: NDArray[np.float64],
        num_clusters: int,
    ) -> ClusteringResult:
        """
        Simple fallback clustering without sklearn.

        Uses basic k-means-like assignment.
        """
        # Random initial centroids
        np.random.seed(42)
        centroid_indices = np.random.choice(len(features), min(num_clusters, len(features)), replace=False)
        centroids = features[centroid_indices]

        # Assign to nearest centroid
        assignments = []
        cluster_sizes: dict[int, int] = {i: 0 for i in range(len(centroids))}

        for idx, feat in enumerate(features):
            distances = [np.linalg.norm(feat - c) for c in centroids]
            cluster_id = int(np.argmin(distances))
            min_dist = distances[cluster_id]
            max_dist = max(distances) if max(distances) > 0 else 1.0
            similarity = 1.0 - (min_dist / (max_dist + 1e-10))

            assignments.append(
                ClusterAssignment(
                    episode_index=idx,
                    cluster_id=cluster_id,
                    similarity_score=float(similarity),
                )
            )
            cluster_sizes[cluster_id] += 1

        return ClusteringResult(
            num_clusters=len(centroids),
            assignments=assignments,
            cluster_sizes=cluster_sizes,
            silhouette_score=0.5,  # Unknown without sklearn
        )
