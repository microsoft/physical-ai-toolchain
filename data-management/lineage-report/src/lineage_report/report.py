"""Collect and render immutable Viewer release lineage."""

from __future__ import annotations

import html
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class CollectionContext:
    """Runtime Azure ML workspace context."""

    subscription_id: str
    resource_group: str
    workspace_name: str


@dataclass(frozen=True)
class LineageRow:
    """One deterministic release, model, and run lineage result."""

    status: str
    dataset_id: str = ""
    release_id: str = ""
    manifest_sha256: str = ""
    statistics_sha256: str = ""
    target_format: str = ""
    statistics_profile_version: str = ""
    asset_name: str = ""
    asset_version: str = ""
    model_name: str = ""
    model_version: str = ""
    dataset_trust: str = ""
    azureml_run_id: str = ""
    mlflow_run_id: str = ""
    mlflow_experiment_id: str = ""


class AzureMLLineageClient(Protocol):
    """Bounded Azure ML entity listing boundary."""

    def list_data_assets(self, *, max_records: int) -> Iterable[Any]: ...

    def list_models(self, *, max_records: int) -> Iterable[Any]: ...


class MLflowLineageClient(Protocol):
    """MLflow run enrichment boundary."""

    def get_run(self, run_id: str) -> Any | None: ...


@dataclass(frozen=True)
class _AssetRecord:
    name: str
    version: str
    dataset_id: str
    release_id: str
    manifest_sha256: str
    statistics_sha256: str
    target_format: str
    statistics_profile_version: str


@dataclass(frozen=True)
class _ModelRecord:
    name: str
    version: str
    release_id: str
    manifest_sha256: str
    dataset_trust: str
    azureml_run_id: str
    mlflow_run_id: str
    mlflow_experiment_id: str


def collect_lineage(
    context: CollectionContext,
    azureml_client: AzureMLLineageClient,
    mlflow_client: MLflowLineageClient | None,
    *,
    max_records: int,
) -> tuple[LineageRow, ...]:
    """Collect bounded runtime entities and join them on immutable release evidence."""
    del context
    if max_records <= 0:
        raise ValueError("max_records must be greater than zero")

    assets = tuple(
        record
        for entity in azureml_client.list_data_assets(max_records=max_records)
        if (record := _asset_record(entity)) is not None
    )
    models = tuple(
        record
        for entity in azureml_client.list_models(max_records=max_records)
        if (record := _model_record(entity)) is not None
    )
    assets_by_key = _group_by_key(assets)
    models_by_key = _group_by_key(models)
    rows: list[LineageRow] = []

    for key in sorted(assets_by_key.keys() & models_by_key.keys()):
        matching_assets = assets_by_key.pop(key)
        matching_models = models_by_key.pop(key)
        rows.extend(_joined_row(asset, model, mlflow_client) for asset in matching_assets for model in matching_models)

    asset_release_ids = {record.release_id for records in assets_by_key.values() for record in records}
    model_release_ids = {record.release_id for records in models_by_key.values() for record in records}
    for release_id in sorted(asset_release_ids & model_release_ids):
        asset_keys = tuple(key for key in assets_by_key if key[0] == release_id)
        model_keys = tuple(key for key in models_by_key if key[0] == release_id)
        mismatched_assets = tuple(record for key in asset_keys for record in assets_by_key.pop(key))
        mismatched_models = tuple(record for key in model_keys for record in models_by_key.pop(key))
        rows.extend(
            _row(asset=asset, model=model, status="digest-mismatch")
            for asset in mismatched_assets
            for model in mismatched_models
        )

    rows.extend(_row(asset=asset, status="asset-orphan") for records in assets_by_key.values() for asset in records)
    rows.extend(_row(model=model, status="model-orphan") for records in models_by_key.values() for model in records)
    return tuple(sorted(rows, key=lambda row: (row.release_id, row.status, row.asset_name, row.model_name)))


def render_markdown(
    context: CollectionContext,
    rows: Iterable[LineageRow],
    *,
    generated_at: str,
) -> str:
    """Render deterministic, escaped Markdown from collected lineage rows."""
    ordered = sorted(rows, key=lambda row: (row.release_id, row.status, row.asset_name, row.model_name))
    lines = [
        "# Azure ML Release Lineage",
        "",
        f"Generated: `{_escape(generated_at)}`  ",
        f"Workspace: `{_escape(context.workspace_name)}`",
        "",
        "| Status | Dataset | Release | Manifest SHA-256 | Statistics SHA-256 | Asset | Model | Trust | "
        "Azure ML run | MLflow run | MLflow experiment |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in ordered:
        asset = f"{row.asset_name}:{row.asset_version}" if row.asset_name else "missing"
        model = f"{row.model_name}:{row.model_version}" if row.model_name else "missing"
        values = (
            row.status,
            row.dataset_id or "missing",
            row.release_id or "missing",
            row.manifest_sha256 or "missing",
            row.statistics_sha256 or "missing",
            asset,
            model,
            row.dataset_trust or "missing",
            row.azureml_run_id or "missing",
            row.mlflow_run_id or "missing",
            row.mlflow_experiment_id or "missing",
        )
        lines.append("| " + " | ".join(_escape(value) for value in values) + " |")
    return "\n".join(lines) + "\n"


def validate_output_path(output_path: Path, *, project_root: Path) -> str | None:
    """Warn when output is outside the project's ignored output directory."""
    if not output_path.is_absolute() and output_path.parts and output_path.parts[0] == "output":
        return None
    ignored_root = (project_root / "output").resolve()
    resolved = output_path.resolve()
    if resolved == ignored_root or ignored_root in resolved.parents:
        return None
    return f"Output path is outside the ignored lineage-report output directory: {output_path}"


def _asset_record(entity: Any) -> _AssetRecord | None:
    properties = {str(key): str(value) for key, value in (getattr(entity, "properties", None) or {}).items()}
    release_id = properties.get("release_id", "")
    manifest_sha256 = properties.get("manifest_sha256", "")
    if not release_id or not manifest_sha256:
        return None
    return _AssetRecord(
        name=str(entity.name),
        version=str(entity.version),
        dataset_id=properties.get("dataset_id", ""),
        release_id=release_id,
        manifest_sha256=manifest_sha256,
        statistics_sha256=properties.get("statistics_sha256", ""),
        target_format=properties.get("target_format", ""),
        statistics_profile_version=properties.get("statistics_profile_version", ""),
    )


def _model_record(entity: Any) -> _ModelRecord | None:
    tags = {str(key): str(value) for key, value in (getattr(entity, "tags", None) or {}).items()}
    release_id = tags.get("dataset_release_id", "")
    manifest_sha256 = tags.get("dataset_manifest_digest", "")
    if not release_id or not manifest_sha256:
        return None
    return _ModelRecord(
        name=str(entity.name),
        version=str(entity.version),
        release_id=release_id,
        manifest_sha256=manifest_sha256,
        dataset_trust=tags.get("dataset_trust", ""),
        azureml_run_id=tags.get("azureml_run_id", ""),
        mlflow_run_id=tags.get("mlflow_run_id", ""),
        mlflow_experiment_id=tags.get("mlflow_experiment_id", ""),
    )


def _group_by_key(records: Iterable[_AssetRecord | _ModelRecord]) -> dict[tuple[str, str], list[Any]]:
    grouped: defaultdict[tuple[str, str], list[Any]] = defaultdict(list)
    for record in records:
        grouped[(record.release_id, record.manifest_sha256)].append(record)
    return dict(grouped)


def _joined_row(
    asset: _AssetRecord,
    model: _ModelRecord,
    mlflow_client: MLflowLineageClient | None,
) -> LineageRow:
    experiment_id = model.mlflow_experiment_id
    status = "complete"
    if model.mlflow_run_id:
        run = mlflow_client.get_run(model.mlflow_run_id) if mlflow_client is not None else None
        if run is None:
            status = "mlflow-missing"
        else:
            experiment_id = str(run.info.experiment_id)
    return _row(asset=asset, model=model, status=status, mlflow_experiment_id=experiment_id)


def _row(
    *,
    status: str,
    asset: _AssetRecord | None = None,
    model: _ModelRecord | None = None,
    mlflow_experiment_id: str = "",
) -> LineageRow:
    return LineageRow(
        status=status,
        dataset_id=asset.dataset_id if asset else "",
        release_id=asset.release_id if asset else model.release_id if model else "",
        manifest_sha256=asset.manifest_sha256 if asset else model.manifest_sha256 if model else "",
        statistics_sha256=asset.statistics_sha256 if asset else "",
        target_format=asset.target_format if asset else "",
        statistics_profile_version=asset.statistics_profile_version if asset else "",
        asset_name=asset.name if asset else "",
        asset_version=asset.version if asset else "",
        model_name=model.name if model else "",
        model_version=model.version if model else "",
        dataset_trust=model.dataset_trust if model else "",
        azureml_run_id=model.azureml_run_id if model else "",
        mlflow_run_id=model.mlflow_run_id if model else "",
        mlflow_experiment_id=mlflow_experiment_id or (model.mlflow_experiment_id if model else ""),
    )


def _escape(value: str) -> str:
    return (
        html.escape(str(value), quote=False)
        .replace("|", "\\|")
        .replace("`", "&#96;")
        .replace("\n", " ")
        .replace("\r", " ")
    )
