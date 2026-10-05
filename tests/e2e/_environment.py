"""Resolve a named deployment environment for live e2e tests.

Setting ``E2E_ENVIRONMENT=<name>`` selects a non-secret environment bundle (the
``deployment.json`` produced by the environment-deployment workflow) and fills the
Azure resource variables that the e2e fixtures and submit scripts read. Values that
are already exported always win, so a bundle only supplies missing defaults.

Bundles are never tracked: they live under the user's configuration directory or the
gitignored ``infrastructure/setup/generated/<name>`` directory. Secrets such as
``HF_TOKEN`` never go in a bundle; they live in the gitignored repository-root
``.env.local``, which ``read_local_env`` reads for the variables a caller needs. The GPU
instance types for the Azure ML checks come from the same file through
``resolve_instance_type``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

ENVIRONMENT_VAR = "E2E_ENVIRONMENT"
BUNDLE_DIR_VAR = "E2E_ENVIRONMENT_BUNDLE_DIR"
DEPLOYMENT_FILE = "deployment.json"
LOCAL_ENV_FILE = ".env.local"
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
INSTANCE_TYPE_VARIABLE = "E2E_AML_INSTANCE_TYPE"

_ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_LOCAL_ENV_ASSIGNMENT = re.compile(r"(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)")
_LOCAL_ENV_SPACED_ASSIGNMENT = re.compile(r"(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]+=")
# Values that ``source`` assigns literally: empty, single-quoted, double-quoted without
# expansions or escapes, or a bare word without quoting, expansion, or shell operators.
_LOCAL_ENV_LITERAL = re.compile(
    r"""(?:'(?P<single>[^']*)'|"(?P<double>[^"$`\\]*)"|(?P<bare>[^\s'"$`\\~;&|<>()#][^\s'"$`\\~;&|<>()]*))?"""
    r"(?:[ \t]+#.*)?"
)


class EnvironmentBundleError(RuntimeError):
    """Raised when a named environment bundle cannot be found or read."""


class LocalEnvError(RuntimeError):
    """Raised when the repository-root ``.env.local`` exists but cannot be read."""


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


def _local_env_value(raw: str) -> str | None:
    """Return the literal value assigned by the text after ``NAME=``, or ``None`` when it isn't a plain literal."""
    match = _LOCAL_ENV_LITERAL.fullmatch(raw)
    if match is None:
        return None
    return next((group for group in match.group("single", "double", "bare") if group is not None), "")


def read_local_env(repo_root: Path, names: Iterable[str]) -> dict[str, str]:
    """Return the requested variables that the untracked repository-root ``.env.local`` assigns.

    Submission scripts source the same file through ``scripts/lib/common.sh``, after the
    environment is set, so an assignment there wins over an exported value, even an empty
    one. This reads ``NAME=value`` lines (an optional ``export``, the last assignment winning)
    and returns each requested name the file assigns, with its possibly empty value. A
    requested name must be assigned a plain literal (a bare word, a single-quoted string, or
    a double-quoted string without ``$``, backticks, or backslashes) so the value matches what
    ``source`` assigns. Any other assignment to it, or a file that can't be read, raises
    ``LocalEnvError`` without exposing the value. A missing file yields no values.
    """
    wanted = set(names)
    if not wanted:
        return {}
    path = repo_root / LOCAL_ENV_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except UnicodeDecodeError:
        raise LocalEnvError(f"Cannot read {path}: the file is not valid UTF-8") from None
    except OSError as error:
        raise LocalEnvError(f"Cannot read {path}: {error.strerror or type(error).__name__}") from None
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        spaced = _LOCAL_ENV_SPACED_ASSIGNMENT.match(stripped)
        if spaced is not None and spaced.group(1) in wanted:
            raise LocalEnvError(
                f"Cannot use {spaced.group(1)} from {path} (line {number}): remove the spaces around '='"
            )
        match = _LOCAL_ENV_ASSIGNMENT.fullmatch(stripped)
        if match is None or match.group(1) not in wanted:
            continue
        value = _local_env_value(match.group(2))
        if value is None:
            raise LocalEnvError(
                f"Cannot use {match.group(1)} from {path} (line {number}): assign a plain value, "
                "or a quoted one without $, backticks, or backslashes"
            )
        values[match.group(1)] = value
    return values


@dataclass(frozen=True)
class InstanceTypeChoice:
    """The GPU instance type a category's Azure ML jobs request and the variable that chose it.

    ``value`` is ``None`` when no variable is set, so each submission script keeps its own
    default, and empty when an exported variable asks the scripts to omit the instance type.
    """

    value: str | None
    variable: str | None = None
    source: str | None = None

    def describe(self) -> str:
        if self.value is None:
            return "script default"
        name = self.value or "omitted"
        return f"{name} ({self.variable} in {self.source})"


def instance_type_variables(category: str) -> tuple[str, str]:
    """Return a category's own instance-type variable, then the variable shared by every category."""
    return f"{INSTANCE_TYPE_VARIABLE}_{category.upper().replace('-', '_')}", INSTANCE_TYPE_VARIABLE


def resolve_instance_type(repo_root: Path, category: str, environ: Mapping[str, str]) -> InstanceTypeChoice:
    """Choose the instance type for a category from ``.env.local`` and the environment.

    The category's own variable wins over the shared one. For each variable, a value assigned
    in ``.env.local`` wins over an exported one, as it does for the submission scripts. A value
    in the file must name an instance type; an empty or invalid one raises ``LocalEnvError``
    without exposing it.
    """
    names = instance_type_variables(category)
    local = read_local_env(repo_root, names)
    for name in names:
        if name in local:
            value = local[name].strip()
            if not value:
                raise LocalEnvError(
                    f"{name} is empty in {LOCAL_ENV_FILE}, which overrides the environment; "
                    "set an instance type name there or remove the line"
                )
            return InstanceTypeChoice(value, name, LOCAL_ENV_FILE)
        if name in environ:
            return InstanceTypeChoice(environ[name].strip(), name, "environment")
    return InstanceTypeChoice(None)
