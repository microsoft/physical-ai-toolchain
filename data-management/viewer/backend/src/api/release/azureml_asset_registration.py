"""Optional Azure ML data asset registration contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from ..models.releases import ReleaseManifest, ReleaseStatistics

if TYPE_CHECKING:
    from ..config import AppConfig

_AZUREML_ASSET_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,29}$")
_AZUREML_ASSET_NAME_MAX_LENGTH = 255
_AZUREML_ASSET_NAME_SUFFIX_LENGTH = 12
_UNSAFE_ASSET_NAME_CHARS = re.compile(r"[^a-z0-9]+")
_AZUREML_TAG_VALUE_MAX_LENGTH = 256
_MANIFEST_PATH = "metadata/release-manifest.json"
_STATISTICS_PATH = "metadata/release-statistics.json"
_CHECKSUMS_PATH = "checksums.sha256"
_MARKER_PATH = ".published.json"


class PublishedReleaseReader(Protocol):
    """Read marker-visible release evidence from immutable publication storage."""

    async def read_published_file(self, dataset_id: str, release_id: str, relative_path: str) -> bytes: ...

    def published_release_url(self, dataset_id: str, release_id: str) -> str: ...


@dataclass(frozen=True)
class AzureMLAssetRegistration:
    """SDK-neutral immutable Azure ML data asset registration request."""

    name: str
    version: str
    path: str
    properties: dict[str, str]
    tags: dict[str, str]


@dataclass(frozen=True)
class AzureMLRegistrationEvidence:
    """Validated published evidence used by immediate and reconciled registration."""

    dataset_id: str
    release_id: str
    manifest_sha256: str
    statistics_sha256: str
    target_format: str
    statistics_profile_version: str
    registration: AzureMLAssetRegistration


class AzureMLAssetClient(Protocol):
    """Narrow external-client boundary for Azure ML data assets."""

    def get_data_asset(self, name: str, version: str) -> AzureMLAssetRegistration | None: ...

    def create_data_asset(self, registration: AzureMLAssetRegistration) -> None: ...


class AzureMLAssetRegistrationConflictError(RuntimeError):
    """Raised when an immutable asset version has different evidence."""


class StatisticsUnavailableError(ValueError):
    """Raised when a valid legacy release predates release statistics."""


class AzureMLAssetRegistrationService:
    """Ensure immutable Azure ML data asset versions idempotently."""

    def __init__(self, client: AzureMLAssetClient) -> None:
        self._client = client

    def ensure(self, evidence: AzureMLRegistrationEvidence) -> None:
        """Create a missing asset version or accept an exact existing match."""
        registration = evidence.registration
        existing = self._client.get_data_asset(registration.name, registration.version)
        if existing is None:
            self._client.create_data_asset(registration)
            return
        if existing.path != registration.path or existing.properties != registration.properties:
            raise AzureMLAssetRegistrationConflictError(
                "Azure ML data asset version conflicts with published evidence: "
                f"{registration.name}:{registration.version}"
            )


async def hydrate_registration_evidence(
    reader: PublishedReleaseReader,
    dataset_id: str,
    release_id: str,
) -> AzureMLRegistrationEvidence:
    """Hydrate and validate one published release registration request."""
    validate_azureml_data_asset_version(release_id)
    marker_bytes = await reader.read_published_file(dataset_id, release_id, _MARKER_PATH)
    manifest_bytes = await reader.read_published_file(dataset_id, release_id, _MANIFEST_PATH)
    checksums_bytes = await reader.read_published_file(dataset_id, release_id, _CHECKSUMS_PATH)
    marker = json.loads(marker_bytes)
    if not isinstance(marker, dict) or marker.get("dataset_id") != dataset_id or marker.get("release_id") != release_id:
        raise ValueError("Published release marker identity mismatch")
    manifest = ReleaseManifest.model_validate_json(manifest_bytes)
    source_dataset_ids = {source.dataset_id for source in manifest.source_provenance}
    if manifest.release_id != release_id or source_dataset_ids != {dataset_id}:
        raise ValueError("Published release manifest identity mismatch")
    statistics_file = next((entry for entry in manifest.files if entry.path == _STATISTICS_PATH), None)
    if statistics_file is None:
        raise StatisticsUnavailableError("Published release manifest does not inventory release statistics")
    checksums = _parse_checksums(checksums_bytes)
    manifest_sha256 = _verified_digest(_MANIFEST_PATH, manifest_bytes, checksums)
    statistics_bytes = await reader.read_published_file(dataset_id, release_id, _STATISTICS_PATH)
    statistics_sha256 = _verified_digest(_STATISTICS_PATH, statistics_bytes, checksums)
    if statistics_file.size_bytes != len(statistics_bytes) or statistics_file.sha256 != statistics_sha256:
        raise ValueError("Published release statistics do not match the manifest inventory")
    statistics = ReleaseStatistics.model_validate_json(statistics_bytes)
    target_format = f"{manifest.target_format.name}:{manifest.target_format.version}"
    profile_version = statistics.statistics_profile.version
    properties = {
        "dataset_id": dataset_id,
        "release_id": release_id,
        "manifest_sha256": manifest_sha256,
        "statistics_sha256": statistics_sha256,
        "target_format": target_format,
        "statistics_profile_version": profile_version,
    }
    tags = {
        "dataset_id": _bounded_tag_value(dataset_id),
        "release_id": _bounded_tag_value(release_id),
        "target_format": _bounded_tag_value(target_format),
        "statistics_profile_version": _bounded_tag_value(profile_version),
    }
    registration = AzureMLAssetRegistration(
        name=azureml_data_asset_name(dataset_id),
        version=release_id,
        path=reader.published_release_url(dataset_id, release_id),
        properties=properties,
        tags=tags,
    )
    return AzureMLRegistrationEvidence(
        dataset_id=dataset_id,
        release_id=release_id,
        manifest_sha256=manifest_sha256,
        statistics_sha256=statistics_sha256,
        target_format=target_format,
        statistics_profile_version=profile_version,
        registration=registration,
    )


def create_azureml_asset_registration_service(
    config: AppConfig,
    *,
    client_factory: Any | None = None,
) -> AzureMLAssetRegistrationService | None:
    """Create the optional Azure ML registration service lazily."""
    if not config.azureml_registration_enabled:
        return None
    required_config = {
        "AZURE_SUBSCRIPTION_ID": config.azure_subscription_id,
        "AZURE_RESOURCE_GROUP": config.azure_resource_group,
        "AZUREML_WORKSPACE_NAME": config.azureml_workspace_name,
    }
    missing = [name for name, value in required_config.items() if value is None]
    if missing:
        raise ValueError(f"Azure ML registration requires {', '.join(missing)}")
    if client_factory is not None:
        return AzureMLAssetRegistrationService(client_factory(config))
    try:
        from azure.ai.ml import MLClient
        from azure.identity import DefaultAzureCredential

        client = MLClient(
            credential=DefaultAzureCredential(),
            subscription_id=config.azure_subscription_id,
            resource_group_name=config.azure_resource_group,
            workspace_name=config.azureml_workspace_name,
        )
    except ImportError as exc:
        raise RuntimeError("Azure ML registration requires the backend azureml extra") from exc
    return AzureMLAssetRegistrationService(_AzureMLAssetClientAdapter(client))


def validate_azureml_data_asset_version(release_id: str) -> str:
    """Return an exact release ID that is safe as an Azure ML asset version."""
    if _AZUREML_ASSET_VERSION_PATTERN.fullmatch(release_id) is None:
        raise ValueError("release ID is not a valid Azure ML data asset version")
    return release_id


def azureml_data_asset_name(dataset_id: str) -> str:
    """Derive a readable, collision-resistant Azure ML asset name."""
    digest = hashlib.sha256(dataset_id.encode("utf-8")).hexdigest()[:_AZUREML_ASSET_NAME_SUFFIX_LENGTH]
    max_slug_length = _AZUREML_ASSET_NAME_MAX_LENGTH - len(digest) - 1
    slug = _UNSAFE_ASSET_NAME_CHARS.sub("-", dataset_id.lower()).strip("-")[:max_slug_length].rstrip("-")
    return f"{slug}-{digest}"


class _AzureMLAssetClientAdapter:
    """Adapt the Azure ML SDK to the narrow registration client contract."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get_data_asset(self, name: str, version: str) -> AzureMLAssetRegistration | None:
        try:
            asset = self._client.data.get(name=name, version=version)
        except Exception as exc:
            try:
                from azure.core.exceptions import ResourceNotFoundError
            except ImportError:
                raise exc
            if isinstance(exc, ResourceNotFoundError):
                return None
            raise
        return AzureMLAssetRegistration(
            name=str(asset.name),
            version=str(asset.version),
            path=str(asset.path),
            properties={str(key): str(value) for key, value in (asset.properties or {}).items()},
            tags={str(key): str(value) for key, value in (asset.tags or {}).items()},
        )

    def create_data_asset(self, registration: AzureMLAssetRegistration) -> None:
        from azure.ai.ml.constants import AssetTypes
        from azure.ai.ml.entities import Data

        asset = Data(
            name=registration.name,
            version=registration.version,
            path=registration.path,
            type=AssetTypes.URI_FOLDER,
            properties=registration.properties,
            tags=registration.tags,
        )
        self._client.data.create_or_update(asset)


def _parse_checksums(payload: bytes) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in payload.decode("utf-8").splitlines():
        digest, separator, relative_path = line.partition("  ")
        if separator != "  " or not re.fullmatch(r"[0-9a-f]{64}", digest) or relative_path in checksums:
            raise ValueError("Invalid published checksums.sha256")
        checksums[relative_path] = digest
    return checksums


def _verified_digest(relative_path: str, payload: bytes, checksums: dict[str, str]) -> str:
    expected = checksums.get(relative_path)
    if expected is None:
        raise ValueError(f"Published checksums are missing {relative_path}")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise ValueError(f"Published {relative_path} SHA-256 mismatch")
    return actual


def _bounded_tag_value(value: str) -> str:
    if len(value) <= _AZUREML_TAG_VALUE_MAX_LENGTH:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    suffix = f"...sha256:{digest}"
    return f"{value[: _AZUREML_TAG_VALUE_MAX_LENGTH - len(suffix)]}{suffix}"
