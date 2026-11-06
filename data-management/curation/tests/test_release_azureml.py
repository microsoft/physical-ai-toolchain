"""Azure ML data-asset registration adapter tests using a fake asset client and real published fixtures."""

from __future__ import annotations

import re
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from physical_ai_curation.release.azureml import (
    AzureMLDataAsset,
    AzureMLRegistration,
    AzureMLRegistrationError,
    azureml_data_asset_name,
    bounded_tag_value,
    create_azureml_client,
    is_valid_azureml_asset_version,
    register_published_release,
)
from physical_ai_curation.release.canonical import serialize_model, sha256_hex
from physical_ai_curation.release.models import (
    CHECKSUMS_PATH,
    MANIFEST_PATH,
    MARKER_PATH,
    RECEIPT_PATH,
    FrozenInput,
    ProvenanceMapping,
    ReleaseManifest,
    ReleasePreview,
)
from physical_ai_curation.release.packaging import build_inventory
from physical_ai_curation.release.store import LocalReleaseStore
from physical_ai_curation.release.verification import build_marker, verify_candidate
from pytest_mock import MockerFixture

FROZEN_PATH = "evidence/frozen-input.json"
PREVIEW_PATH = "evidence/preview.json"
INVENTORY_PATH = "release/inventory.json"
MEDIA_PATH = "media/front/episode-000.mp4"
DATASTORE = "workspaceblobstore"
PREFIX = "releases/release-a"
EXPECTED_PATH = f"azureml://datastores/{DATASTORE}/paths/{PREFIX}/"
DIGEST = "a" * 64


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("provider detail")
        self.status_code = status_code


class _FakeAssetClient:
    def __init__(self) -> None:
        self.assets: dict[tuple[str, str], AzureMLDataAsset] = {}
        self.created: list[AzureMLDataAsset] = []
        self.create_error: Exception | None = None

    def get_data_asset(self, name: str, version: str) -> AzureMLDataAsset | None:
        return self.assets.get((name, version))

    def create_data_asset(self, asset: AzureMLDataAsset) -> None:
        if self.create_error is not None:
            raise self.create_error
        self.created.append(asset)
        self.assets[(asset.name, asset.version)] = asset


def _payload_files() -> dict[str, bytes]:
    source_bytes = b'{"episode_index":0,"length":12}\n'
    frozen = serialize_model(
        FrozenInput(
            selected_episode_ids=["ep-000"],
            source_objects=[
                {
                    "assurance": "content_sha256",
                    "path": "data/episode-000.jsonl",
                    "revision": "fixture-v1",
                    "length": len(source_bytes),
                    "sha256": sha256_hex(source_bytes),
                }
            ],
        )
    )
    preview = serialize_model(ReleasePreview(frozen_input_sha256=sha256_hex(frozen), episode_count=1))
    return {
        "data/episode-000.jsonl": source_bytes,
        MEDIA_PATH: b"synthetic-media",
        FROZEN_PATH: frozen,
        PREVIEW_PATH: preview,
    }


def _manifest_for(files: dict[str, bytes]) -> ReleaseManifest:
    inventory_bytes = serialize_model(build_inventory(files))
    return ReleaseManifest(
        package_id="pkg-001",
        frozen_input_path=FROZEN_PATH,
        frozen_input_sha256=sha256_hex(files[FROZEN_PATH]),
        preview_path=PREVIEW_PATH,
        preview_sha256=sha256_hex(files[PREVIEW_PATH]),
        inventory_sha256=sha256_hex(inventory_bytes),
        producer={
            "application": {"name": "physical-ai-curation", "version": "0.1.0"},
            "adapter": {"name": "synthetic-fixture", "version": "1.0.0"},
            "source_format": {"name": "lerobot", "version": "v3.0"},
            "target_format": {"name": "lerobot", "version": "v3.0"},
            "tools": [{"name": "fixture-copy", "version": "1.0.0"}],
            "settings_json": '{"route":"copy"}',
        },
        mappings=[ProvenanceMapping(source="data/episode-000.jsonl", target=MEDIA_PATH)],
        episode_mappings=[
            {
                "output_episode_index": 0,
                "source_episode_id": "ep-000",
                "frames": [{"output_frame_index": 0, "source_frame_index": 0}],
            }
        ],
    )


def _build_package(
    root: Path,
    *,
    release_name: str = "release-a",
    source_dataset_id: str = "dataset-a",
    finalize: bool = True,
) -> str:
    files = _payload_files()
    inventory_bytes = serialize_model(build_inventory(files))
    manifest_bytes = serialize_model(_manifest_for(files))
    for relative, content in {**files, INVENTORY_PATH: inventory_bytes, MANIFEST_PATH: manifest_bytes}.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    if finalize:
        candidate = verify_candidate(LocalReleaseStore(root))
        (root / RECEIPT_PATH).write_bytes(candidate.receipt_bytes)
        (root / CHECKSUMS_PATH).write_bytes(candidate.checksums_bytes)
        (root / MARKER_PATH).write_bytes(
            build_marker(candidate, release_name=release_name, source_dataset_id=source_dataset_id)
        )
    return sha256_hex(manifest_bytes)


def _register(
    root: Path,
    manifest_sha256: str,
    client: _FakeAssetClient,
    **overrides: object,
) -> AzureMLRegistration:
    options: dict[str, object] = {
        "expected_manifest_sha256": manifest_sha256,
        "datastore_name": DATASTORE,
        "release_prefix": PREFIX,
        "client": client,
    }
    options.update(overrides)
    return register_published_release(LocalReleaseStore(root), **options)  # type: ignore[arg-type]


def _seed(client: _FakeAssetClient, asset: AzureMLDataAsset, **changes: object) -> None:
    seeded = replace(asset, **changes)
    client.assets[(seeded.name, seeded.version)] = seeded


def test_given_published_release_when_register_then_creates_asset(tmp_path: Path) -> None:
    # Arrange
    manifest_sha256 = _build_package(tmp_path)
    client = _FakeAssetClient()

    # Act
    result = _register(tmp_path, manifest_sha256, client)

    # Assert
    assert result.outcome == "created"
    asset = client.created[0]
    assert asset.path == EXPECTED_PATH
    assert asset.name == azureml_data_asset_name("dataset-a")
    assert asset.version == "release-a"
    assert set(asset.properties) == {
        "package_id",
        "release_name",
        "source_dataset_id",
        "manifest_sha256",
        "inventory_sha256",
        "checksums_sha256",
        "schema_version",
        "content_digest_count",
        "storage_revision_count",
        "uploaded_content_count",
    }
    assert asset.properties["manifest_sha256"] == manifest_sha256
    assert asset.properties["content_digest_count"] == "4"
    assert asset.properties["storage_revision_count"] == asset.properties["uploaded_content_count"] == "0"


def test_given_existing_matching_asset_when_register_then_reports_already_registered(tmp_path: Path) -> None:
    # Arrange
    manifest_sha256 = _build_package(tmp_path)
    client = _FakeAssetClient()
    _register(tmp_path, manifest_sha256, client)

    # Act
    result = _register(tmp_path, manifest_sha256, client)

    # Assert
    assert (result.outcome, len(client.created)) == ("already_registered", 1)


def test_given_existing_asset_at_other_path_when_register_then_conflict(tmp_path: Path) -> None:
    # Arrange
    manifest_sha256 = _build_package(tmp_path)
    first = _FakeAssetClient()
    _register(tmp_path, manifest_sha256, first)
    client = _FakeAssetClient()
    _seed(client, first.created[0], path="azureml://datastores/other/paths/x/")

    # Act
    with pytest.raises(AzureMLRegistrationError) as info:
        _register(tmp_path, manifest_sha256, client)

    # Assert
    assert info.value.category == "conflict"


def test_given_existing_asset_with_other_properties_when_register_then_conflict(tmp_path: Path) -> None:
    # Arrange
    manifest_sha256 = _build_package(tmp_path)
    first = _FakeAssetClient()
    _register(tmp_path, manifest_sha256, first)
    asset = first.created[0]
    client = _FakeAssetClient()
    _seed(client, asset, properties={**asset.properties, "manifest_sha256": "0" * 64})

    # Act
    with pytest.raises(AzureMLRegistrationError) as info:
        _register(tmp_path, manifest_sha256, client)

    # Assert
    assert info.value.category == "conflict"


def test_given_existing_file_asset_when_registered_then_conflict_without_write(tmp_path: Path) -> None:
    manifest_sha256 = _build_package(tmp_path)
    first = _FakeAssetClient()
    _register(tmp_path, manifest_sha256, first)
    client = _FakeAssetClient()
    _seed(client, first.created[0], asset_type="uri_file")
    with pytest.raises(AzureMLRegistrationError) as error:
        _register(tmp_path, manifest_sha256, client)
    assert error.value.category == "conflict"
    assert client.created == []


def test_given_unpublished_release_when_register_then_not_published(tmp_path: Path) -> None:
    # Arrange
    manifest_sha256 = _build_package(tmp_path, finalize=False)
    client = _FakeAssetClient()

    # Act
    with pytest.raises(AzureMLRegistrationError) as info:
        _register(tmp_path, manifest_sha256, client)

    # Assert
    assert info.value.category == "not_published"


def test_given_tampered_object_when_register_then_verification_failed_without_client_call(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    # Arrange
    manifest_sha256 = _build_package(tmp_path)
    (tmp_path / MEDIA_PATH).write_bytes(b"synthetic-audio")
    factory = mocker.MagicMock()

    # Act
    with pytest.raises(AzureMLRegistrationError) as info:
        register_published_release(
            LocalReleaseStore(tmp_path),
            expected_manifest_sha256=manifest_sha256,
            datastore_name=DATASTORE,
            release_prefix=PREFIX,
            client_factory=factory,
        )

    # Assert
    assert (info.value.category, factory.call_count) == ("verification_failed", 0)


def test_given_manifest_digest_mismatch_when_register_then_verification_failed(tmp_path: Path) -> None:
    # Arrange
    _build_package(tmp_path)
    client = _FakeAssetClient()

    # Act
    with pytest.raises(AzureMLRegistrationError) as info:
        _register(tmp_path, "0" * 64, client)

    # Assert
    assert info.value.category == "verification_failed"


def test_given_release_name_not_valid_as_version_when_register_then_invalid_request(tmp_path: Path) -> None:
    # Arrange
    manifest_sha256 = _build_package(tmp_path, release_name="bad name!")
    client = _FakeAssetClient()

    # Act
    with pytest.raises(AzureMLRegistrationError) as info:
        _register(tmp_path, manifest_sha256, client)

    # Assert
    assert info.value.category == "invalid_request"


def test_given_create_returns_status_409_when_register_then_conflict(tmp_path: Path) -> None:
    # Arrange
    manifest_sha256 = _build_package(tmp_path)
    client = _FakeAssetClient()
    client.create_error = _StatusError(409)

    # Act
    with pytest.raises(AzureMLRegistrationError) as info:
        _register(tmp_path, manifest_sha256, client)

    # Assert
    assert info.value.category == "conflict"


def test_given_create_raises_secret_bearing_error_when_register_then_bounded_provider_error(tmp_path: Path) -> None:
    # Arrange
    manifest_sha256 = _build_package(tmp_path)
    client = _FakeAssetClient()
    client.create_error = RuntimeError("secret-token")

    # Act
    with pytest.raises(AzureMLRegistrationError) as info:
        _register(tmp_path, manifest_sha256, client)

    # Assert
    assert (info.value.category, "secret" not in repr(info.value)) == ("provider_error", True)


def test_given_sdk_missing_when_create_client_then_not_configured(mocker: MockerFixture) -> None:
    # Arrange
    mocker.patch.dict(sys.modules, {"azure.ai.ml": None})

    # Act
    with pytest.raises(AzureMLRegistrationError) as info:
        create_azureml_client(
            subscription_id="subscription",
            resource_group="resource-group",
            workspace_name="workspace",
            credential=object(),
        )

    # Assert
    assert info.value.category == "not_configured"


def test_given_long_dataset_id_when_name_then_bounded_lowercase_with_digest() -> None:
    # Arrange
    dataset_id = "Org/" + "x" * 400

    # Act
    name = azureml_data_asset_name(dataset_id)

    # Assert
    assert len(name) <= 255 and re.fullmatch(r"[a-z0-9-]+-[0-9a-f]{12}", name) is not None


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("release-a", True),
        ("v1.0_2", True),
        ("", False),
        ("-bad", False),
        ("bad name", False),
        ("a" * 30, True),
        ("a" * 31, False),
        ("a" * 65, False),
    ],
)
def test_given_version_when_validate_then_matches_expected(version: str, expected: bool) -> None:
    # Arrange
    candidate = version

    # Act
    result = is_valid_azureml_asset_version(candidate)

    # Assert
    assert result is expected


def test_given_value_over_tag_limit_when_bound_then_length_is_256() -> None:
    # Arrange
    value = "x" * 300

    # Act
    bounded = bounded_tag_value(value)

    # Assert
    assert len(bounded) == 256


def test_given_long_source_identity_when_registered_then_only_tags_are_bounded(tmp_path: Path) -> None:
    source_id = "org/" + "x" * 270
    manifest_sha256 = _build_package(tmp_path, source_dataset_id=source_id)
    client = _FakeAssetClient()
    _register(tmp_path, manifest_sha256, client)
    asset = client.created[0]
    assert asset.properties["source_dataset_id"] == source_id
    assert asset.tags["package_id"] == asset.properties["package_id"]
    assert len(asset.tags["source_dataset_id"]) <= 256


def test_given_client_factory_failure_when_registered_then_provider_details_are_hidden(tmp_path: Path) -> None:
    manifest_sha256 = _build_package(tmp_path)

    def failed_factory() -> _FakeAssetClient:
        raise RuntimeError("secret-token")

    with pytest.raises(AzureMLRegistrationError) as error:
        register_published_release(
            LocalReleaseStore(tmp_path),
            expected_manifest_sha256=manifest_sha256,
            datastore_name=DATASTORE,
            release_prefix=PREFIX,
            client_factory=failed_factory,
        )
    assert error.value.category == "provider_error"
    assert "secret" not in repr(error.value)


@pytest.mark.parametrize("foreign", [False, True])
def test_given_scoped_sdk_uri_when_replayed_then_only_current_workspace_is_equivalent(
    tmp_path: Path,
    mocker: MockerFixture,
    foreign: bool,
) -> None:
    from physical_ai_curation.release.azureml import _MLClientDataAssets

    digest = _build_package(tmp_path)
    fake = _FakeAssetClient()
    _register(tmp_path, digest, fake)
    asset = fake.created[0]
    subscription = "00000000-0000-0000-0000-000000000001"
    workspace = "foreign-workspace" if foreign else "MLW-EXAMPLE"
    scoped_path = (
        f"azureml://subscriptions/{subscription}/resourcegroups/RG-EXAMPLE/"
        f"workspaces/{workspace}/datastores/{DATASTORE}/paths/{PREFIX}/"
    )
    sdk_asset = SimpleNamespace(**{**asset.__dict__, "path": scoped_path, "type": asset.asset_type})
    data = SimpleNamespace(get=lambda **kwargs: sdk_asset, create_or_update=mocker.Mock())
    client = _MLClientDataAssets(
        SimpleNamespace(data=data),
        subscription_id=subscription,
        resource_group="rg-example",
        workspace_name="mlw-example",
    )
    if foreign:
        with pytest.raises(AzureMLRegistrationError) as error:
            _register(tmp_path, digest, client)
        assert error.value.category == "conflict"
    else:
        assert _register(tmp_path, digest, client).outcome == "already_registered"
    data.create_or_update.assert_not_called()


def test_given_storage_failure_when_verified_then_provider_details_are_hidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_sha256 = _build_package(tmp_path)
    store = LocalReleaseStore(tmp_path)

    def unavailable() -> list:
        raise OSError("secret-token")

    monkeypatch.setattr(store, "list_objects", unavailable)
    with pytest.raises(AzureMLRegistrationError) as error:
        register_published_release(
            store,
            expected_manifest_sha256=manifest_sha256,
            datastore_name=DATASTORE,
            release_prefix=PREFIX,
            client=_FakeAssetClient(),
        )
    assert error.value.category == "provider_error"
    assert "secret" not in repr(error.value)
