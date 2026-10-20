"""Select and resolve the platform profile that the OSMO lifecycle e2e tests run against.

``PLATFORM_PROFILE`` selects ``azure`` (the default) or ``local``, as it does for the OSMO submit
scripts, and ``scripts/platform-profile.sh`` resolves it, so the tests and the scripts read one
source. ``tests.e2e._platform`` builds the services each profile runs on.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tests.e2e._common import format_command_failure, run_command

PROFILE_VARIABLE = "PLATFORM_PROFILE"
AZURE_PROFILE = "azure"
LOCAL_PROFILE = "local"
PROFILE_NAMES = (AZURE_PROFILE, LOCAL_PROFILE)

_PROFILE_FIELDS = {
    "PROFILE_NAME": "name",
    "PROFILE_TRACKING_BACKEND": "tracking_backend",
    "PROFILE_OSMO_URL": "osmo_url",
    "PROFILE_OSMO_CONFIG_DIR": "osmo_config_dir",
    "PROFILE_MLFLOW_HOST_URI": "mlflow_host_uri",
    "PROFILE_DATASETS_HOST_DIR": "datasets_host_dir",
    "PROFILE_IMAGE_REGISTRY": "image_registry",
    "PROFILE_KUBECONFIG": "kubeconfig",
    "PROFILE_KUBE_CONTEXT": "kube_context",
}


class PlatformProfileError(RuntimeError):
    """Raised when the platform profile cannot be selected or resolved."""


@dataclass(frozen=True)
class PlatformProfile:
    """The values of ``scripts/platform-profile.sh`` that the e2e fixtures read."""

    name: str
    tracking_backend: str
    osmo_url: str = ""
    osmo_config_dir: str = ""
    mlflow_host_uri: str = ""
    datasets_host_dir: str = ""
    image_registry: str = ""
    kubeconfig: str = ""
    kube_context: str = ""

    @property
    def is_local(self) -> bool:
        return self.name == LOCAL_PROFILE

    def profile_args(self) -> list[str]:
        """Return the option that selects this profile in a submit script."""
        return ["--profile", self.name]

    def client_environment(self) -> dict[str, str]:
        """Return the variables that point the OSMO CLI and kubectl at this profile's services.

        The azure profile keeps the caller's OSMO login and kubeconfig, so it sets none.
        """
        environment: dict[str, str] = {}
        if self.osmo_config_dir:
            environment["OSMO_CONFIG_FILE_DIR"] = self.osmo_config_dir
        if self.kubeconfig:
            environment["KUBECONFIG"] = self.kubeconfig
        return environment


def selected_profile_name(environ: Mapping[str, str] = os.environ) -> str:
    """Return the profile ``PLATFORM_PROFILE`` selects, which defaults to azure as in the submit scripts."""
    name = environ.get(PROFILE_VARIABLE, "").strip() or AZURE_PROFILE
    if name not in PROFILE_NAMES:
        raise PlatformProfileError(f"Unknown {PROFILE_VARIABLE} {name!r}: use {' or '.join(PROFILE_NAMES)}")
    return name


def profile_selected(name: str, environ: Mapping[str, str] = os.environ) -> bool:
    """Return whether ``PLATFORM_PROFILE`` selects ``name``, for collection-time skips; invalid values select none."""
    try:
        return selected_profile_name(environ) == name
    except PlatformProfileError:
        return False


def parse_profile(output: str) -> PlatformProfile:
    """Build a profile from the resolver's ``KEY=VALUE`` lines."""
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in _PROFILE_FIELDS:
            values[_PROFILE_FIELDS[key]] = value
    if not values.get("name") or not values.get("tracking_backend"):
        raise PlatformProfileError(f"The platform profile resolver printed no profile:\n{output}")
    return PlatformProfile(**values)


def resolve_platform_profile(repo_root: Path, environ: Mapping[str, str] = os.environ) -> PlatformProfile:
    """Resolve the selected profile through ``scripts/platform-profile.sh``."""
    name = selected_profile_name(environ)
    result = run_command([str(repo_root / "scripts/platform-profile.sh"), "--profile", name], cwd=repo_root)
    if result.returncode != 0:
        raise PlatformProfileError(f"Cannot resolve the {name} platform profile\n\n{format_command_failure(result)}")
    return parse_profile(result.stdout)


def osmo_login_url(config_dir: str) -> str | None:
    """Return the service the OSMO CLI in ``config_dir`` is logged in to, or ``None`` before a login."""
    try:
        text = (Path(config_dir) / "login.yaml").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "url:":
            return fields[1]
    return None
