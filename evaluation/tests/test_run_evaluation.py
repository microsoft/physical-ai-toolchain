"""Tests for release lineage in the canonical LeRobot evaluator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "sil" / "scripts" / "run_evaluation.py"
_SPEC = importlib.util.spec_from_file_location("run_evaluation", _SCRIPT_PATH)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def _release(release_id: str, source_index: int = 7) -> dict:
    return {
        "release_id": release_id,
        "manifest_evidence_digest": "a" * 64,
        "target_format_name": "lerobot",
        "target_format_version": "3.0",
        "source_dataset_ids": ["dataset-a"],
        "episode_count": 1,
        "frame_count": 10,
        "quality_profile_versions": ["quality-v1"],
        "accepted_decision_ids": ["decision-a"],
        "parent_release_ids": [],
        "quality_artifact_paths": ["metadata/quality/run-a.json", "metadata/package-quality.json"],
        "episode_source_mapping": [
            {
                "release_episode_index": 0,
                "source_dataset_id": "dataset-a",
                "source_episode_index": source_index,
                "decision_id": "decision-a",
                "quality_run_id": "run-a",
            }
        ],
    }


def test_verified_mapping_and_schema_artifacts(tmp_path: Path) -> None:
    lineage = {"schema_version": "1.0.0", "trust": "verified", "release": _release("release-1")}
    mapping = _MOD._episode_source_mapping(lineage)
    episode = {"episode": 0, "rollout_error": "failed", "source_episode": mapping[0], "mae": 1.0}

    _MOD._write_vla_schema_v1(tmp_path, {"mae": 1.0}, [episode], "dataset-a", "policy-a", lineage)

    metrics = json.loads((tmp_path / "metrics.json").read_text())
    failure = json.loads((tmp_path / "failure_cases.jsonl").read_text())
    assert metrics["dataset_lineage"]["release"]["release_id"] == "release-1"
    assert failure["dataset_version"] == "release-1"
    assert failure["source_episode"]["source_episode_index"] == 7


def test_derived_mapping_offsets_parent_episodes() -> None:
    first = _release("release-1", source_index=7)
    first["episode_count"] = 2
    second = _release("release-2", source_index=12)
    lineage = {
        "schema_version": "1.0.0",
        "trust": "derived",
        "derived_input_digest": "d" * 64,
        "parent_releases": [first, second],
    }

    mapping = _MOD._episode_source_mapping(lineage)

    assert mapping[0]["parent_release_id"] == "release-1"
    assert mapping[2]["parent_release_id"] == "release-2"
    assert mapping[2]["source_episode_index"] == 12


def test_mlflow_lineage_artifacts_and_unverified_tag_suppression(tmp_path: Path) -> None:
    mlflow = MagicMock()
    verified = {"schema_version": "1.0.0", "trust": "verified", "release": _release("release-1")}

    _MOD._log_dataset_lineage(mlflow, tmp_path, verified)

    assert mlflow.log_artifact.call_count == 2
    assert mlflow.set_tags.call_args.args[0]["dataset.release_id"] == "release-1"
    summary = mlflow.log_dict.call_args.args[0]
    assert summary["release_id"] == "release-1"
    assert "episode_source_mapping" not in summary

    mlflow.reset_mock()
    unverified = {"schema_version": "1.0.0", "trust": "unverified", "source": "huggingface"}
    _MOD._log_dataset_lineage(mlflow, tmp_path, unverified)

    assert mlflow.set_tags.call_args.args[0] == {"dataset.trust": "unverified"}
    mlflow.log_artifact.assert_not_called()
