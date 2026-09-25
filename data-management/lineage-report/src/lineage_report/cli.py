"""Command-line entry point for runtime Azure ML lineage reports."""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .report import CollectionContext, collect_lineage, render_markdown, validate_output_path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_ROOT = _PROJECT_ROOT.parents[1]
_LOGGER = logging.getLogger(__name__)


class _AzureMLClientAdapter:
    def __init__(self, client: Any) -> None:
        self._client = client

    def list_data_assets(self, *, max_records: int) -> Iterable[Any]:
        return _list_versions(self._client.data, max_records=max_records)

    def list_models(self, *, max_records: int) -> Iterable[Any]:
        return _list_versions(self._client.models, max_records=max_records)


class _MLflowSDKClient:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get_run(self, run_id: str) -> Any | None:
        try:
            return self._client.get_run(run_id)
        except Exception:
            _LOGGER.warning("MLflow run enrichment unavailable for run %s", run_id)
            return None


class _FixtureAzureMLClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._assets = tuple(_namespace(entity) for entity in payload.get("assets", []))
        self._models = tuple(_namespace(entity) for entity in payload.get("models", []))

    def list_data_assets(self, *, max_records: int) -> Iterable[Any]:
        return self._assets[:max_records]

    def list_models(self, *, max_records: int) -> Iterable[Any]:
        return self._models[:max_records]


class _FixtureMLflowClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._runs = {run_id: _namespace(run) for run_id, run in payload.get("runs", {}).items()}

    def get_run(self, run_id: str) -> Any | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        return SimpleNamespace(info=run)


def create_parser() -> argparse.ArgumentParser:
    """Create the lineage report argument parser."""
    parser = argparse.ArgumentParser(description="Generate Viewer release-to-model Azure ML lineage")
    parser.add_argument("--subscription-id", default=os.environ.get("AZURE_SUBSCRIPTION_ID"))
    parser.add_argument("--resource-group", default=os.environ.get("AZURE_RESOURCE_GROUP"))
    parser.add_argument("--workspace-name", default=os.environ.get("AZUREML_WORKSPACE_NAME"))
    parser.add_argument("--terraform-dir", type=Path, default=_REPOSITORY_ROOT / "infrastructure/terraform")
    parser.add_argument("--output", type=Path, default=_PROJECT_ROOT / "output/report.md")
    parser.add_argument("--max-records", type=int, default=1000)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--generated-at")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Generate a lineage report from runtime clients or an offline fixture."""
    args = create_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if args.fixture is not None:
        payload = json.loads(args.fixture.read_text(encoding="utf-8"))
        context = CollectionContext(**payload["context"])
        azureml_client = _FixtureAzureMLClient(payload)
        mlflow_client = _FixtureMLflowClient(payload)
    else:
        context = _resolve_context(args)
        azureml_client, mlflow_client = _create_runtime_clients(context)

    rows = collect_lineage(context, azureml_client, mlflow_client, max_records=args.max_records)
    generated_at = args.generated_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    warning = validate_output_path(args.output, project_root=_PROJECT_ROOT)
    if warning is not None:
        _LOGGER.warning("%s", warning)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_markdown(context, rows, generated_at=generated_at), encoding="utf-8")
    return 0


def entrypoint() -> None:
    """Run the console entry point."""
    raise SystemExit(main())


def _resolve_context(args: argparse.Namespace) -> CollectionContext:
    subscription_id = args.subscription_id
    resource_group = args.resource_group
    workspace_name = args.workspace_name
    if not subscription_id or not resource_group or not workspace_name:
        terraform_context = _terraform_context(args.terraform_dir)
        subscription_id = subscription_id or terraform_context.subscription_id
        resource_group = resource_group or terraform_context.resource_group
        workspace_name = workspace_name or terraform_context.workspace_name
    return CollectionContext(
        subscription_id=subscription_id,
        resource_group=resource_group,
        workspace_name=workspace_name,
    )


def _terraform_context(terraform_dir: Path) -> CollectionContext:
    result = subprocess.run(
        ["terraform", "output", "-json", "azureml_workspace"],
        cwd=terraform_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    workspace = json.loads(result.stdout)
    resource_id = str(workspace["id"])
    parts = resource_id.strip("/").split("/")
    values = {parts[index].lower(): parts[index + 1] for index in range(0, len(parts) - 1, 2)}
    return CollectionContext(
        subscription_id=values["subscriptions"],
        resource_group=values["resourcegroups"],
        workspace_name=str(workspace["name"]),
    )


def _create_runtime_clients(context: CollectionContext) -> tuple[_AzureMLClientAdapter, _MLflowSDKClient]:
    import mlflow
    from azure.ai.ml import MLClient
    from azure.identity import DefaultAzureCredential
    from mlflow.tracking import MlflowClient

    client = MLClient(
        credential=DefaultAzureCredential(),
        subscription_id=context.subscription_id,
        resource_group_name=context.resource_group,
        workspace_name=context.workspace_name,
    )
    workspace = client.workspaces.get(name=context.workspace_name)
    mlflow.set_tracking_uri(workspace.mlflow_tracking_uri)
    return _AzureMLClientAdapter(client), _MLflowSDKClient(MlflowClient())


def _namespace(payload: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(**payload)


def _list_versions(operation: Any, *, max_records: int) -> tuple[Any, ...]:
    names = (str(entity.name) for entity in operation.list())
    unique_names = dict.fromkeys(itertools.islice(names, max_records))
    versions = (entity for name in unique_names for entity in operation.list(name=name))
    return tuple(itertools.islice(versions, max_records))


if __name__ == "__main__":
    sys.exit(main())
