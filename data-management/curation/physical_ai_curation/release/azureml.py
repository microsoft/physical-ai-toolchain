"""Optional Azure ML data-asset registration for verified published releases.

The adapter verifies the release before it constructs or calls an Azure ML
client. The Azure ML SDK is imported lazily and is never required by release
verification. Registration only creates a missing asset or confirms an existing
asset; it never updates one.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from physical_ai_curation.release.errors import IncompleteReleaseError, ReleaseVerificationError
from physical_ai_curation.release.models import ReleaseMarker, VerificationReceipt
from physical_ai_curation.release.store import ReleaseStore
from physical_ai_curation.release.verification import VerifiedPublication, verify_published_publication

ASSET_NAME_MAX_LENGTH = 255
ASSET_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,29}")
TAG_VALUE_MAX_LENGTH = 256
DIGEST_SUFFIX_LENGTH = 12

ErrorCategory = Literal[
    "not_configured",
    "not_published",
    "verification_failed",
    "invalid_request",
    "conflict",
    "provider_error",
]

_MESSAGES: dict[ErrorCategory, str] = {
    "not_configured": "Azure ML SDK is unavailable or the client is not configured",
    "not_published": "release has no published marker",
    "verification_failed": "release verification failed",
    "invalid_request": "registration request is invalid",
    "conflict": "existing Azure ML data asset conflicts with the verified release",
    "provider_error": "Azure ML data asset operation failed",
}


class AzureMLRegistrationError(RuntimeError):
    """Bounded registration failure. The message is fixed and carries no provider or path detail."""

    def __init__(self, category: ErrorCategory) -> None:
        super().__init__(_MESSAGES[category])
        self.category = category


@dataclass(frozen=True)
class AzureMLDataAsset:
    """Data asset identity, location, and bounded metadata."""

    name: str
    version: str
    path: str
    properties: dict[str, str]
    tags: dict[str, str]
    asset_type: str = "uri_folder"


class AzureMLAssetClient(Protocol):
    """Narrow data-asset surface used by registration; implementations may wrap the Azure ML SDK."""

    def get_data_asset(self, name: str, version: str) -> AzureMLDataAsset | None: ...

    def create_data_asset(self, asset: AzureMLDataAsset) -> None: ...


@dataclass(frozen=True)
class AzureMLRegistration:
    """Successful registration outcome."""

    name: str
    version: str
    path: str
    outcome: Literal["created", "already_registered"]


def is_valid_azureml_asset_version(value: str) -> bool:
    """Return whether a release name can be used as an Azure ML asset version."""
    return ASSET_VERSION_PATTERN.fullmatch(value) is not None


def azureml_data_asset_name(source_dataset_id: str) -> str:
    """Return a lowercase, bounded asset name with a stable digest of the source dataset identity."""
    slug = re.sub(r"[^a-z0-9]+", "-", source_dataset_id.lower()).strip("-") or "dataset"
    digest = hashlib.sha256(source_dataset_id.encode("utf-8")).hexdigest()[:DIGEST_SUFFIX_LENGTH]
    room = ASSET_NAME_MAX_LENGTH - DIGEST_SUFFIX_LENGTH - 1
    return f"{slug[:room].strip('-') or 'dataset'}-{digest}"


def bounded_tag_value(value: str) -> str:
    """Return the value unchanged when it fits, otherwise a prefix plus a stable digest suffix."""
    if len(value) <= TAG_VALUE_MAX_LENGTH:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:DIGEST_SUFFIX_LENGTH]
    keep = TAG_VALUE_MAX_LENGTH - DIGEST_SUFFIX_LENGTH - 1
    return f"{value[:keep]}-{digest}"


def azureml_asset_path(datastore_name: str, release_prefix: str) -> str:
    """Return the datastore URI of the release directory."""
    prefix = release_prefix.strip("/")
    if not datastore_name or "/" in datastore_name or not prefix:
        raise AzureMLRegistrationError("invalid_request")
    return f"azureml://datastores/{datastore_name}/paths/{prefix}/"


def create_azureml_client(
    *,
    subscription_id: str,
    resource_group: str,
    workspace_name: str,
    credential: Any | None = None,
) -> AzureMLAssetClient:
    """Build a data-asset client from the Azure ML SDK, or raise a bounded not_configured error."""
    if not subscription_id or not resource_group or not workspace_name:
        raise AzureMLRegistrationError("invalid_request")
    try:
        from azure.ai.ml import MLClient
        from azure.identity import DefaultAzureCredential
    except ImportError:
        raise AzureMLRegistrationError("not_configured") from None
    ml_client = MLClient(
        credential=credential if credential is not None else DefaultAzureCredential(),
        subscription_id=subscription_id,
        resource_group_name=resource_group,
        workspace_name=workspace_name,
    )
    return _MLClientDataAssets(
        ml_client,
        subscription_id=subscription_id,
        resource_group=resource_group,
        workspace_name=workspace_name,
    )


def register_published_release(
    store: ReleaseStore,
    *,
    expected_manifest_sha256: str,
    datastore_name: str,
    release_prefix: str,
    expected_release_name: str | None = None,
    expected_source_dataset_id: str | None = None,
    client: AzureMLAssetClient | None = None,
    client_factory: Callable[[], AzureMLAssetClient] | None = None,
) -> AzureMLRegistration:
    """Verify a published release, then create or confirm its Azure ML data asset.

    Verification runs before any client is built. The marker is read only as part
    of verification. Exactly one of ``client`` or ``client_factory`` must be given.
    """
    if (client is None) == (client_factory is None):
        raise ValueError("provide exactly one of client or client_factory")
    verified = _verify_published(
        store,
        expected_manifest_sha256,
        expected_release_name=expected_release_name,
        expected_source_dataset_id=expected_source_dataset_id,
    )
    marker = verified.marker
    if not is_valid_azureml_asset_version(marker.release_name):
        raise AzureMLRegistrationError("invalid_request")
    asset = AzureMLDataAsset(
        name=azureml_data_asset_name(marker.source_dataset_id),
        version=marker.release_name,
        path=azureml_asset_path(datastore_name, release_prefix),
        properties=_asset_properties(marker, verified.receipt),
        tags=_asset_tags(marker),
    )
    asset_client = _resolve_client(client, client_factory)
    existing = _provider_call(lambda: asset_client.get_data_asset(asset.name, asset.version))
    if existing is None:
        _create(asset_client, asset)
        return AzureMLRegistration(asset.name, asset.version, asset.path, "created")
    if _conflicts(existing, asset):
        raise AzureMLRegistrationError("conflict")
    return AzureMLRegistration(asset.name, asset.version, asset.path, "already_registered")


def _verify_published(
    store: ReleaseStore,
    expected_manifest_sha256: str,
    *,
    expected_release_name: str | None,
    expected_source_dataset_id: str | None,
) -> VerifiedPublication:
    try:
        return verify_published_publication(
            store,
            expected_manifest_sha256,
            expected_release_name=expected_release_name,
            expected_source_dataset_id=expected_source_dataset_id,
        )
    except IncompleteReleaseError:
        raise AzureMLRegistrationError("not_published") from None
    except ReleaseVerificationError:
        raise AzureMLRegistrationError("verification_failed") from None
    except Exception:
        raise AzureMLRegistrationError("provider_error") from None


def _resolve_client(
    client: AzureMLAssetClient | None,
    client_factory: Callable[[], AzureMLAssetClient] | None,
) -> AzureMLAssetClient:
    if client is not None:
        return client
    if client_factory is not None:
        return _provider_call(client_factory)
    raise ValueError("provide exactly one of client or client_factory")


def _asset_properties(marker: ReleaseMarker, receipt: VerificationReceipt) -> dict[str, str]:
    values = {
        "package_id": marker.package_id,
        "release_name": marker.release_name,
        "source_dataset_id": marker.source_dataset_id,
        "manifest_sha256": marker.manifest_sha256,
        "inventory_sha256": marker.inventory_sha256,
        "checksums_sha256": marker.checksums_sha256,
        "schema_version": marker.schema_version,
        "content_digest_count": str(receipt.content_digest_count),
        "storage_revision_count": str(receipt.storage_revision_count),
        "uploaded_content_count": str(receipt.uploaded_content_count),
    }
    return values


def _asset_tags(marker: ReleaseMarker) -> dict[str, str]:
    return {
        "package_id": bounded_tag_value(marker.package_id),
        "release_name": bounded_tag_value(marker.release_name),
        "source_dataset_id": bounded_tag_value(marker.source_dataset_id),
        "manifest_sha256": marker.manifest_sha256,
    }


def _conflicts(existing: AzureMLDataAsset, expected: AzureMLDataAsset) -> bool:
    if existing.asset_type != expected.asset_type or existing.path.rstrip("/") != expected.path.rstrip("/"):
        return True
    return any(existing.properties.get(key) != value for key, value in expected.properties.items())


def _provider_call[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except AzureMLRegistrationError:
        raise
    except Exception:
        raise AzureMLRegistrationError("provider_error") from None


def _create(client: AzureMLAssetClient, asset: AzureMLDataAsset) -> None:
    try:
        client.create_data_asset(asset)
    except Exception as exc:
        category: ErrorCategory = "conflict" if getattr(exc, "status_code", None) == 409 else "provider_error"
        raise AzureMLRegistrationError(category) from None


class _MLClientDataAssets:
    """Adapts the Azure ML SDK data operations to `AzureMLAssetClient`."""

    def __init__(self, ml_client: Any, *, subscription_id: str, resource_group: str, workspace_name: str) -> None:
        self._data = ml_client.data
        self._workspace_path = (
            f"/{subscription_id}/resourcegroups/{resource_group}/workspaces/{workspace_name}/datastores/"
        )

    def get_data_asset(self, name: str, version: str) -> AzureMLDataAsset | None:
        try:
            asset = self._data.get(name=name, version=version)
        except Exception as exc:
            if getattr(exc, "status_code", None) == 404:
                return None
            raise
        path = asset.path
        parsed = urlsplit(path)
        if (
            parsed.scheme == "azureml"
            and parsed.netloc == "subscriptions"
            and not parsed.query
            and not parsed.fragment
            and parsed.path[: len(self._workspace_path)].casefold() == self._workspace_path.casefold()
        ):
            path = f"azureml://datastores/{parsed.path[len(self._workspace_path) :]}"
        return AzureMLDataAsset(
            name=asset.name,
            version=asset.version,
            path=path,
            properties=dict(asset.properties or {}),
            tags=dict(asset.tags or {}),
            asset_type=asset.type,
        )

    def create_data_asset(self, asset: AzureMLDataAsset) -> None:
        from azure.ai.ml.constants import AssetTypes
        from azure.ai.ml.entities import Data

        self._data.create_or_update(
            Data(
                name=asset.name,
                version=asset.version,
                path=asset.path,
                type=AssetTypes.URI_FOLDER,
                properties=asset.properties,
                tags=asset.tags,
            )
        )
