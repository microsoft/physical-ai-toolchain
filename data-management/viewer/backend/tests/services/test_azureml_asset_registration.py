"""Behavior tests for optional Azure ML release asset registration."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.api.config import AppConfig
from src.api.models.releases import (
    QualityEvidenceReference,
    ReleaseFile,
    ReleaseFormat,
    ReleaseManifest,
    ReleaseStatistics,
    ReleaseStatisticsProfile,
    canonical_json_bytes,
)
from src.api.models.reviews import QualityOutcome, SourceFileIdentity, SourceIdentity
from src.api.release.azureml_asset_registration import (
    AzureMLAssetRegistration,
    AzureMLAssetRegistrationConflictError,
    AzureMLAssetRegistrationService,
    StatisticsUnavailableError,
    azureml_data_asset_name,
    create_azureml_asset_registration_service,
    hydrate_registration_evidence,
    validate_azureml_data_asset_version,
)


class _PublishedReleaseReader:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads

    async def read_published_file(self, dataset_id: str, release_id: str, relative_path: str) -> bytes:
        assert dataset_id == "Warehouse:Arm"
        assert release_id == "release-1"
        return self.payloads[relative_path]

    def published_release_url(self, dataset_id: str, release_id: str) -> str:
        assert dataset_id == "Warehouse:Arm"
        assert release_id == "release-1"
        return "https://storage.example/container/exports/releases/Warehouse%3AArm/release-1"


class _AssetClient:
    def __init__(self, existing: AzureMLAssetRegistration | None = None) -> None:
        self.existing = existing
        self.created: list[AzureMLAssetRegistration] = []

    def get_data_asset(self, name: str, version: str) -> AzureMLAssetRegistration | None:
        if self.existing is not None:
            assert (name, version) == (self.existing.name, self.existing.version)
        return self.existing

    def create_data_asset(self, registration: AzureMLAssetRegistration) -> None:
        self.created.append(registration)


def _app_config(*, enabled: bool) -> AppConfig:
    return AppConfig(
        storage_backend="azure",
        data_path="./data",
        azure_account_name="storage",
        azure_dataset_container="datasets",
        azure_annotation_container=None,
        azure_sas_token=None,
        backend_host="127.0.0.1",
        backend_port=8000,
        azureml_registration_enabled=enabled,
        azure_subscription_id="subscription" if enabled else None,
        azure_resource_group="resource-group" if enabled else None,
        azureml_workspace_name="workspace" if enabled else None,
    )


def _published_payloads() -> dict[str, bytes]:
    source = SourceIdentity(
        dataset_id="Warehouse:Arm",
        episode_index=0,
        source_format="lerobot",
        format_version="3.0",
        source_digest="a" * 64,
        files=(SourceFileIdentity(relative_path="meta/info.json", size_bytes=2, sha256="b" * 64),),
    )
    statistics = canonical_json_bytes(
        ReleaseStatistics(
            statistics_profile=ReleaseStatisticsProfile(),
            tool_versions={"numpy": "2.5.3", "scipy": "1.18.1"},
            feature_schema_sha256="c" * 64,
            episodes=(),
        )
    )
    statistics_digest = hashlib.sha256(statistics).hexdigest()
    manifest = ReleaseManifest(
        release_id="release-1",
        created_at=datetime(2026, 9, 25, tzinfo=UTC),
        actor_id="publisher",
        source_provenance=(source,),
        accepted_decision_ids=("decision-1",),
        rejected_decision_ids=(),
        excluded_episode_indices=(),
        episode_index_mapping={0: 0},
        source_formats=(ReleaseFormat(name="lerobot", version="3.0"),),
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        adapter_versions={"lerobot": "0.6.1"},
        tool_versions={"dataviewer": "0.1.0"},
        feature_schema={},
        candidate_count=1,
        accepted_count=1,
        rejected_count=0,
        excluded_count=0,
        nonincluded_count=0,
        episode_count=1,
        frame_count=1,
        quality_evidence=(
            QualityEvidenceReference(
                source_episode_index=0,
                release_episode_index=0,
                decision_id="decision-1",
                quality_run_id="quality-1",
                quality_report_path="metadata/quality/quality-1.json",
                check_set_version="1.0.0",
                required_outcome=QualityOutcome.PASS,
            ),
        ),
        files=(
            ReleaseFile(
                path="metadata/release-statistics.json",
                size_bytes=len(statistics),
                sha256=statistics_digest,
            ),
        ),
    )
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    checksums = (
        f"{manifest_digest}  metadata/release-manifest.json\n{statistics_digest}  metadata/release-statistics.json\n"
    ).encode()
    marker = json.dumps(
        {
            "dataset_id": "Warehouse:Arm",
            "owner": "job-1",
            "release_id": "release-1",
            "schema_version": "1.0.0",
        }
    ).encode()
    return {
        ".published.json": marker,
        "metadata/release-manifest.json": manifest_bytes,
        "metadata/release-statistics.json": statistics,
        "checksums.sha256": checksums,
    }


@pytest.mark.parametrize("version", ["a", "Release_2026.09-25", "z" * 30])
def test_given_safe_release_id_when_validated_then_exact_version_is_preserved(version: str) -> None:
    assert validate_azureml_data_asset_version(version) == version


@pytest.mark.parametrize(
    "version",
    ["release:2026", "release@production", "release+candidate", "a" * 31],
)
def test_given_incompatible_release_id_when_validated_then_registration_is_rejected(version: str) -> None:
    with pytest.raises(ValueError, match="Azure ML data asset version"):
        validate_azureml_data_asset_version(version)


def test_given_normalization_collision_when_names_derived_then_hash_suffixes_disambiguate() -> None:
    first = azureml_data_asset_name("Warehouse:Arm")
    second = azureml_data_asset_name("warehouse-arm")

    assert first.startswith("warehouse-arm-")
    assert second.startswith("warehouse-arm-")
    assert first != second


def test_given_long_dataset_id_when_name_derived_then_result_is_stable_and_bounded() -> None:
    dataset_id = "Dataset_" + "A" * 247

    first = azureml_data_asset_name(dataset_id)
    second = azureml_data_asset_name(dataset_id)

    assert first == second
    assert len(first) <= 255
    assert first[0].isalnum()
    assert first[-1].isalnum()


def test_given_disabled_integration_when_service_created_then_client_factory_is_not_called() -> None:
    # Arrange
    called = False

    def client_factory(_config: AppConfig) -> Any:
        nonlocal called
        called = True
        raise AssertionError("disabled integration constructed a client")

    # Act
    service = create_azureml_asset_registration_service(_app_config(enabled=False), client_factory=client_factory)

    # Assert
    assert service is None
    assert called is False


def test_given_enabled_integration_without_optional_package_when_service_created_then_configuration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setitem(sys.modules, "azure.ai.ml", None)

    # Act & Assert
    with pytest.raises(RuntimeError, match="azureml"):
        create_azureml_asset_registration_service(_app_config(enabled=True))


def test_given_enabled_integration_without_workspace_configuration_then_service_creation_fails() -> None:
    # Arrange
    config = replace(_app_config(enabled=True), azureml_workspace_name=None)

    # Act & Assert
    with pytest.raises(ValueError, match="AZUREML_WORKSPACE_NAME"):
        create_azureml_asset_registration_service(config, client_factory=lambda _config: _AssetClient())


async def test_given_valid_published_evidence_when_hydrated_then_digest_bound_registration_is_returned() -> None:
    # Arrange
    reader = _PublishedReleaseReader(_published_payloads())

    # Act
    evidence = await hydrate_registration_evidence(reader, "Warehouse:Arm", "release-1")

    # Assert
    assert evidence.registration.version == "release-1"
    assert evidence.registration.name.startswith("warehouse-arm-")
    assert evidence.registration.path.endswith("/Warehouse%3AArm/release-1")
    assert evidence.registration.properties["dataset_id"] == "Warehouse:Arm"
    assert len(evidence.registration.properties["manifest_sha256"]) == 64
    assert len(evidence.registration.properties["statistics_sha256"]) == 64
    assert evidence.registration.properties["target_format"] == "lerobot:3.0"
    assert evidence.registration.properties["statistics_profile_version"] == "1.0.0"
    assert all(len(value) <= 256 for value in evidence.registration.tags.values())


async def test_given_tampered_published_statistics_when_hydrated_then_registration_is_rejected() -> None:
    # Arrange
    payloads = _published_payloads()
    payloads["metadata/release-statistics.json"] = b"{}\n"

    # Act & Assert
    with pytest.raises(ValueError, match="SHA-256"):
        await hydrate_registration_evidence(_PublishedReleaseReader(payloads), "Warehouse:Arm", "release-1")


async def test_given_pre_statistics_release_when_hydrated_then_registration_is_ineligible() -> None:
    # Arrange
    payloads = _published_payloads()
    manifest = ReleaseManifest.model_validate_json(payloads["metadata/release-manifest.json"])
    manifest_bytes = canonical_json_bytes(manifest.model_copy(update={"files": ()}))
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    payloads["metadata/release-manifest.json"] = manifest_bytes
    payloads["checksums.sha256"] = f"{manifest_digest}  metadata/release-manifest.json\n".encode()

    # Act & Assert
    with pytest.raises(StatisticsUnavailableError):
        await hydrate_registration_evidence(_PublishedReleaseReader(payloads), "Warehouse:Arm", "release-1")


async def test_given_new_asset_version_when_ensured_then_registration_is_created() -> None:
    # Arrange
    evidence = await hydrate_registration_evidence(
        _PublishedReleaseReader(_published_payloads()),
        "Warehouse:Arm",
        "release-1",
    )
    client = _AssetClient()

    # Act
    AzureMLAssetRegistrationService(client).ensure(evidence)

    # Assert
    assert client.created == [evidence.registration]


async def test_given_matching_asset_version_when_ensured_then_registration_is_idempotent() -> None:
    # Arrange
    evidence = await hydrate_registration_evidence(
        _PublishedReleaseReader(_published_payloads()),
        "Warehouse:Arm",
        "release-1",
    )
    client = _AssetClient(existing=evidence.registration)

    # Act
    AzureMLAssetRegistrationService(client).ensure(evidence)

    # Assert
    assert client.created == []


async def test_given_conflicting_asset_version_when_ensured_then_existing_asset_is_not_overwritten() -> None:
    # Arrange
    evidence = await hydrate_registration_evidence(
        _PublishedReleaseReader(_published_payloads()),
        "Warehouse:Arm",
        "release-1",
    )
    conflicting = AzureMLAssetRegistration(
        name=evidence.registration.name,
        version=evidence.registration.version,
        path="https://storage.example/other",
        properties=evidence.registration.properties,
        tags=evidence.registration.tags,
    )
    client = _AssetClient(existing=conflicting)

    # Act & Assert
    with pytest.raises(AzureMLAssetRegistrationConflictError):
        AzureMLAssetRegistrationService(client).ensure(evidence)
    assert client.created == []


def test_given_backend_manifest_when_inspected_then_azureml_dependency_is_optional() -> None:
    # Arrange
    manifest_path = Path(__file__).parents[2] / "pyproject.toml"

    # Act
    manifest = manifest_path.read_text(encoding="utf-8")

    # Assert
    assert "azureml = [" in manifest
    assert '"azure-ai-ml==1.35.0"' in manifest
