"""Release contract round trips and standalone verification of miniature packages."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from physical_ai_curation.release.canonical import (
    canonical_json_bytes,
    normalize_release_path,
    parse_strict_json,
    serialize_model,
    sha256_hex,
)
from physical_ai_curation.release.errors import IncompleteReleaseError, ReleaseVerificationError
from physical_ai_curation.release.models import (
    CHECKSUMS_PATH,
    MANIFEST_PATH,
    MARKER_PATH,
    RECEIPT_PATH,
    FrozenInput,
    IntegrationStatus,
    Inventory,
    InventoryEntry,
    PackageIdentity,
    ProvenanceMapping,
    ReleaseManifest,
    ReleaseMarker,
    ReleasePreview,
    ReleaseResult,
    RevisionSourceObject,
    SourceObject,
    StorageRevisionEntry,
    UploadedContentEntry,
    VerificationReceipt,
)
from physical_ai_curation.release.packaging import build_inventory
from physical_ai_curation.release.store import LocalReleaseStore, ObjectStat
from physical_ai_curation.release.verification import (
    build_marker,
    verify_candidate,
    verify_published,
)
from pydantic import ValidationError

FROZEN_PATH = "evidence/frozen-input.json"
PREVIEW_PATH = "evidence/preview.json"
INVENTORY_PATH = "release/inventory.json"
DIGEST = "a" * 64


def _producer_dict() -> dict[str, object]:
    return {
        "application": {"name": "physical-ai-curation", "version": "0.1.0"},
        "adapter": {"name": "synthetic-fixture", "version": "1.0.0"},
        "source_format": {"name": "lerobot", "version": "v3.0"},
        "target_format": {"name": "lerobot", "version": "v3.0"},
        "tools": [{"name": "fixture-copy", "version": "1.0.0"}],
        "settings_json": '{"route":"copy"}',
    }


def _valid_manifest(**changes: object) -> ReleaseManifest:
    values: dict[str, object] = {
        "package_id": "pkg-001",
        "frozen_input_path": FROZEN_PATH,
        "frozen_input_sha256": DIGEST,
        "preview_path": PREVIEW_PATH,
        "preview_sha256": DIGEST,
        "inventory_sha256": DIGEST,
        "producer": _producer_dict(),
        "mappings": [],
    }
    values.update(changes)
    return ReleaseManifest(**values)


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
        "media/front/episode-000.mp4": b"synthetic-media",
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
        producer=_producer_dict(),
        mappings=[ProvenanceMapping(source="data/episode-000.jsonl", target="media/front/episode-000.mp4")],
        episode_mappings=[
            {
                "output_episode_index": 0,
                "source_episode_id": "ep-000",
                "frames": [{"output_frame_index": 0, "source_frame_index": 0}],
            }
        ],
    )


def _write_tree(root: Path, objects: dict[str, bytes]) -> None:
    for relative, content in objects.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _write_package(root: Path, files: dict[str, bytes], manifest: ReleaseManifest) -> str:
    inventory_bytes = serialize_model(build_inventory(files))
    manifest_bytes = serialize_model(manifest)
    _write_tree(root, {**files, INVENTORY_PATH: inventory_bytes, MANIFEST_PATH: manifest_bytes})
    return sha256_hex(manifest_bytes)


def _finalize(root: Path) -> None:
    candidate = verify_candidate(LocalReleaseStore(root))
    (root / RECEIPT_PATH).write_bytes(candidate.receipt_bytes)
    (root / CHECKSUMS_PATH).write_bytes(candidate.checksums_bytes)
    (root / MARKER_PATH).write_bytes(build_marker(candidate, release_name="pkg-001", source_dataset_id="sample"))


@dataclass(frozen=True)
class MiniaturePackage:
    root: Path
    manifest_sha256: str
    files: dict[str, bytes]


@pytest.fixture
def miniature(tmp_path: Path) -> MiniaturePackage:
    # Arrange
    files = _payload_files()
    manifest_sha = _write_package(tmp_path, files, _manifest_for(files))
    return MiniaturePackage(root=tmp_path, manifest_sha256=manifest_sha, files=files)


class TestSchemaRoundTrip:
    def test_given_manifest_when_serialize_then_parse_and_reserialize_is_byte_stable(self) -> None:
        # Arrange
        manifest = _valid_manifest()

        # Act
        content = serialize_model(manifest)
        parsed = ReleaseManifest.model_validate(parse_strict_json(content))

        # Assert
        assert parsed == manifest
        assert serialize_model(parsed) == content

    def test_given_receipt_when_serialize_then_parse_returns_same_model_and_bytes(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        content = verify_candidate(LocalReleaseStore(miniature.root)).receipt_bytes

        # Act
        parsed = VerificationReceipt.model_validate_json(content)

        # Assert
        assert parsed.package_id == "pkg-001"
        assert serialize_model(parsed) == content

    @pytest.mark.parametrize("version", ["1.0.0", "3.0.0"])
    def test_given_unsupported_major_when_validate_then_rejected(self, version: str) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            _valid_manifest(schema_version=version)

    @pytest.mark.parametrize("version", ["1", "1.0", "v1.0.0", "01.0.0", "1.0.0-rc1"])
    def test_given_malformed_version_when_validate_then_rejected(self, version: str) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            _valid_manifest(schema_version=version)

    def test_given_extra_field_when_validate_then_rejected(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            ReleaseManifest(**_valid_manifest().model_dump(), unexpected="value")

    def test_given_same_relative_paths_when_validate_mapping_then_namespaces_are_distinct(self) -> None:
        mapping = ProvenanceMapping(source="data/a.jsonl", target="data/a.jsonl")

        assert mapping.source == mapping.target

    def test_given_unsorted_inventory_when_validate_then_rejected(self) -> None:
        # Arrange
        entries = [
            InventoryEntry(path="media/b.mp4", length=1, sha256=DIGEST),
            InventoryEntry(path="data/a.jsonl", length=1, sha256=DIGEST),
        ]

        # Act / Assert
        with pytest.raises(ValidationError):
            Inventory(entries=entries)

    def test_given_duplicate_inventory_path_when_validate_then_rejected(self) -> None:
        # Arrange
        entries = [
            InventoryEntry(path="data/a.jsonl", length=1, sha256=DIGEST),
            InventoryEntry(path="data/a.jsonl", length=1, sha256=DIGEST),
        ]

        # Act / Assert
        with pytest.raises(ValidationError):
            Inventory(entries=entries)

    @pytest.mark.parametrize("control_path", [MANIFEST_PATH, RECEIPT_PATH, MARKER_PATH])
    def test_given_control_artifact_in_inventory_when_validate_then_rejected(self, control_path: str) -> None:
        # Arrange
        entries = [InventoryEntry(path=control_path, length=1, sha256=DIGEST)]

        # Act / Assert
        with pytest.raises(ValidationError):
            Inventory(entries=entries)

    def test_given_non_finite_number_when_serialize_then_rejected(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValueError):
            canonical_json_bytes({"value": float("nan")})

    def test_given_duplicate_key_when_parse_then_rejected(self) -> None:
        # Arrange
        content = b'{"schema_version": "1.0.0", "schema_version": "1.0.0"}'

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            parse_strict_json(content)

    def test_given_non_finite_token_when_parse_then_rejected(self) -> None:
        # Arrange
        content = b'{"value": NaN}'

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            parse_strict_json(content)

    @pytest.mark.parametrize(
        "raw",
        ["", "/absolute/file", "../escape", "a/../b", "a//b", "a/./b", "a\\b", "C:/drive", "bad\x00name"],
    )
    def test_given_unsafe_path_when_normalize_then_rejected(self, raw: str) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ReleaseVerificationError):
            normalize_release_path(raw)

    def test_given_safe_path_when_normalize_then_returned_unchanged(self) -> None:
        # Arrange
        raw = "media/front/episode-000.mp4"

        # Act
        result = normalize_release_path(raw)

        # Assert
        assert result == raw


class TestResultIdentity:
    @pytest.mark.parametrize(
        "claim",
        [
            {"origin": "supplied_receipt"},
            {"origin": "imported_timestamp"},
            {"origin": "unknown", "receipt_sha256": DIGEST},
            {"origin": "unknown", "claimed_timestamp": "2026-01-01T00:00:00Z"},
        ],
    )
    def test_given_inconsistent_recording_claim_when_validated_then_unobserved_facts_are_refused(
        self, claim: dict[str, str]
    ) -> None:
        from physical_ai_curation.release.models import RecordingClaim

        with pytest.raises(ValidationError):
            RecordingClaim(episode_id="ep-000", **claim)

    @pytest.mark.parametrize("collection", ["selected_episode_ids", "source_objects", "saved_resources"])
    def test_given_duplicate_frozen_identity_when_validated_then_ambiguous_authority_is_refused(
        self, collection: str
    ) -> None:
        frozen = FrozenInput.model_validate_json(_payload_files()[FROZEN_PATH]).model_dump(mode="json")
        if collection == "saved_resources":
            frozen[collection] = [{"resource_id": "annotation", "revision": "v1", "value_json": "{}"}]
        frozen[collection] *= 2

        with pytest.raises(ValidationError, match="unique"):
            FrozenInput.model_validate(frozen)

    @pytest.mark.parametrize("damage", ["shared_control_path", "duplicate_target"])
    def test_given_ambiguous_manifest_mapping_when_validated_then_distinct_bindings_are_required(
        self, damage: str
    ) -> None:
        manifest = _manifest_for(_payload_files()).model_dump(mode="json")
        if damage == "shared_control_path":
            manifest["frozen_input_path"] = manifest["preview_path"]
        else:
            manifest["mappings"] *= 2

        with pytest.raises(ValidationError):
            ReleaseManifest.model_validate(manifest)

    def test_given_registered_integration_when_not_published_then_rejected(self) -> None:
        # Arrange
        identity = PackageIdentity(package_id="pkg-001", manifest_sha256=DIGEST)

        # Act / Assert
        with pytest.raises(ValidationError):
            ReleaseResult(
                identity=identity,
                publication_state="candidate",
                integrations=[IntegrationStatus(name="azureml", state="registered")],
            )

    def test_given_published_without_integration_when_validate_then_accepted(self) -> None:
        # Arrange
        identity = PackageIdentity(
            package_id="pkg-001", manifest_sha256=DIGEST, release_name="pkg-001", source_dataset_id="sample"
        )

        # Act
        result = ReleaseResult(identity=identity, publication_state="published")

        # Assert
        assert result.discoverable is True
        assert result.integrations == []


class TestCandidateAndPublicVerification:
    @pytest.mark.parametrize(
        "damage",
        [
            "frozen_digest",
            "preview_digest",
            "outside_target",
            "preview_count",
            "preview_digest_binding",
            "preview_output",
        ],
    )
    def test_given_self_consistent_inventory_when_authoritative_bindings_conflict_then_verification_refuses(
        self, tmp_path: Path, damage: str
    ) -> None:
        files = _payload_files()
        if damage.startswith("preview_") and damage != "preview_digest":
            preview = ReleasePreview.model_validate_json(files[PREVIEW_PATH]).model_dump(mode="json")
            if damage == "preview_count":
                preview["episode_count"] = 2
            elif damage == "preview_digest_binding":
                preview["frozen_input_sha256"] = "0" * 64
            else:
                preview["actions"] = [
                    {
                        "object_path": "data/foreign.bin",
                        "action": "copy",
                        "route": "copy",
                        "tool": "fixture",
                        "destination_alias": "release",
                        "confirmation_required": False,
                    }
                ]
            files[PREVIEW_PATH] = serialize_model(ReleasePreview.model_validate(preview))
        manifest = _manifest_for(files)
        if damage in {"frozen_digest", "preview_digest"}:
            manifest = manifest.model_copy(
                update={"frozen_input_sha256" if damage == "frozen_digest" else "preview_sha256": "0" * 64}
            )
        elif damage == "outside_target":
            manifest = manifest.model_copy(
                update={"mappings": (ProvenanceMapping(source="data/episode-000.jsonl", target="data/foreign.bin"),)}
            )
        _write_package(tmp_path, files, manifest)
        before = {path: (tmp_path / path).read_bytes() for path in files}

        with pytest.raises(ReleaseVerificationError):
            verify_candidate(LocalReleaseStore(tmp_path))

        assert {path: (tmp_path / path).read_bytes() for path in files} == before
        assert not (tmp_path / MARKER_PATH).exists()

    def test_given_duplicate_adapter_listing_when_verified_then_ambiguous_object_identity_is_refused(
        self, miniature: MiniaturePackage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = LocalReleaseStore(miniature.root)
        objects = store.list_objects()
        monkeypatch.setattr(store, "list_objects", lambda: [*objects, objects[0]])

        with pytest.raises(ReleaseVerificationError, match="duplicate release object path"):
            verify_candidate(store)

        assert not (miniature.root / MARKER_PATH).exists()

    def test_given_inventory_payload_when_growing_after_admission_then_verification_refuses_extra_bytes(
        self, miniature: MiniaturePackage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = LocalReleaseStore(miniature.root)
        original_iter = store.iter_bytes
        grown = []

        def grow_then_read(path: str, *, chunk_size: int) -> Iterator[bytes]:
            if path == FROZEN_PATH:
                target = miniature.root / path
                target.write_bytes(target.read_bytes() + b" ")
                grown.append(path)
            yield from original_iter(path, chunk_size=chunk_size)

        monkeypatch.setattr(store, "iter_bytes", grow_then_read)
        with pytest.raises(ReleaseVerificationError, match="length mismatch"):
            verify_candidate(store)

        assert grown == [FROZEN_PATH]
        assert (miniature.root / FROZEN_PATH).read_bytes() == miniature.files[FROZEN_PATH] + b" "
        assert not (miniature.root / MARKER_PATH).exists()

    def test_given_published_package_when_expected_source_is_foreign_then_verification_refuses(
        self, miniature: MiniaturePackage
    ) -> None:
        _finalize(miniature.root)
        marker = (miniature.root / MARKER_PATH).read_bytes()

        with pytest.raises(ReleaseVerificationError, match="expected source dataset"):
            verify_published(LocalReleaseStore(miniature.root), expected_source_dataset_id="foreign")

        assert (miniature.root / MARKER_PATH).read_bytes() == marker

    def test_given_source_only_in_frozen_provenance_when_verify_then_original_payload_is_not_required(
        self, tmp_path: Path
    ) -> None:
        files = _payload_files()
        files[FROZEN_PATH] = serialize_model(
            FrozenInput(
                selected_episode_ids=["ep-000"],
                source_objects=[
                    {
                        "assurance": "content_sha256",
                        "path": "original/shared.parquet",
                        "revision": "source-v1",
                        "length": 9000,
                        "sha256": DIGEST,
                    }
                ],
            )
        )
        files[PREVIEW_PATH] = serialize_model(
            ReleasePreview(frozen_input_sha256=sha256_hex(files[FROZEN_PATH]), episode_count=1)
        )
        manifest = _manifest_for(files).model_copy(
            update={"mappings": (ProvenanceMapping(source="original/shared.parquet", target="data/episode-000.jsonl"),)}
        )
        _write_package(tmp_path, files, manifest)

        candidate = verify_candidate(LocalReleaseStore(tmp_path))

        assert candidate.result.publication_state == "candidate"
        assert not (tmp_path / "original/shared.parquet").exists()
        _finalize(tmp_path)
        assert verify_published(LocalReleaseStore(tmp_path)).discoverable

    @pytest.mark.parametrize("source", ["data/episode-000.jsonl", "unbound/source.parquet"])
    def test_given_mapping_when_verify_then_only_frozen_source_and_inventoried_target_are_valid(
        self, tmp_path: Path, source: str
    ) -> None:
        files = _payload_files()
        manifest = _manifest_for(files).model_copy(
            update={"mappings": (ProvenanceMapping(source=source, target="data/episode-000.jsonl"),)}
        )
        _write_package(tmp_path, files, manifest)

        if source == "data/episode-000.jsonl":
            assert verify_candidate(LocalReleaseStore(tmp_path)).result.publication_state == "candidate"
        else:
            with pytest.raises(ReleaseVerificationError, match="frozen source"):
                verify_candidate(LocalReleaseStore(tmp_path))

    def test_given_uninventoried_target_when_verify_then_rejected(self, tmp_path: Path) -> None:
        files = _payload_files()
        manifest = _manifest_for(files).model_copy(
            update={"mappings": (ProvenanceMapping(source="data/episode-000.jsonl", target="missing/output.parquet"),)}
        )
        _write_package(tmp_path, files, manifest)

        with pytest.raises(ReleaseVerificationError, match="outside the inventory"):
            verify_candidate(LocalReleaseStore(tmp_path))

    def test_given_candidate_package_when_verify_candidate_then_succeeds_without_marker(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        store = LocalReleaseStore(miniature.root)

        # Act
        candidate = verify_candidate(store)

        # Assert
        assert candidate.result.publication_state == "candidate"
        assert candidate.result.discoverable is False
        assert candidate.result.identity.manifest_sha256 == miniature.manifest_sha256

    def test_given_checkpoint_raises_when_verify_candidate_then_error_propagates_mid_payload(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        store = LocalReleaseStore(miniature.root)
        calls: list[int] = []

        def checkpoint() -> None:
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("stop")

        # Act / Assert
        with pytest.raises(RuntimeError, match="stop"):
            verify_candidate(store, checkpoint=checkpoint)
        assert len(calls) == 2

    def test_given_checkpoint_raises_when_verify_published_then_error_propagates_mid_payload(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        _finalize(miniature.root)
        store = LocalReleaseStore(miniature.root)
        calls: list[int] = []

        def checkpoint() -> None:
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("stop")

        # Act / Assert
        with pytest.raises(RuntimeError, match="stop"):
            verify_published(store, checkpoint=checkpoint)
        assert len(calls) == 2

    def test_given_candidate_without_marker_when_verify_published_then_incomplete(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        store = LocalReleaseStore(miniature.root)

        # Act / Assert
        with pytest.raises(IncompleteReleaseError):
            verify_published(store)

    def test_given_finalized_package_when_verify_published_then_publishes_identity(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        _finalize(miniature.root)

        # Act
        result = verify_published(LocalReleaseStore(miniature.root))

        # Assert
        assert result.publication_state == "published"
        assert result.discoverable is True
        assert result.identity.manifest_sha256 == miniature.manifest_sha256
        assert result.job is None
        assert result.integrations == []

    def test_given_expected_digest_mismatch_when_verify_candidate_then_rejected(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        store = LocalReleaseStore(miniature.root)

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_candidate(store, expected_manifest_sha256="0" * 64)

    def test_given_expected_digest_match_when_verify_candidate_then_succeeds(self, miniature: MiniaturePackage) -> None:
        # Arrange
        store = LocalReleaseStore(miniature.root)

        # Act
        candidate = verify_candidate(store, expected_manifest_sha256=miniature.manifest_sha256)

        # Assert
        assert candidate.result.identity.manifest_sha256 == miniature.manifest_sha256

    def test_given_expected_digest_mismatch_when_verify_published_then_rejected(
        self, miniature: MiniaturePackage
    ) -> None:
        _finalize(miniature.root)

        with pytest.raises(ReleaseVerificationError, match="expected"):
            verify_published(LocalReleaseStore(miniature.root), expected_manifest_sha256="0" * 64)

    def test_given_tampered_payload_when_verify_candidate_then_rejected(self, miniature: MiniaturePackage) -> None:
        # Arrange
        (miniature.root / "media/front/episode-000.mp4").write_bytes(b"synthetic-MEDIA")

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_candidate(LocalReleaseStore(miniature.root))

    def test_given_missing_inventory_object_when_verify_candidate_then_rejected(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        (miniature.root / "media/front/episode-000.mp4").unlink()

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_candidate(LocalReleaseStore(miniature.root))

    def test_given_unexpected_file_when_verify_candidate_then_rejected(self, miniature: MiniaturePackage) -> None:
        # Arrange
        (miniature.root / "media/front/undeclared.bin").write_bytes(b"hidden")

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_candidate(LocalReleaseStore(miniature.root))

    def test_given_marker_present_when_verify_candidate_then_rejected(self, miniature: MiniaturePackage) -> None:
        # Arrange
        _finalize(miniature.root)

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_candidate(LocalReleaseStore(miniature.root))

    def test_given_non_canonical_manifest_bytes_when_verify_candidate_then_rejected(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        manifest = json.loads((miniature.root / MANIFEST_PATH).read_bytes())
        (miniature.root / MANIFEST_PATH).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_candidate(LocalReleaseStore(miniature.root))

    def test_given_frozen_input_binding_mismatch_when_verify_candidate_then_rejected(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        manifest = _manifest_for(miniature.files).model_copy(update={"frozen_input_sha256": "0" * 64})
        (miniature.root / MANIFEST_PATH).write_bytes(serialize_model(manifest))

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_candidate(LocalReleaseStore(miniature.root))

    def test_given_length_mismatch_when_verify_candidate_then_rejected(self, miniature: MiniaturePackage) -> None:
        # Arrange
        store = _WrongLengthStore(LocalReleaseStore(miniature.root), "data/episode-000.jsonl", 3)

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_candidate(store)

    def test_given_tampered_receipt_when_verify_published_then_rejected(self, miniature: MiniaturePackage) -> None:
        # Arrange
        _finalize(miniature.root)
        (miniature.root / RECEIPT_PATH).write_bytes(b"{}")

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_published(LocalReleaseStore(miniature.root))

    def test_given_tampered_checksums_when_verify_published_then_rejected(self, miniature: MiniaturePackage) -> None:
        # Arrange
        _finalize(miniature.root)
        (miniature.root / CHECKSUMS_PATH).write_bytes(b"altered\n")

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_published(LocalReleaseStore(miniature.root))

    def test_given_marker_bound_to_other_manifest_when_verify_published_then_rejected(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        _finalize(miniature.root)
        marker = ReleaseMarker.model_validate_json((miniature.root / MARKER_PATH).read_bytes())
        (miniature.root / MARKER_PATH).write_bytes(
            serialize_model(marker.model_copy(update={"manifest_sha256": "0" * 64}))
        )

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_published(LocalReleaseStore(miniature.root))

    def test_given_undeclared_object_after_marker_when_verify_published_then_rejected(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        _finalize(miniature.root)
        (miniature.root / "media/front/late.bin").write_bytes(b"late")

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_published(LocalReleaseStore(miniature.root))


class TestLocalStoreRules:
    def test_given_directory_replaced_after_read_when_stream_finishes_then_rejected(
        self, miniature: MiniaturePackage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_read = os.read
        replaced = []

        def replacing_read(descriptor: int, length: int) -> bytes:
            chunk = real_read(descriptor, length)
            if chunk and not replaced:
                replaced.append(True)
                original = miniature.root / "media"
                moved = miniature.root / "old-media"
                original.rename(moved)
                (original / "front").mkdir(parents=True)
                os.link(moved / "front/episode-000.mp4", original / "front/episode-000.mp4")
            return chunk

        monkeypatch.setattr(os, "read", replacing_read)

        with pytest.raises(ReleaseVerificationError, match="changed"):
            list(LocalReleaseStore(miniature.root).iter_bytes("media/front/episode-000.mp4", chunk_size=4))

    def test_given_symlink_when_list_objects_then_rejected(self, miniature: MiniaturePackage) -> None:
        # Arrange
        link = miniature.root / "data" / "alias.jsonl"
        try:
            os.symlink(miniature.root / "data/episode-000.jsonl", link)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this platform")

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            LocalReleaseStore(miniature.root).list_objects()


def _frozen_dict(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {"selected_episode_ids": ["ep-000"]}
    values.update(changes)
    return values


def _action_dict(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "object_path": "media/front/episode-000.mp4",
        "action": "copy",
        "route": "direct_copy",
        "tool": "copy",
        "destination_alias": "release-store",
        "confirmation_required": False,
    }
    values.update(changes)
    return values


def _preview_dict(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "frozen_input_sha256": DIGEST,
        "episode_count": 1,
        "actions": [_action_dict()],
        "capacity": {"estimated_object_count": 1, "estimated_output_bytes": 15},
    }
    values.update(changes)
    return values


def _episode_mapping(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "output_episode_index": 0,
        "source_episode_id": "ep-000",
        "frames": [
            {"output_frame_index": 0, "source_frame_index": 0},
            {"output_frame_index": 1, "source_frame_index": 1},
        ],
    }
    values.update(changes)
    return values


def _manifest_dict(*episodes: dict[str, object]) -> dict[str, object]:
    return {
        "package_id": "pkg-001",
        "frozen_input_path": FROZEN_PATH,
        "frozen_input_sha256": DIGEST,
        "preview_path": PREVIEW_PATH,
        "preview_sha256": DIGEST,
        "inventory_sha256": DIGEST,
        "producer": _producer_dict(),
        "episode_mappings": list(episodes),
    }


class TestProducerIdentity:
    def test_given_missing_producer_when_validate_then_rejected(self) -> None:
        values = _manifest_dict(_episode_mapping())
        del values["producer"]

        with pytest.raises(ValidationError):
            ReleaseManifest.model_validate(values)

    def test_given_producer_when_round_trip_then_versions_and_settings_are_bound(self) -> None:
        manifest = _valid_manifest()

        parsed = ReleaseManifest.model_validate(parse_strict_json(serialize_model(manifest)))

        assert parsed.producer.application.version == "0.1.0"
        assert parsed.producer.target_format.version == "v3.0"
        assert parsed.producer.settings_json == '{"route":"copy"}'
        assert parsed.producer.feature == "curated-dataset-release"

    @pytest.mark.parametrize("settings", ['{ "route": "copy" }', '{"limit":NaN}', '{"x":1,"x":2}'])
    def test_given_invalid_settings_when_validate_producer_then_rejected(self, settings: str) -> None:
        producer = {**_producer_dict(), "settings_json": settings}

        with pytest.raises((ValidationError, ReleaseVerificationError)):
            _valid_manifest(producer=producer)


def _frame_row(output_frame: int = 0, **changes: object) -> bytes:
    values = {
        "schema_version": "2.0.0",
        "kind": "frame",
        "output_episode_index": 0,
        "source_episode_id": "ep-000",
        "output_frame_index": output_frame,
        "source_frame_index": output_frame + 3,
    }
    values.update(changes)
    return canonical_json_bytes(values) + b"\n"


def _write_lineage_package(root: Path, records: bytes, **changes: object) -> str:
    files = {**_payload_files(), "evidence/frame-lineage.jsonl": records}
    values = _manifest_for(files).model_dump()
    values.update(
        episode_mappings=[],
        lineage_stream={
            "path": "evidence/frame-lineage.jsonl",
            "sha256": sha256_hex(records),
            "record_count": 1,
            "episode_count": 1,
            **changes,
        },
    )
    return _write_package(root, files, ReleaseManifest.model_validate(values))


class TestStreamedLineage:
    def test_given_multiple_episodes_and_split_rows_when_verify_then_preserves_selection_order(
        self, tmp_path: Path
    ) -> None:
        class SplitRowStore(_NoWholePayloadStore):
            def iter_bytes(self, path: str, *, chunk_size: int) -> Iterator[bytes]:
                yield from super().iter_bytes(path, chunk_size=min(chunk_size, 31))

        files = _payload_files()
        frozen = FrozenInput.model_validate_json(files[FROZEN_PATH])
        files[FROZEN_PATH] = serialize_model(frozen.model_copy(update={"selected_episode_ids": ("ep-000", "ep-007")}))
        files[PREVIEW_PATH] = serialize_model(
            ReleasePreview(frozen_input_sha256=sha256_hex(files[FROZEN_PATH]), episode_count=2)
        )
        records = _frame_row() + _frame_row(0, output_episode_index=1, source_episode_id="ep-007")
        files["evidence/frame-lineage.jsonl"] = records
        values = _manifest_for(files).model_dump()
        values.update(
            episode_mappings=[],
            lineage_stream={
                "path": "evidence/frame-lineage.jsonl",
                "sha256": sha256_hex(records),
                "record_count": 2,
                "episode_count": 2,
            },
        )
        _write_package(tmp_path, files, ReleaseManifest.model_validate(values))

        receipt = json.loads(verify_candidate(SplitRowStore(tmp_path)).receipt_bytes)

        assert receipt["episode_mapping_count"] == 2
        assert receipt["frame_mapping_count"] == 2

    def test_given_large_lineage_when_verify_then_streams_beyond_control_limit(self, tmp_path: Path) -> None:
        records = b"".join(_frame_row(index) for index in range(12000))
        expected = _write_lineage_package(tmp_path, records, record_count=12000)

        candidate = verify_candidate(_NoWholePayloadStore(tmp_path), expected_manifest_sha256=expected)
        receipt = json.loads(candidate.receipt_bytes)

        assert len(records) > 1024 * 1024
        assert receipt["episode_mapping_count"] == 1
        assert receipt["frame_mapping_count"] == 12000
        _finalize(tmp_path)
        assert verify_published(_NoWholePayloadStore(tmp_path), expected_manifest_sha256=expected).discoverable

    @pytest.mark.parametrize(
        "records",
        [
            b"\n",
            b"{}\n",
            b"{\xff}\n",
            b'{"kind":"frame","kind":"frame"}\n',
            b" " * (64 * 1024 + 1) + b"\n",
            _frame_row().rstrip(b"\n"),
            _frame_row(1),
            _frame_row(source_episode_id="unselected"),
            _frame_row(schema_version="3.0.0"),
            _frame_row(output_frame_index=True),
            _frame_row() + _frame_row(1, source_frame_index=3),
            _frame_row() + _frame_row(0),
            _frame_row() + _frame_row(1, output_episode_index=2),
        ],
    )
    def test_given_invalid_lineage_when_verify_then_rejected(self, tmp_path: Path, records: bytes) -> None:
        _write_lineage_package(tmp_path, records)

        with pytest.raises(ReleaseVerificationError):
            verify_candidate(LocalReleaseStore(tmp_path))

    @pytest.mark.parametrize(
        "changes",
        [{"record_count": 2}, {"episode_count": 2}, {"sha256": "0" * 64}, {"record_schema_version": "2.1.0"}],
    )
    def test_given_mismatched_lineage_binding_when_verify_then_rejected(
        self, tmp_path: Path, changes: dict[str, object]
    ) -> None:
        _write_lineage_package(tmp_path, _frame_row(), **changes)

        with pytest.raises(ReleaseVerificationError):
            verify_candidate(LocalReleaseStore(tmp_path))

    def test_given_inline_and_streamed_lineage_when_validate_then_rejected(self) -> None:
        values = _manifest_dict(_episode_mapping())
        values["lineage_stream"] = {
            "path": "evidence/frame-lineage.jsonl",
            "sha256": DIGEST,
            "record_count": 1,
            "episode_count": 1,
        }

        with pytest.raises(ValidationError):
            ReleaseManifest.model_validate(values)


class TestStrictCanonicalValues:
    def test_given_overflowing_number_when_parse_then_rejected(self) -> None:
        # Arrange
        content = b'{"value": 1e999}'

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            parse_strict_json(content)

    def test_given_non_string_key_when_canonical_then_rejected(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValueError):
            canonical_json_bytes({"outer": {1: "value"}})


class TestFrozenInputContract:
    def test_given_saved_value_not_canonical_when_validate_then_rejected(self) -> None:
        # Arrange
        saved = [{"resource_id": "crop-1", "revision": "rev-7", "value_json": '{ "x": 1 }'}]

        # Act / Assert
        with pytest.raises(ValidationError):
            FrozenInput.model_validate(_frozen_dict(saved_resources=saved))

    def test_given_values_sources_and_claims_when_round_trip_then_byte_stable(self) -> None:
        # Arrange
        frozen = FrozenInput.model_validate(
            _frozen_dict(
                saved_resources=[{"resource_id": "crop-1", "revision": "rev-7", "value_json": '{"x":1}'}],
                source_objects=[
                    {
                        "assurance": "content_sha256",
                        "path": "data/episode-000.jsonl",
                        "revision": "rev-1",
                        "length": 12,
                        "sha256": DIGEST,
                    }
                ],
                contributors=[{"contributor_label": "contributor-a", "role": "author"}],
                recording_claims=[
                    {
                        "episode_id": "ep-000",
                        "origin": "imported_timestamp",
                        "claimed_timestamp": "2026-10-01T00:00:00Z",
                    }
                ],
            )
        )

        # Act
        content = serialize_model(frozen)

        # Assert
        assert serialize_model(FrozenInput.model_validate(parse_strict_json(content))) == content

    def test_given_contributor_principal_key_when_validate_then_rejected(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            FrozenInput.model_validate(
                _frozen_dict(contributors=[{"contributor_label": "a", "role": "author", "principal_key": "k"}])
            )

    def test_given_supplied_receipt_without_digest_when_validate_then_rejected(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            FrozenInput.model_validate(
                _frozen_dict(recording_claims=[{"episode_id": "ep-000", "origin": "supplied_receipt"}])
            )


class TestPreviewContract:
    def test_given_actions_and_capacity_when_round_trip_then_byte_stable(self) -> None:
        # Arrange
        preview = ReleasePreview.model_validate(_preview_dict())

        # Act
        content = serialize_model(preview)

        # Assert
        assert serialize_model(ReleasePreview.model_validate(parse_strict_json(content))) == content

    def test_given_unknown_action_when_validate_then_rejected(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            ReleasePreview.model_validate(_preview_dict(actions=[_action_dict(action="delete")]))


class TestEpisodeMappings:
    def test_given_contiguous_mapping_when_validate_then_accepted(self) -> None:
        # Arrange / Act
        manifest = ReleaseManifest.model_validate(_manifest_dict(_episode_mapping()))

        # Assert
        assert manifest.episode_mappings[0].source_episode_id == "ep-000"

    def test_given_non_contiguous_episode_index_when_validate_then_rejected(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            ReleaseManifest.model_validate(_manifest_dict(_episode_mapping(output_episode_index=1)))

    def test_given_non_contiguous_frame_index_when_validate_then_rejected(self) -> None:
        # Arrange
        frames = [
            {"output_frame_index": 0, "source_frame_index": 0},
            {"output_frame_index": 2, "source_frame_index": 1},
        ]

        # Act / Assert
        with pytest.raises(ValidationError):
            ReleaseManifest.model_validate(_manifest_dict(_episode_mapping(frames=frames)))

    def test_given_decreasing_source_frame_order_when_validate_then_rejected(self) -> None:
        # Arrange
        frames = [
            {"output_frame_index": 0, "source_frame_index": 5},
            {"output_frame_index": 1, "source_frame_index": 3},
        ]

        # Act / Assert
        with pytest.raises(ValidationError):
            ReleaseManifest.model_validate(_manifest_dict(_episode_mapping(frames=frames)))

    def test_given_duplicate_source_episode_when_validate_then_rejected(self) -> None:
        # Arrange
        second = _episode_mapping(output_episode_index=1)

        # Act / Assert
        with pytest.raises(ValidationError):
            ReleaseManifest.model_validate(_manifest_dict(_episode_mapping(), second))


MEDIA_REVISION = "c" * 64


class _RevisionStore:
    """Reports storage revisions for listed objects and refuses to stream revision-bound payloads."""

    def __init__(
        self,
        root: Path,
        revisions: dict[str, str],
        copy_digest: str | None = DIGEST,
        source_revision: str | None = MEDIA_REVISION,
    ) -> None:
        self._inner = LocalReleaseStore(root)
        self._revisions = revisions
        self._copy_digest = copy_digest
        self._source_revision = source_revision
        self.uploaded_sha256 = None

    def list_objects(self) -> list[ObjectStat]:
        return [
            ObjectStat(
                path=item.path,
                length=item.length,
                revision=self._revisions.get(item.path),
                copy_operation_sha256=self._copy_digest if item.path in self._revisions else None,
                source_revision=self._source_revision if item.path in self._revisions else None,
                source_identity=DIGEST if item.path in self._revisions else None,
                uploaded_sha256=self.uploaded_sha256 if item.path in self._revisions else None,
            )
            for item in self._inner.list_objects()
        ]

    def iter_bytes(self, path: str, *, chunk_size: int) -> Iterator[bytes]:
        if path in self._revisions:
            raise AssertionError(f"revision-bound payload streamed: {path}")
        return self._inner.iter_bytes(path, chunk_size=chunk_size)

    def read_control_bytes(self, path: str, *, max_bytes: int) -> bytes:
        return self._inner.read_control_bytes(path, max_bytes=max_bytes)


def _mixed_inventory(files: dict[str, bytes], revision_paths: frozenset[str]) -> Inventory:
    entries: list[InventoryEntry | StorageRevisionEntry] = []
    for path in sorted(files):
        content = files[path]
        if path in revision_paths:
            entries.append(
                StorageRevisionEntry(
                    path=path, length=len(content), revision=MEDIA_REVISION, copy_operation_sha256=DIGEST
                )
            )
        else:
            entries.append(InventoryEntry(path=path, length=len(content), sha256=sha256_hex(content)))
    return Inventory(entries=entries)


def _write_mixed_package(root: Path, revision_paths: frozenset[str]) -> None:
    files = _payload_files()
    media = files["media/front/episode-000.mp4"]
    source = files["data/episode-000.jsonl"]
    frozen = serialize_model(
        FrozenInput(
            selected_episode_ids=["ep-000"],
            source_objects=[
                SourceObject(
                    assurance="content_sha256",
                    path="data/episode-000.jsonl",
                    revision="fixture-v1",
                    length=len(source),
                    sha256=sha256_hex(source),
                ),
                RevisionSourceObject(
                    assurance="storage_revision",
                    path="videos/front/episode-000.mp4",
                    revision=MEDIA_REVISION,
                    length=len(media),
                    storage_identity=DIGEST,
                ),
            ],
        )
    )
    files[FROZEN_PATH] = frozen
    files[PREVIEW_PATH] = serialize_model(ReleasePreview(frozen_input_sha256=sha256_hex(frozen), episode_count=1))
    inventory_bytes = serialize_model(_mixed_inventory(files, revision_paths))
    manifest = _manifest_for(files).model_copy(
        update={
            "frozen_input_sha256": sha256_hex(frozen),
            "preview_sha256": sha256_hex(files[PREVIEW_PATH]),
            "inventory_sha256": sha256_hex(inventory_bytes),
            "mappings": (
                ProvenanceMapping(source="videos/front/episode-000.mp4", target="media/front/episode-000.mp4"),
            ),
        }
    )
    _write_tree(root, {**files, INVENTORY_PATH: inventory_bytes, MANIFEST_PATH: serialize_model(manifest)})


class TestStorageRevisionEvidence:
    @pytest.mark.parametrize("source_revision", [None, "e" * 64])
    def test_given_wrong_source_revision_when_verify_then_refused(
        self, tmp_path: Path, source_revision: str | None
    ) -> None:
        _write_mixed_package(tmp_path, frozenset({"media/front/episode-000.mp4"}))
        store = _RevisionStore(
            tmp_path,
            {"media/front/episode-000.mp4": MEDIA_REVISION},
            source_revision=source_revision,
        )

        with pytest.raises(ReleaseVerificationError, match="storage revision provenance changed"):
            verify_candidate(store)

    def test_given_missing_copy_provenance_when_verify_then_refused(self, tmp_path: Path) -> None:
        _write_mixed_package(tmp_path, frozenset({"media/front/episode-000.mp4"}))
        manifest_path = tmp_path / MANIFEST_PATH
        manifest = ReleaseManifest.model_validate_json(manifest_path.read_bytes())
        manifest_path.write_bytes(serialize_model(manifest.model_copy(update={"mappings": ()})))
        store = _RevisionStore(tmp_path, {"media/front/episode-000.mp4": MEDIA_REVISION})

        with pytest.raises(ReleaseVerificationError, match="requires one provenance mapping"):
            verify_candidate(store)

    def test_given_revision_entry_when_serialized_then_no_content_digest_field(self) -> None:
        # Arrange / Act
        entry = StorageRevisionEntry(
            path="media/front/episode-000.mp4", length=1, revision=MEDIA_REVISION, copy_operation_sha256=DIGEST
        )

        # Assert
        assert entry.assurance == "storage_revision"
        assert "sha256" not in entry.model_dump()

    def test_given_etag_as_content_digest_when_validate_then_rejected(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            InventoryEntry(path="data/a.jsonl", length=1, sha256='"0x8DC1"')

    def test_given_legacy_entry_without_assurance_when_validate_then_rejected(self) -> None:
        # Arrange
        entries = [{"path": "data/a.jsonl", "length": 1, "sha256": DIGEST}]

        # Act / Assert
        with pytest.raises(ValidationError):
            Inventory.model_validate({"schema_version": "2.0.0", "entries": entries})

    def test_given_revision_entry_without_revision_when_validate_then_rejected(self) -> None:
        # Arrange
        entries = [
            {
                "assurance": "storage_revision",
                "path": "media/front/episode-000.mp4",
                "length": 1,
                "copy_operation_sha256": DIGEST,
            }
        ]

        # Act / Assert
        with pytest.raises(ValidationError):
            Inventory.model_validate({"schema_version": "2.0.0", "entries": entries})

    def test_given_matching_revision_when_verify_then_payload_not_streamed(self, tmp_path: Path) -> None:
        # Arrange
        _write_mixed_package(tmp_path, frozenset({"media/front/episode-000.mp4"}))
        store = _RevisionStore(tmp_path, {"media/front/episode-000.mp4": MEDIA_REVISION})

        # Act
        receipt = json.loads(verify_candidate(store).receipt_bytes)

        # Assert
        assert receipt["content_digest_count"] == 3
        assert receipt["storage_revision_count"] == 1

    def test_given_replaced_revision_when_verify_candidate_then_same_length_change_is_rejected(
        self, tmp_path: Path
    ) -> None:
        # Arrange
        _write_mixed_package(tmp_path, frozenset({"media/front/episode-000.mp4"}))
        store = _RevisionStore(tmp_path, {"media/front/episode-000.mp4": "d" * 64})

        # Act / Assert
        with pytest.raises(ReleaseVerificationError, match="revision changed"):
            verify_candidate(store)

    def test_given_replaced_copy_operation_when_verify_candidate_then_copy_evidence_change_is_rejected(
        self, tmp_path: Path
    ) -> None:
        # Arrange
        _write_mixed_package(tmp_path, frozenset({"media/front/episode-000.mp4"}))
        store = _RevisionStore(tmp_path, {"media/front/episode-000.mp4": MEDIA_REVISION}, copy_digest="e" * 64)

        # Act / Assert
        with pytest.raises(ReleaseVerificationError, match="copy evidence changed"):
            verify_candidate(store)

    def test_given_revision_without_copy_operation_when_verify_then_fails_closed(self, tmp_path: Path) -> None:
        # Arrange
        _write_mixed_package(tmp_path, frozenset({"media/front/episode-000.mp4"}))
        store = _RevisionStore(tmp_path, {"media/front/episode-000.mp4": MEDIA_REVISION}, copy_digest=None)

        # Act / Assert
        with pytest.raises(ReleaseVerificationError, match="evidence unavailable"):
            verify_candidate(store)

    def test_given_no_listed_revision_when_verify_candidate_then_fails_closed(self, tmp_path: Path) -> None:
        # Arrange
        _write_mixed_package(tmp_path, frozenset({"media/front/episode-000.mp4"}))

        # Act / Assert
        with pytest.raises(ReleaseVerificationError, match="evidence unavailable"):
            verify_candidate(LocalReleaseStore(tmp_path))

    def test_given_revision_only_control_input_when_verify_candidate_then_rejected(self, tmp_path: Path) -> None:
        # Arrange
        _write_mixed_package(tmp_path, frozenset({FROZEN_PATH}))

        # Act / Assert
        with pytest.raises(ReleaseVerificationError, match="content digest"):
            verify_candidate(LocalReleaseStore(tmp_path))


@pytest.mark.parametrize("changed", [None, "revision", "operation", "digest", "missing"])
def test_given_uploaded_content_when_verify_then_checks_evidence_without_readback(
    tmp_path: Path, changed: str | None
) -> None:
    path = "media/front/episode-000.mp4"
    _write_mixed_package(tmp_path, frozenset())
    inventory = Inventory.model_validate_json((tmp_path / INVENTORY_PATH).read_bytes())
    content = (tmp_path / path).read_bytes()
    entry = UploadedContentEntry(
        path=path, length=len(content), revision=MEDIA_REVISION, sha256=sha256_hex(content), operation_sha256=DIGEST
    )
    inventory = inventory.model_copy(
        update={"entries": tuple(entry if item.path == path else item for item in inventory.entries)}
    )
    inventory_bytes = serialize_model(inventory)
    (tmp_path / INVENTORY_PATH).write_bytes(inventory_bytes)
    manifest = ReleaseManifest.model_validate_json((tmp_path / MANIFEST_PATH).read_bytes())
    (tmp_path / MANIFEST_PATH).write_bytes(
        serialize_model(manifest.model_copy(update={"inventory_sha256": sha256_hex(inventory_bytes)}))
    )
    store = _RevisionStore(
        tmp_path,
        {path: "d" * 64 if changed == "revision" else MEDIA_REVISION},
        copy_digest="e" * 64 if changed == "operation" else DIGEST,
        source_revision=None,
    )
    store.uploaded_sha256 = None if changed == "missing" else "f" * 64 if changed == "digest" else entry.sha256

    if changed is not None:
        with pytest.raises(ReleaseVerificationError, match="uploaded content"):
            verify_candidate(store)
    else:
        receipt = json.loads(verify_candidate(store).receipt_bytes)
        assert receipt["uploaded_content_count"] == 1
        assert receipt["storage_revision_count"] == 0
        assert receipt["content_digest_count"] == 3


class _ChangingListingStore(LocalReleaseStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.listings = 0

    def list_objects(self) -> list[ObjectStat]:
        self.listings += 1
        listed = super().list_objects()
        if self.listings == 1:
            return listed
        return [ObjectStat(path=item.path, length=item.length + 1) for item in listed]


class _NoWholePayloadStore(LocalReleaseStore):
    def read_control_bytes(self, path: str, *, max_bytes: int) -> bytes:
        if path not in {MANIFEST_PATH, INVENTORY_PATH, RECEIPT_PATH, CHECKSUMS_PATH, MARKER_PATH}:
            raise AssertionError(f"payload read as control bytes: {path}")
        return super().read_control_bytes(path, max_bytes=max_bytes)


class TestStreamingAndBounds:
    def test_given_object_added_during_payload_read_when_verify_candidate_then_rejected(
        self, miniature: MiniaturePackage
    ) -> None:
        class LateObjectStore(LocalReleaseStore):
            def iter_bytes(self, path: str, *, chunk_size: int) -> Iterator[bytes]:
                yield from super().iter_bytes(path, chunk_size=chunk_size)
                if path == "media/front/episode-000.mp4":
                    (miniature.root / "late.bin").write_bytes(b"not inventoried")

        with pytest.raises(ReleaseVerificationError, match="changed"):
            verify_candidate(LateObjectStore(miniature.root))

    def test_given_oversized_frozen_evidence_when_verify_candidate_then_rejected(
        self, miniature: MiniaturePackage
    ) -> None:
        frozen = FrozenInput(
            selected_episode_ids=["ep-000"],
            saved_resources=[{"resource_id": "labels", "revision": "1", "value_json": '"' + "a" * 1048576 + '"'}],
        )
        files = {**miniature.files, FROZEN_PATH: serialize_model(frozen)}
        files[PREVIEW_PATH] = serialize_model(
            ReleasePreview(frozen_input_sha256=sha256_hex(files[FROZEN_PATH]), episode_count=1)
        )
        _write_package(miniature.root, files, _manifest_for(files))

        with pytest.raises(ReleaseVerificationError, match="exceeds"):
            verify_candidate(LocalReleaseStore(miniature.root))

    def test_given_inconsistent_preview_episode_count_when_verify_candidate_then_rejected(
        self, miniature: MiniaturePackage
    ) -> None:
        files = dict(miniature.files)
        files[PREVIEW_PATH] = serialize_model(
            ReleasePreview(frozen_input_sha256=sha256_hex(files[FROZEN_PATH]), episode_count=2)
        )
        _write_package(miniature.root, files, _manifest_for(files))

        with pytest.raises(ReleaseVerificationError, match="episode count"):
            verify_candidate(LocalReleaseStore(miniature.root))

    def test_given_payloads_when_verify_candidate_then_never_read_whole(self, miniature: MiniaturePackage) -> None:
        # Arrange
        store = _NoWholePayloadStore(miniature.root)

        # Act
        candidate = verify_candidate(store)

        # Assert
        assert candidate.result.publication_state == "candidate"

    @pytest.mark.parametrize("object_path", ["media/front/episode-000.mp4", "videos/missing.mp4"])
    def test_given_preview_action_when_verify_candidate_then_output_must_be_inventoried(
        self, miniature: MiniaturePackage, object_path: str
    ) -> None:
        files = dict(miniature.files)
        files[PREVIEW_PATH] = serialize_model(
            ReleasePreview.model_validate(
                _preview_dict(
                    frozen_input_sha256=sha256_hex(files[FROZEN_PATH]),
                    actions=[_action_dict(object_path=object_path)],
                )
            )
        )
        _write_package(miniature.root, files, _manifest_for(files))

        if object_path not in files:
            with pytest.raises(ReleaseVerificationError, match=r"preview.*inventory"):
                verify_candidate(LocalReleaseStore(miniature.root))
        else:
            assert verify_candidate(LocalReleaseStore(miniature.root)).result.publication_state == "candidate"

    def test_given_fifo_when_iter_bytes_then_open_is_nonblocking_and_object_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable on this platform")
        os.mkfifo(tmp_path / "pipe")
        real_open = os.open

        def guarded_open(path: object, flags: int, mode: int = 0o600, *, dir_fd: int | None = None) -> int:
            if path == "pipe":
                assert flags & os.O_NONBLOCK, "opening an untrusted FIFO must not block"
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", guarded_open)
        with pytest.raises(ReleaseVerificationError, match="non-regular"):
            list(LocalReleaseStore(tmp_path).iter_bytes("pipe", chunk_size=4))

    def test_given_inventory_changed_between_start_and_end_when_verify_candidate_then_rejected(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        store = _ChangingListingStore(miniature.root)

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            verify_candidate(store)

    def test_given_oversized_manifest_when_verify_candidate_then_rejected_by_bound(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        original = (miniature.root / MANIFEST_PATH).read_bytes()
        (miniature.root / MANIFEST_PATH).write_bytes(b" " * (2 * 1024 * 1024) + original)

        # Act / Assert
        with pytest.raises(ReleaseVerificationError, match="exceeds"):
            verify_candidate(LocalReleaseStore(miniature.root))

    def test_given_candidate_when_receipt_then_reports_bytes_version_and_assurance(
        self, miniature: MiniaturePackage
    ) -> None:
        # Arrange
        store = LocalReleaseStore(miniature.root)

        # Act
        receipt = json.loads(verify_candidate(store).receipt_bytes)

        # Assert
        assert receipt["payload_byte_count"] == sum(len(content) for content in miniature.files.values())
        assert receipt["assurance"] == "candidate_internal_consistency"
        assert receipt["verifier_version"]

    def test_given_directory_swapped_for_symlink_during_open_when_iter_bytes_then_rejected(
        self, miniature: MiniaturePackage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        real_open = os.open
        swapped: list[bool] = []

        def swapping_open(path: object, flags: int, mode: int = 0o600, *, dir_fd: int | None = None) -> int:
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if path == "media" and not swapped:
                swapped.append(True)
                os.rename(miniature.root / "media", miniature.root / "media-moved")
                os.symlink(miniature.root / "media-moved", miniature.root / "media")
            return descriptor

        monkeypatch.setattr(os, "open", swapping_open)

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            list(LocalReleaseStore(miniature.root).iter_bytes("media/front/episode-000.mp4", chunk_size=4))

    def test_given_safe_primitive_unavailable_when_list_objects_then_fails_closed(
        self, miniature: MiniaturePackage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.delattr(os, "O_NOFOLLOW")

        # Act / Assert
        with pytest.raises(ReleaseVerificationError):
            LocalReleaseStore(miniature.root).list_objects()


class _WrongLengthStore:
    def __init__(self, inner: LocalReleaseStore, path: str, length: int) -> None:
        self._inner = inner
        self._path = path
        self._length = length

    def list_objects(self) -> list[ObjectStat]:
        return [
            ObjectStat(path=item.path, length=self._length if item.path == self._path else item.length)
            for item in self._inner.list_objects()
        ]

    def iter_bytes(self, path: str, *, chunk_size: int) -> Iterator[bytes]:
        return self._inner.iter_bytes(path, chunk_size=chunk_size)

    def read_control_bytes(self, path: str, *, max_bytes: int) -> bytes:
        return self._inner.read_control_bytes(path, max_bytes=max_bytes)
