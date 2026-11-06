"""Versioned release contract models.

Closure rules:

* Payload and evidence objects are listed in the inventory with normalized
  relative path, byte length, and SHA-256 digest.
* The inventory never lists itself or another control artifact.
* The manifest binds the inventory, frozen input, preview, and provenance
  mappings. It never hashes itself.
* The candidate receipt binds the manifest digest. The checksums file lists
  SHA-256 digests in `sha256sum` format for the manifest and every content
  entry that carries a content digest. Storage-revision entries are omitted
  and counted in the receipt. The marker binds the manifest, inventory,
  receipt, and checksums digests, and is the only artifact without a digest.
* Candidate closure is inventory plus manifest, inventory, receipt, and
  checksums, with no marker. Public closure is inventory plus all five control
  artifacts, with the marker required.

Each artifact carries its own `schema_version`. Only major version 2 is
supported; major version 1 inventories lack assurance discriminators and are
rejected.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from physical_ai_curation.release.canonical import canonical_json_bytes, normalize_release_path, parse_strict_json

SUPPORTED_MAJOR = 2
SCHEMA_VERSION = "2.0.0"
VERIFIER_VERSION = "2.0.0"
CANDIDATE_ASSURANCE = "candidate_internal_consistency"

MANIFEST_PATH = "release/manifest.json"
INVENTORY_PATH = "release/inventory.json"
RECEIPT_PATH = "release/receipt.json"
CHECKSUMS_PATH = "checksums.sha256"
MARKER_PATH = ".published.json"
CONTROL_PATHS = frozenset({MANIFEST_PATH, INVENTORY_PATH, RECEIPT_PATH, CHECKSUMS_PATH, MARKER_PATH})
CANDIDATE_CONTROL_PATHS = frozenset({MANIFEST_PATH, INVENTORY_PATH, RECEIPT_PATH, CHECKSUMS_PATH})

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def _check_schema_version(value: str) -> str:
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ValueError("schema_version must be MAJOR.MINOR.PATCH")
    if int(match.group(1)) != SUPPORTED_MAJOR:
        raise ValueError(f"unsupported schema major version: {match.group(1)}")
    return value


def _check_release_path(value: str) -> str:
    return normalize_release_path(value)


SchemaVersion = Annotated[str, AfterValidator(_check_schema_version)]
ReleasePath = Annotated[str, AfterValidator(_check_release_path)]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]


class ReleaseModel(BaseModel):
    """Strict, immutable base for release control and payload contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceObject(ReleaseModel):
    """Source object bound by a content SHA-256 digest at a specific revision."""

    assurance: Literal["content_sha256"] = "content_sha256"
    path: ReleasePath
    revision: NonEmptyStr
    length: NonNegativeInt
    sha256: Sha256Hex


class RevisionSourceObject(ReleaseModel):
    """Source object bound only by storage revision evidence; carries no content digest.

    Reads are conditioned on the listed revision. Content is never hashed to admit it.
    """

    assurance: Literal["storage_revision"] = "storage_revision"
    path: ReleasePath
    revision: NonEmptyStr
    length: NonNegativeInt
    storage_identity: Sha256Hex


SourceObjectRecord = Annotated[SourceObject | RevisionSourceObject, Field(discriminator="assurance")]


class SavedResource(ReleaseModel):
    """Saved resource value copied into the snapshot as canonical JSON text.

    Storing text rather than a mapping keeps the frozen value immutable.
    """

    resource_id: NonEmptyStr
    revision: NonEmptyStr
    value_json: NonEmptyStr

    @model_validator(mode="after")
    def validate_canonical_value(self) -> Self:
        raw = self.value_json.encode("utf-8")
        if canonical_json_bytes(parse_strict_json(raw)) != raw:
            raise ValueError("value_json must be canonical JSON")
        return self


class ContributorProjection(ReleaseModel):
    """Portable contributor label; private principal keys are never part of the package."""

    contributor_label: NonEmptyStr
    role: Literal["author", "initiator", "contributor"]


class RecordingClaim(ReleaseModel):
    """Recording claim kept as a claim; imported timestamps are not observed capture facts."""

    episode_id: NonEmptyStr
    origin: Literal["unknown", "supplied_receipt", "imported_timestamp"]
    receipt_sha256: Sha256Hex | None = None
    claimed_timestamp: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_origin_fields(self) -> Self:
        if self.origin == "supplied_receipt" and self.receipt_sha256 is None:
            raise ValueError("supplied_receipt claims require receipt_sha256")
        if self.origin == "imported_timestamp" and self.claimed_timestamp is None:
            raise ValueError("imported_timestamp claims require claimed_timestamp")
        if self.origin == "unknown" and (self.receipt_sha256 or self.claimed_timestamp):
            raise ValueError("unknown origin must not carry receipt or timestamp")
        return self


class FrozenInput(ReleaseModel):
    """Explicit selection and saved values accepted for a release."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    selected_episode_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    source_admission: Literal["unknown", "finalized_quiescent", "immutable_revision", "revision_bound"] = "unknown"
    source_objects: tuple[SourceObjectRecord, ...] = ()
    saved_resources: tuple[SavedResource, ...] = ()
    evidence_references: tuple[Sha256Hex, ...] = ()
    ledger_contribution_ids: tuple[NonEmptyStr, ...] = ()
    contributors: tuple[ContributorProjection, ...] = ()
    recording_claims: tuple[RecordingClaim, ...] = ()

    @model_validator(mode="after")
    def validate_unique_keys(self) -> Self:
        if len(set(self.selected_episode_ids)) != len(self.selected_episode_ids):
            raise ValueError("selected_episode_ids must be unique")
        paths = [item.path for item in self.source_objects]
        if len(set(paths)) != len(paths):
            raise ValueError("source object paths must be unique")
        resources = [item.resource_id for item in self.saved_resources]
        if len(set(resources)) != len(resources):
            raise ValueError("saved resource ids must be unique")
        return self


class PreviewAction(ReleaseModel):
    """Deterministic per-object action shown before submission."""

    object_path: ReleasePath
    action: Literal["copy", "transcode", "generate_metadata"]
    route: NonEmptyStr
    tool: NonEmptyStr
    destination_alias: NonEmptyStr
    confirmation_required: bool


class CapacityEstimate(ReleaseModel):
    """Deterministic capacity estimate; no sampled or mutable values."""

    estimated_object_count: NonNegativeInt
    estimated_output_bytes: NonNegativeInt


class ReleasePreview(ReleaseModel):
    """Read-only preview bound to a frozen input digest."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    frozen_input_sha256: Sha256Hex
    episode_count: NonNegativeInt
    warnings: tuple[NonEmptyStr, ...] = ()
    actions: tuple[PreviewAction, ...] = ()
    capacity: CapacityEstimate = Field(default=CapacityEstimate(estimated_object_count=0, estimated_output_bytes=0))


class InventoryEntry(ReleaseModel):
    """Closed object bound by a content SHA-256 digest; required for control-bound objects."""

    assurance: Literal["content_sha256"] = "content_sha256"
    path: ReleasePath
    length: NonNegativeInt
    sha256: Sha256Hex


class StorageRevisionEntry(ReleaseModel):
    """Payload bound by storage revision evidence for an unchanged storage-side copy.

    Carries no content digest. Verification compares the listed revision instead of streaming bytes.
    """

    assurance: Literal["storage_revision"] = "storage_revision"
    path: ReleasePath
    length: NonNegativeInt
    revision: NonEmptyStr
    copy_operation_sha256: Sha256Hex


class UploadedContentEntry(ReleaseModel):
    """Payload written by this release; its digest was verified against the streamed bytes at write time.

    Verification does not re-read the payload. It checks the listed revision, length, digest, and
    ownership operation. This is revision and ownership evidence, not independent readback.
    """

    assurance: Literal["uploaded_content"] = "uploaded_content"
    path: ReleasePath
    length: NonNegativeInt
    revision: NonEmptyStr
    sha256: Sha256Hex
    operation_sha256: Sha256Hex


InventoryObject = Annotated[
    InventoryEntry | StorageRevisionEntry | UploadedContentEntry,
    Field(discriminator="assurance"),
]


class Inventory(ReleaseModel):
    """Sorted, closed inventory of payload and evidence objects."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    entries: tuple[InventoryObject, ...]

    @model_validator(mode="after")
    def validate_entries(self) -> Self:
        paths = [entry.path for entry in self.entries]
        if len(set(paths)) != len(paths):
            raise ValueError("inventory paths must be unique")
        if paths != sorted(paths):
            raise ValueError("inventory paths must be sorted")
        if CONTROL_PATHS.intersection(paths):
            raise ValueError("inventory must not list control artifacts")
        return self


class ProvenanceMapping(ReleaseModel):
    """Derivation from a frozen source object to an inventoried output object."""

    source: ReleasePath
    target: ReleasePath


class FrameMapping(ReleaseModel):
    """Output frame derived from one source frame."""

    output_frame_index: NonNegativeInt
    source_frame_index: NonNegativeInt


class EpisodeMapping(ReleaseModel):
    """Output episode derived from one selected original episode."""

    output_episode_index: NonNegativeInt
    source_episode_id: NonEmptyStr
    frames: tuple[FrameMapping, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_frame_order(self) -> Self:
        if [frame.output_frame_index for frame in self.frames] != list(range(len(self.frames))):
            raise ValueError("output frame indices must be contiguous from zero")
        sources = [frame.source_frame_index for frame in self.frames]
        if any(later <= earlier for earlier, later in pairwise(sources)):
            raise ValueError("source frame indices must increase in selection order")
        return self


class VersionIdentity(ReleaseModel):
    """Explicit producer component or dataset format name and version."""

    name: NonEmptyStr
    version: NonEmptyStr


class ProducerIdentity(ReleaseModel):
    """Reproducible producer identity and canonical generation settings."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    feature: Literal["curated-dataset-release"] = "curated-dataset-release"
    application: VersionIdentity
    adapter: VersionIdentity
    source_format: VersionIdentity
    target_format: VersionIdentity
    tools: tuple[VersionIdentity, ...]
    settings_json: NonEmptyStr

    @model_validator(mode="after")
    def validate_settings(self) -> Self:
        raw = self.settings_json.encode("utf-8")
        if canonical_json_bytes(parse_strict_json(raw)) != raw:
            raise ValueError("settings_json must be canonical JSON")
        return self


class FrameLineageRecord(ReleaseModel):
    """One canonical JSONL row, ordered by output episode then output frame."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    kind: Literal["frame"] = "frame"
    output_episode_index: NonNegativeInt
    source_episode_id: NonEmptyStr
    output_frame_index: NonNegativeInt
    source_frame_index: NonNegativeInt


class LineageStream(ReleaseModel):
    """Digest/count/order binding for inventoried, newline-terminated frame lineage.

    Each row is canonical FrameLineageRecord JSON, at most 64 KiB excluding
    its newline. Empty rows and an unterminated final row are invalid.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION
    record_schema_version: SchemaVersion = SCHEMA_VERSION
    order: Literal["output_episode_then_frame"] = "output_episode_then_frame"
    path: ReleasePath
    sha256: Sha256Hex
    record_count: NonNegativeInt = Field(ge=1)
    episode_count: NonNegativeInt = Field(ge=1)


class ReleaseManifest(ReleaseModel):
    """Binds the inventory, frozen input, preview, and provenance mappings."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    package_id: NonEmptyStr
    producer: ProducerIdentity
    frozen_input_path: ReleasePath
    frozen_input_sha256: Sha256Hex
    preview_path: ReleasePath
    preview_sha256: Sha256Hex
    inventory_sha256: Sha256Hex
    mappings: tuple[ProvenanceMapping, ...] = ()
    episode_mappings: tuple[EpisodeMapping, ...] = ()
    lineage_stream: LineageStream | None = None

    @model_validator(mode="after")
    def validate_mappings(self) -> Self:
        if self.frozen_input_path == self.preview_path:
            raise ValueError("frozen input and preview paths must differ")
        if self.lineage_stream is not None and self.episode_mappings:
            raise ValueError("use either inline episode mappings or a lineage stream, not both")
        targets = [mapping.target for mapping in self.mappings]
        if len(set(targets)) != len(targets):
            raise ValueError("provenance mapping targets must be unique")
        if [episode.output_episode_index for episode in self.episode_mappings] != list(
            range(len(self.episode_mappings))
        ):
            raise ValueError("output episode indices must be contiguous from zero")
        sources = [episode.source_episode_id for episode in self.episode_mappings]
        if len(set(sources)) != len(sources):
            raise ValueError("source episode ids must be unique")
        return self


class VerificationReceipt(ReleaseModel):
    """Candidate byte and structure attestation; not publication."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    package_id: NonEmptyStr
    verification_mode: Literal["candidate"] = "candidate"
    verifier_version: NonEmptyStr = VERIFIER_VERSION
    assurance: Literal["candidate_internal_consistency"] = CANDIDATE_ASSURANCE
    manifest_sha256: Sha256Hex
    inventory_sha256: Sha256Hex
    object_count: NonNegativeInt
    content_digest_count: NonNegativeInt
    uploaded_content_count: NonNegativeInt
    storage_revision_count: NonNegativeInt
    checksum_covered_count: NonNegativeInt
    checksum_omitted_count: NonNegativeInt
    payload_byte_count: NonNegativeInt
    episode_mapping_count: NonNegativeInt
    frame_mapping_count: NonNegativeInt
    checks: tuple[NonEmptyStr, ...]


class ReleaseMarker(ReleaseModel):
    """Final marker written only after candidate verification succeeds."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    package_id: NonEmptyStr
    release_name: NonEmptyStr
    source_dataset_id: NonEmptyStr
    manifest_sha256: Sha256Hex
    inventory_sha256: Sha256Hex
    receipt_sha256: Sha256Hex
    checksums_sha256: Sha256Hex
    checksum_covered_count: NonNegativeInt
    checksum_omitted_count: NonNegativeInt
    status: Literal["final"] = "final"


class PackageIdentity(ReleaseModel):
    """Immutable package identity; published identity also binds release name and source dataset."""

    package_id: NonEmptyStr
    manifest_sha256: Sha256Hex
    release_name: NonEmptyStr | None = None
    source_dataset_id: NonEmptyStr | None = None


class JobStatus(ReleaseModel):
    """Mutable job and cleanup status, kept outside package identity."""

    state: Literal["pending", "running", "succeeded", "failed"]
    cleanup: Literal["pending", "complete", "failed"]


class IntegrationStatus(ReleaseModel):
    """Optional integration capability; absence never implies registration."""

    name: NonEmptyStr
    state: Literal["not_requested", "registered", "unavailable", "failed"]


class ReleaseResult(ReleaseModel):
    """Verification outcome separating identity, publication, and integrations."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    identity: PackageIdentity
    publication_state: Literal["candidate", "published"]
    checksum_covered_count: NonNegativeInt | None = None
    checksum_omitted_count: NonNegativeInt | None = None
    job: JobStatus | None = None
    integrations: list[IntegrationStatus] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_registration_requires_publication(self) -> Self:
        registered = any(item.state == "registered" for item in self.integrations)
        if registered and self.publication_state != "published":
            raise ValueError("integration registration requires published core publication")
        if self.publication_state == "published" and (
            self.identity.release_name is None or self.identity.source_dataset_id is None
        ):
            raise ValueError("published identity requires release name and source dataset")
        return self

    @property
    def discoverable(self) -> bool:
        return self.publication_state == "published"
