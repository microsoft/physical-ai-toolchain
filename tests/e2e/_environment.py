"""Resolve a named deployment environment for live e2e tests.

Setting ``E2E_ENVIRONMENT=<name>`` selects a non-secret environment bundle (the
``deployment.json`` produced by the environment-deployment workflow) and fills the
Azure resource variables that the e2e fixtures and submit scripts read. Values that
are already exported always win, so a bundle only supplies missing defaults.

Bundles are never tracked: they live under the user's configuration directory or the
gitignored ``infrastructure/setup/generated/<name>`` directory.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

ENVIRONMENT_VAR = "E2E_ENVIRONMENT"
BUNDLE_DIR_VAR = "E2E_ENVIRONMENT_BUNDLE_DIR"
DEPLOYMENT_FILE = "deployment.json"
AML_COMPUTE_NAME_MAX_LENGTH = 16

# Bundle field to the environment variable the fixtures and submit scripts read.
BUNDLE_VARIABLES: tuple[tuple[str, str], ...] = (
    ("subscription_id", "AZURE_SUBSCRIPTION_ID"),
    ("resource_group", "AZURE_RESOURCE_GROUP"),
    ("azureml_workspace", "AZUREML_WORKSPACE_NAME"),
    ("storage_account", "AZURE_STORAGE_ACCOUNT_NAME"),
    ("aks_cluster", "AKS_CLUSTER_NAME"),
)
COMPUTE_VARIABLE = "AZUREML_COMPUTE"

_ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class EnvironmentBundleError(RuntimeError):
    """Raised when a named environment bundle cannot be found or read."""


@dataclass(frozen=True)
class EnvironmentBundle:
    name: str
    directory: Path
    values: Mapping[str, str]


def active_environment_name(environ: Mapping[str, str] = os.environ) -> str | None:
    """Return the requested environment name, or ``None`` when none is selected."""
    value = environ.get(ENVIRONMENT_VAR, "").strip()
    return value or None


def bundle_search_paths(name: str, repo_root: Path, environ: Mapping[str, str] = os.environ) -> list[Path]:
    """Return candidate bundle directories in precedence order."""
    paths: list[Path] = []
    explicit = environ.get(BUNDLE_DIR_VAR, "").strip()
    if explicit:
        paths.append(Path(explicit).expanduser())
    home = environ.get("HOME", "").strip()
    home_path = Path(home) if home else Path.home()
    paths.append(home_path / ".config" / "physical-ai-toolchain" / "environments" / name)
    paths.append(repo_root / "infrastructure" / "setup" / "generated" / name)
    return paths


def _read_deployment(deployment: Path) -> dict[str, object]:
    try:
        payload = json.loads(deployment.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EnvironmentBundleError(f"Cannot read environment bundle file {deployment}: {error}") from error
    if not isinstance(payload, dict):
        raise EnvironmentBundleError(f"Environment bundle file {deployment} must contain a JSON object")
    return payload


def load_environment_bundle(
    name: str,
    repo_root: Path,
    environ: Mapping[str, str] = os.environ,
) -> EnvironmentBundle:
    """Load the first bundle found for ``name``; raise when none can be used."""
    if not _ENVIRONMENT_NAME_PATTERN.fullmatch(name):
        raise EnvironmentBundleError(f"Invalid {ENVIRONMENT_VAR} value {name!r}: use letters, digits, '.', '_', or '-'")

    searched = bundle_search_paths(name, repo_root, environ)
    for directory in searched:
        deployment = directory / DEPLOYMENT_FILE
        if deployment.is_symlink():
            raise EnvironmentBundleError(f"Environment bundle file must not be a symlink: {deployment}")
        if not deployment.is_file():
            continue
        payload = _read_deployment(deployment)
        values: dict[str, str] = {}
        for field, _ in BUNDLE_VARIABLES:
            value = payload.get(field)
            if isinstance(value, str) and value.strip():
                values[field] = value.strip()
        return EnvironmentBundle(name=name, directory=directory, values=values)

    locations = ", ".join(str(path / DEPLOYMENT_FILE) for path in searched)
    raise EnvironmentBundleError(f"Environment bundle {name!r} was not found. Searched: {locations}")


def derive_compute_target(aks_cluster: str) -> str:
    """Derive the Azure ML Kubernetes compute name from an AKS cluster name."""
    compute = aks_cluster.replace("aks-", "k8s-", 1)
    if len(compute) > AML_COMPUTE_NAME_MAX_LENGTH:
        compute = compute[:AML_COMPUTE_NAME_MAX_LENGTH].rstrip("-")
    return compute


def environment_defaults(bundle: EnvironmentBundle) -> dict[str, str]:
    """Return the environment variables a bundle can supply."""
    defaults = {variable: bundle.values[field] for field, variable in BUNDLE_VARIABLES if field in bundle.values}
    aks_cluster = bundle.values.get("aks_cluster")
    if aks_cluster:
        defaults[COMPUTE_VARIABLE] = derive_compute_target(aks_cluster)
    return defaults


def apply_environment_defaults(
    bundle: EnvironmentBundle,
    environ: MutableMapping[str, str] = os.environ,
) -> dict[str, str]:
    """Fill unset or blank variables from the bundle and return the ones applied."""
    applied: dict[str, str] = {}
    for variable, value in environment_defaults(bundle).items():
        if not environ.get(variable, "").strip():
            environ[variable] = value
            applied[variable] = value
    return applied


def activate_named_environment(
    repo_root: Path,
    environ: MutableMapping[str, str] = os.environ,
) -> EnvironmentBundle | None:
    """Load the selected bundle and apply its defaults; return ``None`` when none is selected."""
    name = active_environment_name(environ)
    if name is None:
        return None
    bundle = load_environment_bundle(name, repo_root, environ)
    apply_environment_defaults(bundle, environ)
    return bundle
