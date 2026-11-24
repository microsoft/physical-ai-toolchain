"""Independent, leased registration of an already committed release."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from physical_ai_curation.release.azureml import (
    AzureMLAssetClient,
    AzureMLDataAsset,
    AzureMLRegistrationError,
    azureml_data_asset_name,
    create_azureml_client,
    register_published_release,
)
from physical_ai_curation.release.canonical import canonical_json_bytes, parse_strict_json, sha256_hex
from physical_ai_curation.release.coordinator import _publication_runtime
from physical_ai_curation.release.jobs import AuthorityError, ReleaseJobStore
from physical_ai_curation.release.publication import PublicationAttempt
from physical_ai_curation.release.record_storage import BlobLocation, release_collection_location


@dataclass(frozen=True)
class AzureMLDestinationSettings:
    """Credential-free AML workspace and datastore binding for one Azure destination."""

    subscription_id: str
    resource_group: str
    workspace_name: str
    datastore_name: str

    def __post_init__(self) -> None:
        try:
            canonical_subscription = str(UUID(self.subscription_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Azure ML subscription must be a UUID") from error
        if canonical_subscription != self.subscription_id.lower():
            raise ValueError("Azure ML subscription must be a canonical UUID")
        if not re.fullmatch(r"[A-Za-z0-9_.()-]{1,90}", self.resource_group) or self.resource_group.endswith("."):
            raise ValueError("Azure ML resource group is invalid")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,32}", self.workspace_name):
            raise ValueError("Azure ML workspace is invalid")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,255}", self.datastore_name):
            raise ValueError("Azure ML datastore is invalid")


class _RegistrationSettings(Protocol):
    @property
    def azureml_destinations(self) -> Mapping[str, AzureMLDestinationSettings]:
        raise NotImplementedError

    @property
    def destinations(self) -> Mapping[str, Path | BlobLocation]:
        raise NotImplementedError


@dataclass(frozen=True)
class _LeasedClient:
    client: AzureMLAssetClient
    checkpoint: Callable[[], None]

    def get_data_asset(self, name: str, version: str) -> AzureMLDataAsset | None:
        self.checkpoint()
        result = self.client.get_data_asset(name, version)
        self.checkpoint()
        return result

    def create_data_asset(self, asset: AzureMLDataAsset) -> None:
        self.checkpoint()
        self.client.create_data_asset(asset)


@contextmanager
def _registration_lease(store: ReleaseJobStore, job_id: str, token: str) -> Iterator[Callable[[], None]]:
    stop = threading.Event()
    lost = threading.Event()

    def checkpoint() -> None:
        if lost.is_set():
            raise AuthorityError("registration authority was lost")
        store.renew_registration(job_id, token)

    def renew() -> None:
        while not stop.wait(min(5.0, store.settings.lease_seconds / 4)):
            try:
                store.renew_registration(job_id, token)
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=renew, name="release-registration-lease", daemon=True)
    thread.start()
    try:
        yield checkpoint
    finally:
        stop.set()
        thread.join()


def register_job(
    settings: _RegistrationSettings,
    store: ReleaseJobStore,
    job_id: str,
    *,
    retry: bool = False,
) -> bool:
    """Attempt only eligible committed jobs; failures never change publication or escape as provider text.

    The API must authorize the caller before invoking this internal worker interface.
    Returns False when disabled, ineligible, already settled, or owned by another registration attempt.
    """
    row = store.storage.snapshot()[job_id]
    if row["state"] != "committed":
        return False
    binding = parse_strict_json(store.execution_binding(job_id))
    alias = binding.get("destination")
    config = settings.azureml_destinations.get(alias)
    if config is None:
        return False
    base = settings.destinations.get(alias)
    if not isinstance(base, BlobLocation):
        return False
    source_id = row["attempt_source_dataset_id"]
    destination = release_collection_location(base, source_id.replace("--", "/"))
    if destination.as_text() != row["destination"]:
        raise AuthorityError("configured registration destination changed")
    prefix = "/".join(part for part in (destination.prefix, row["attempt_prefix"]) if part)
    target_digest = sha256_hex(canonical_json_bytes({**asdict(config), "destination": destination.as_text()}))
    token = store.begin_registration(
        job_id,
        asset_name=azureml_data_asset_name(source_id),
        asset_version=row["attempt_prefix"],
        target_digest=target_digest,
        retry=retry,
    )
    if token is None:
        return False
    outcome, category = "registered", None
    try:
        package_id, attempt_id = row["attempt_key"].split("/", 1)
        attempt = PublicationAttempt(package_id, attempt_id, row["attempt_prefix"], source_id)
        with (
            _registration_lease(store, job_id, token) as checkpoint,
            _publication_runtime(destination, attempt, store, job_id, row["fence"], None) as publisher,
        ):
            register_published_release(
                publisher.store,
                expected_manifest_sha256=row["commit_digest"],
                expected_release_name=row["attempt_prefix"],
                expected_source_dataset_id=source_id,
                datastore_name=config.datastore_name,
                release_prefix=prefix,
                client_factory=lambda: _LeasedClient(
                    create_azureml_client(
                        subscription_id=config.subscription_id,
                        resource_group=config.resource_group,
                        workspace_name=config.workspace_name,
                    ),
                    checkpoint,
                ),
            )
    except AzureMLRegistrationError as error:
        category = error.category
        outcome = category if category in {"not_configured", "conflict"} else "failed"
    except Exception:
        outcome, category = "failed", "provider_error"
    with suppress(AuthorityError):
        store.finish_registration(job_id, token, status=outcome, error_category=category)
    return True
