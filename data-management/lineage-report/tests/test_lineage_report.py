"""Behavior tests for runtime Azure ML lineage reporting."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lineage_report.cli import _AzureMLClientAdapter, main
from lineage_report.report import (
    CollectionContext,
    collect_lineage,
    render_markdown,
    validate_output_path,
)


class _AzureMLClient:
    def __init__(self, *, assets: list[SimpleNamespace], models: list[SimpleNamespace]) -> None:
        self.assets = assets
        self.models = models

    def list_data_assets(self, *, max_records: int):
        return self.assets[:max_records]

    def list_models(self, *, max_records: int):
        return self.models[:max_records]


class _MLflowClient:
    def __init__(self, runs: dict[str, SimpleNamespace]) -> None:
        self.runs = runs

    def get_run(self, run_id: str) -> SimpleNamespace | None:
        return self.runs.get(run_id)


class _VersionOperation:
    def __init__(self, versions: dict[str, list[str]]) -> None:
        self.versions = versions

    def list(self, name: str | None = None):
        if name is None:
            return (SimpleNamespace(name=entity_name) for entity_name in self.versions)
        return (SimpleNamespace(name=name, version=version) for version in self.versions[name])


def _asset(
    *,
    name: str = "dataset-a-abc123",
    version: str = "release-1",
    release_id: str = "release-1",
    manifest_sha256: str = "a" * 64,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        version=version,
        path="https://storage.example/releases/dataset-a/release-1",
        properties={
            "dataset_id": "dataset-a",
            "release_id": release_id,
            "manifest_sha256": manifest_sha256,
            "statistics_sha256": "b" * 64,
            "target_format": "lerobot:3.0",
            "statistics_profile_version": "1.0.0",
        },
    )


def _model(
    *,
    name: str = "policy-a",
    version: str = "3",
    release_id: str = "release-1",
    manifest_sha256: str = "a" * 64,
    mlflow_run_id: str = "run-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        version=version,
        tags={
            "dataset_release_id": release_id,
            "dataset_manifest_digest": manifest_sha256,
            "dataset_trust": "verified",
            "azureml_run_id": "azure-run-1",
            "mlflow_run_id": mlflow_run_id,
            "mlflow_experiment_id": "experiment-1",
        },
    )


def _context() -> CollectionContext:
    return CollectionContext(subscription_id="sub", resource_group="rg", workspace_name="workspace")


def test_given_matching_asset_model_and_run_when_collected_then_complete_lineage_is_returned() -> None:
    # Arrange
    azureml = _AzureMLClient(assets=[_asset()], models=[_model()])
    mlflow = _MLflowClient(
        {"run-1": SimpleNamespace(info=SimpleNamespace(run_id="run-1", experiment_id="experiment-1"))}
    )

    # Act
    rows = collect_lineage(_context(), azureml, mlflow, max_records=100)

    # Assert
    assert len(rows) == 1
    assert rows[0].status == "complete"
    assert rows[0].release_id == "release-1"
    assert rows[0].model_name == "policy-a"
    assert rows[0].mlflow_run_id == "run-1"
    assert rows[0].mlflow_experiment_id == "experiment-1"


def test_given_versioned_sdk_entities_when_listed_then_all_versions_respect_global_bound() -> None:
    # Arrange
    operation = _VersionOperation({"asset-a": ["1", "2"], "asset-b": ["1", "2"]})
    client = _AzureMLClientAdapter(SimpleNamespace(data=operation, models=operation))

    # Act
    assets = tuple(client.list_data_assets(max_records=3))
    models = tuple(client.list_models(max_records=2))

    # Assert
    assert [(entity.name, entity.version) for entity in assets] == [
        ("asset-a", "1"),
        ("asset-a", "2"),
        ("asset-b", "1"),
    ]
    assert [(entity.name, entity.version) for entity in models] == [("asset-a", "1"), ("asset-a", "2")]


def test_given_multiple_models_for_one_release_when_collected_then_no_lineage_is_collapsed() -> None:
    # Arrange
    azureml = _AzureMLClient(
        assets=[_asset()],
        models=[_model(name="policy-a", version="1"), _model(name="policy-b", version="2")],
    )
    mlflow = _MLflowClient(
        {"run-1": SimpleNamespace(info=SimpleNamespace(run_id="run-1", experiment_id="experiment-1"))}
    )

    # Act
    rows = collect_lineage(_context(), azureml, mlflow, max_records=100)

    # Assert
    assert [(row.model_name, row.model_version) for row in rows] == [("policy-a", "1"), ("policy-b", "2")]


def test_given_orphans_digest_mismatch_and_missing_run_when_collected_then_states_are_explicit() -> None:
    # Arrange
    assets = [
        _asset(name="asset-orphan", release_id="asset-only"),
        _asset(name="asset-mismatch", release_id="mismatch", manifest_sha256="c" * 64),
        _asset(name="asset-no-run", release_id="no-run", manifest_sha256="d" * 64),
    ]
    models = [
        _model(name="model-orphan", release_id="model-only"),
        _model(name="model-mismatch", release_id="mismatch", manifest_sha256="e" * 64),
        _model(name="model-no-run", release_id="no-run", manifest_sha256="d" * 64, mlflow_run_id="missing"),
    ]

    # Act
    rows = collect_lineage(_context(), _AzureMLClient(assets=assets, models=models), _MLflowClient({}), max_records=100)

    # Assert
    assert [(row.release_id, row.status) for row in rows] == [
        ("asset-only", "asset-orphan"),
        ("mismatch", "digest-mismatch"),
        ("model-only", "model-orphan"),
        ("no-run", "mlflow-missing"),
    ]


def test_given_untrusted_values_when_rendered_then_markdown_is_escaped_and_stable() -> None:
    # Arrange
    azureml = _AzureMLClient(
        assets=[_asset(name="asset|`unsafe", release_id="release-<one>")],
        models=[_model(name="model|unsafe", release_id="release-<one>")],
    )
    rows = collect_lineage(_context(), azureml, _MLflowClient({}), max_records=100)

    # Act
    first = render_markdown(_context(), rows, generated_at="2026-09-25T12:00:00Z")
    second = render_markdown(_context(), tuple(reversed(rows)), generated_at="2026-09-25T12:00:00Z")

    # Assert
    assert first == second
    assert "asset\\|&#96;unsafe" in first
    assert "release-&lt;one&gt;" in first
    assert "Workspace: `workspace`" in first
    assert "SAS" not in first


def test_given_output_outside_ignored_directory_when_validated_then_warning_is_returned(tmp_path: Path) -> None:
    # Arrange
    default_output = Path("output/report.md")
    external_output = tmp_path / "report.md"

    # Act
    default_warning = validate_output_path(default_output, project_root=Path.cwd())
    external_warning = validate_output_path(external_output, project_root=Path.cwd())

    # Assert
    assert default_warning is None
    assert external_warning is not None
    assert "outside" in external_warning


def test_given_fixture_input_when_cli_runs_then_report_is_written(tmp_path: Path) -> None:
    # Arrange
    fixture_path = tmp_path / "lineage.json"
    output_path = tmp_path / "output" / "report.md"
    fixture_path.write_text(
        json.dumps(
            {
                "context": {"subscription_id": "sub", "resource_group": "rg", "workspace_name": "workspace"},
                "assets": [vars(_asset())],
                "models": [vars(_model())],
                "runs": {"run-1": {"run_id": "run-1", "experiment_id": "experiment-1"}},
            }
        ),
        encoding="utf-8",
    )

    # Act
    exit_code = main(
        [
            "--fixture",
            str(fixture_path),
            "--output",
            str(output_path),
            "--generated-at",
            "2026-09-25T12:00:00Z",
        ]
    )

    # Assert
    assert exit_code == 0
    assert "release-1" in output_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("max_records", [0, -1])
def test_given_invalid_collection_bound_when_collected_then_value_is_rejected(max_records: int) -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="max_records"):
        collect_lineage(_context(), _AzureMLClient(assets=[], models=[]), _MLflowClient({}), max_records=max_records)
