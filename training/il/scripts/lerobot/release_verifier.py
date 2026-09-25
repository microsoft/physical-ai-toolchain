"""Verify immutable Viewer dataset releases without runtime framework dependencies."""

# cspell:ignore nonincluded

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

_MANIFEST_PATH = "metadata/release-manifest.json"
_PACKAGE_QUALITY_PATH = "metadata/package-quality.json"
_ACCEPTED_PATH = "metadata/accepted.json"
_REJECTED_PATH = "metadata/rejected.json"
_EXCLUDED_PATH = "metadata/excluded.json"
_CHECKSUMS_PATH = "checksums.sha256"
_MARKER_PATH = ".published.json"
_DERIVED_LINEAGE_PATH = "metadata/derived-input.json"
_MANIFEST_SCHEMA = "2.0.0"
_EVIDENCE_SCHEMA = "1.0.0"


class ReleaseVerificationError(ValueError):
    """Raised when a release package cannot establish trusted identity."""


@dataclass(frozen=True)
class EpisodeSourceMapping:
    """Trace one release episode to its accepted source and review evidence."""

    release_episode_index: int
    source_dataset_id: str
    source_episode_index: int
    decision_id: str
    quality_run_id: str


@dataclass(frozen=True)
class VerifiedReleaseSummary:
    """Bounded identity and quality summary for one verified release."""

    release_id: str
    manifest_evidence_digest: str
    target_format_name: str
    target_format_version: str
    source_dataset_ids: tuple[str, ...]
    episode_count: int
    frame_count: int
    quality_profile_versions: tuple[str, ...]
    accepted_decision_ids: tuple[str, ...]
    parent_release_ids: tuple[str, ...]
    quality_artifact_paths: tuple[str, ...] = ()
    episode_source_mapping: tuple[EpisodeSourceMapping, ...] = ()


@dataclass(frozen=True)
class DerivedInputSummary:
    """Identity summary for transformed bytes derived from verified releases."""

    derived_input_digest: str
    parent_releases: tuple[VerifiedReleaseSummary, ...]


def build_dataset_lineage_record(
    summary: VerifiedReleaseSummary | DerivedInputSummary | None,
    *,
    unverified_source: str,
) -> dict[str, Any]:
    """Build the canonical JSON-native lineage record for downstream artifacts."""
    if isinstance(summary, VerifiedReleaseSummary):
        release = json.loads(json.dumps(asdict(summary)))
        return {"schema_version": _EVIDENCE_SCHEMA, "trust": "verified", "release": release}
    if isinstance(summary, DerivedInputSummary):
        parents = json.loads(json.dumps([asdict(parent) for parent in summary.parent_releases]))
        return {
            "schema_version": _EVIDENCE_SCHEMA,
            "trust": "derived",
            "derived_input_digest": summary.derived_input_digest,
            "parent_releases": parents,
        }
    return {"schema_version": _EVIDENCE_SCHEMA, "trust": "unverified", "source": unverified_source}


def compute_derived_input_digest(
    dataset_root: Path,
    parents: tuple[VerifiedReleaseSummary, ...] | list[VerifiedReleaseSummary],
) -> str:
    """Bind ordered parent summaries to all derived workspace bytes."""
    root = dataset_root.resolve()
    parent_records = [asdict(parent) for parent in parents]
    digest = hashlib.sha256()
    digest.update(json.dumps(parent_records, sort_keys=True, separators=(",", ":")).encode())
    for path in sorted(root.rglob("*")):
        if path == root / _DERIVED_LINEAGE_PATH:
            continue
        if path.is_symlink():
            raise ReleaseVerificationError(f"Derived dataset contains a symlink: {path.relative_to(root).as_posix()}")
        if not path.is_file():
            continue
        relative_path = path.relative_to(root).as_posix()
        digest.update(f"\n{relative_path}\0{path.stat().st_size}\0{_sha256(path)}".encode())
    return digest.hexdigest()


def verify_derived_input(dataset_root: Path) -> DerivedInputSummary:
    """Verify a derived dataset digest and its ordered parent release summaries."""
    root = dataset_root.resolve()
    lineage = _read_json(root, _DERIVED_LINEAGE_PATH, missing_label="derived input lineage")
    _require_schema(lineage, _EVIDENCE_SCHEMA, "derived input lineage")
    if lineage.get("derivation") != "merge":
        raise ReleaseVerificationError("Derived input lineage must describe a merge derivation")
    digest = _string(lineage, "derived_input_digest", "derived input lineage")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ReleaseVerificationError("Derived input digest must be lowercase SHA-256")
    raw_parents = lineage.get("parent_releases")
    if not isinstance(raw_parents, list) or len(raw_parents) < 2:
        raise ReleaseVerificationError("Derived input lineage requires at least two parent releases")
    parents = tuple(_parse_release_summary(parent) for parent in raw_parents)
    if compute_derived_input_digest(root, parents) != digest:
        raise ReleaseVerificationError("Derived input digest does not match workspace bytes or parent releases")
    return DerivedInputSummary(derived_input_digest=digest, parent_releases=parents)


def verify_release(
    release_root: Path,
    *,
    expected_target_format: tuple[str, str] | None = None,
) -> VerifiedReleaseSummary:
    """Verify a published Viewer release and return its bounded identity summary."""
    root = release_root.resolve()
    if not root.is_dir():
        raise ReleaseVerificationError(f"Release root is not a directory: {release_root}")
    _reject_symlinks(root)
    manifest = _read_json(root, _MANIFEST_PATH)
    _require_schema(manifest, _MANIFEST_SCHEMA, "release manifest")
    release_id = _string(manifest, "release_id", "release manifest")
    target_format = _mapping(manifest, "target_format", "release manifest")
    target_name = _string(target_format, "name", "target format")
    target_version = _string(target_format, "version", "target format")
    if expected_target_format is not None and (target_name, target_version) != expected_target_format:
        raise ReleaseVerificationError("Release target format does not match the requested runtime format")

    marker = _read_json(root, _MARKER_PATH, missing_label="publication marker")
    _require_schema(marker, _EVIDENCE_SCHEMA, "publication marker")
    if marker.get("release_id") != release_id:
        raise ReleaseVerificationError("Publication marker release identity does not match the manifest")

    files = _validate_inventory(root, manifest)
    accepted = _read_json(root, _ACCEPTED_PATH)
    rejected = _read_json(root, _REJECTED_PATH)
    excluded = _read_json(root, _EXCLUDED_PATH)
    _require_schema(accepted, _EVIDENCE_SCHEMA, "accepted ledger")
    _require_schema(rejected, _EVIDENCE_SCHEMA, "rejected ledger")
    _require_schema(excluded, _EVIDENCE_SCHEMA, "excluded ledger")
    source_dataset_ids, episode_source_mapping = _validate_dispositions(manifest, accepted, rejected, excluded)
    marker_dataset_id = _string(marker, "dataset_id", "publication marker")
    if source_dataset_ids != (marker_dataset_id,):
        raise ReleaseVerificationError("Publication marker dataset identity does not match release evidence")

    profile_versions, quality_paths = _validate_quality(root, manifest)
    package_quality = _read_json(root, _PACKAGE_QUALITY_PATH)
    _require_schema(package_quality, _EVIDENCE_SCHEMA, "package quality report")
    _validate_package_quality(manifest, package_quality)
    if _PACKAGE_QUALITY_PATH not in files:
        raise ReleaseVerificationError("Package quality report is missing from the manifest inventory")

    parent_releases = manifest.get("parent_releases", [])
    if not isinstance(parent_releases, list):
        raise ReleaseVerificationError("release manifest parent_releases must be a list")
    parent_ids = tuple(sorted(_string(parent, "release_id", "parent release") for parent in parent_releases))
    return VerifiedReleaseSummary(
        release_id=release_id,
        manifest_evidence_digest=_sha256(root / _MANIFEST_PATH),
        target_format_name=target_name,
        target_format_version=target_version,
        source_dataset_ids=source_dataset_ids,
        episode_count=_integer(manifest, "episode_count", "release manifest"),
        frame_count=_integer(manifest, "frame_count", "release manifest"),
        quality_profile_versions=profile_versions,
        accepted_decision_ids=tuple(sorted(_string_list(manifest, "accepted_decision_ids"))),
        parent_release_ids=parent_ids,
        quality_artifact_paths=tuple(sorted((*quality_paths, _PACKAGE_QUALITY_PATH))),
        episode_source_mapping=episode_source_mapping,
    )


def _parse_release_summary(value: Any) -> VerifiedReleaseSummary:
    """Parse one bounded release summary from derived lineage."""
    if not isinstance(value, dict):
        raise ReleaseVerificationError("Derived parent release summary must be an object")
    raw_episode_mapping = value.get("episode_source_mapping", [])
    if not isinstance(raw_episode_mapping, list):
        raise ReleaseVerificationError("Derived parent episode source mapping must be a list")
    episode_mapping = tuple(
        EpisodeSourceMapping(
            release_episode_index=_integer(item, "release_episode_index", "derived episode mapping"),
            source_dataset_id=_string(item, "source_dataset_id", "derived episode mapping"),
            source_episode_index=_integer(item, "source_episode_index", "derived episode mapping"),
            decision_id=_string(item, "decision_id", "derived episode mapping"),
            quality_run_id=_string(item, "quality_run_id", "derived episode mapping"),
        )
        for item in raw_episode_mapping
        if isinstance(item, dict)
    )
    if len(episode_mapping) != len(raw_episode_mapping):
        raise ReleaseVerificationError("Derived parent episode source mapping contains a non-object")
    return VerifiedReleaseSummary(
        release_id=_string(value, "release_id", "derived parent release"),
        manifest_evidence_digest=_string(value, "manifest_evidence_digest", "derived parent release"),
        target_format_name=_string(value, "target_format_name", "derived parent release"),
        target_format_version=_string(value, "target_format_version", "derived parent release"),
        source_dataset_ids=tuple(_string_list(value, "source_dataset_ids")),
        episode_count=_integer(value, "episode_count", "derived parent release"),
        frame_count=_integer(value, "frame_count", "derived parent release"),
        quality_profile_versions=tuple(_string_list(value, "quality_profile_versions")),
        accepted_decision_ids=tuple(_string_list(value, "accepted_decision_ids")),
        parent_release_ids=tuple(_string_list(value, "parent_release_ids")),
        quality_artifact_paths=tuple(_string_list(value, "quality_artifact_paths")),
        episode_source_mapping=episode_mapping,
    )


def _validate_inventory(root: Path, manifest: dict[str, Any]) -> set[str]:
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ReleaseVerificationError("release manifest files must be a list")
    files: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReleaseVerificationError("release manifest contains an invalid file entry")
        relative_path = _normalized_path(_string(entry, "path", "release file"))
        if relative_path in files:
            raise ReleaseVerificationError(f"Duplicate release file path: {relative_path}")
        files[relative_path] = entry
    checksums = _read_checksums(root / _CHECKSUMS_PATH)
    covered = {_MANIFEST_PATH, *files}
    if set(checksums) != covered:
        raise ReleaseVerificationError("Checksum inventory does not match the release manifest")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() not in {_CHECKSUMS_PATH, _MARKER_PATH}
    }
    if actual != covered:
        raise ReleaseVerificationError("Exact release inventory does not match the manifest")
    for relative_path, digest in checksums.items():
        path = root / relative_path
        if _sha256(path) != digest:
            raise ReleaseVerificationError(f"SHA-256 mismatch for {relative_path}")
        if relative_path in files:
            expected_size = _integer(files[relative_path], "size_bytes", "release file")
            if path.stat().st_size != expected_size:
                raise ReleaseVerificationError(f"Size mismatch for {relative_path}")
            if files[relative_path].get("sha256") != digest:
                raise ReleaseVerificationError(f"Manifest SHA-256 mismatch for {relative_path}")
    required = {_ACCEPTED_PATH, _REJECTED_PATH, _EXCLUDED_PATH, _PACKAGE_QUALITY_PATH}
    if not required.issubset(files):
        raise ReleaseVerificationError("Release manifest is missing mandatory evidence files")
    return set(files)


def _validate_dispositions(
    manifest: dict[str, Any],
    accepted: dict[str, Any],
    rejected: dict[str, Any],
    excluded: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[EpisodeSourceMapping, ...]]:
    if accepted.get("disposition") != "accept" or rejected.get("disposition") != "reject":
        raise ReleaseVerificationError("Decision ledger disposition is invalid")
    accepted_decisions = _mapping_list(accepted, "decisions", "accepted ledger")
    rejected_decisions = _mapping_list(rejected, "decisions", "rejected ledger")
    excluded_candidates = _mapping_list(excluded, "candidates", "excluded ledger")
    _validate_sorted_unique(accepted_decisions, "decision_id", "accepted ledger")
    _validate_sorted_unique(rejected_decisions, "decision_id", "rejected ledger")
    _validate_sorted_unique(excluded_candidates, "episode_index", "excluded ledger")
    if any(decision.get("decision") != "accept" for decision in accepted_decisions):
        raise ReleaseVerificationError("Accepted ledger contains a non-accepted decision")
    if any(decision.get("decision") != "reject" for decision in rejected_decisions):
        raise ReleaseVerificationError("Rejected ledger contains a non-rejected decision")
    if any(candidate.get("disposition") != "excluded" for candidate in excluded_candidates):
        raise ReleaseVerificationError("Excluded ledger contains another disposition")

    accepted_ids = {_string(decision, "decision_id", "accepted decision") for decision in accepted_decisions}
    rejected_ids = {_string(decision, "decision_id", "rejected decision") for decision in rejected_decisions}
    if accepted_ids != set(_string_list(manifest, "accepted_decision_ids")):
        raise ReleaseVerificationError("Accepted ledger does not match the manifest")
    if rejected_ids != set(_string_list(manifest, "rejected_decision_ids")):
        raise ReleaseVerificationError("Rejected ledger does not match the manifest")
    excluded_indices = {_integer(candidate, "episode_index", "excluded candidate") for candidate in excluded_candidates}
    if excluded_indices != set(_integer_list(manifest, "excluded_episode_indices")):
        raise ReleaseVerificationError("Excluded ledger does not match the manifest")

    dataset_id = _string(excluded, "dataset_id", "excluded ledger")
    accepted_keys = {_decision_key(decision) for decision in accepted_decisions}
    rejected_keys = {_decision_key(decision) for decision in rejected_decisions}
    excluded_keys = {
        (
            _string(_mapping(candidate, "source", "excluded candidate"), "dataset_id", "excluded source")
            if candidate.get("source") is not None
            else dataset_id,
            _integer(candidate, "episode_index", "excluded candidate"),
        )
        for candidate in excluded_candidates
    }
    all_keys = accepted_keys | rejected_keys | excluded_keys
    if len(all_keys) != len(accepted_keys) + len(rejected_keys) + len(excluded_keys):
        raise ReleaseVerificationError("Release disposition ledgers overlap")
    counts = {
        "accepted_count": len(accepted_keys),
        "rejected_count": len(rejected_keys),
        "excluded_count": len(excluded_keys),
    }
    for field, actual_count in counts.items():
        if _integer(manifest, field, "release manifest") != actual_count:
            raise ReleaseVerificationError(f"Release manifest {field} does not match its ledger")
    nonincluded = counts["rejected_count"] + counts["excluded_count"]
    if _integer(manifest, "nonincluded_count", "release manifest") != nonincluded:
        raise ReleaseVerificationError("Release manifest nonincluded_count is invalid")
    if _integer(manifest, "candidate_count", "release manifest") != len(all_keys):
        raise ReleaseVerificationError("Release disposition ledgers do not cover every candidate")
    if _integer(manifest, "episode_count", "release manifest") != counts["accepted_count"]:
        raise ReleaseVerificationError("Release manifest episode_count does not match accepted_count")

    mapping = _index_mapping(accepted, "episode_index_mapping", "accepted ledger")
    manifest_mapping = _index_mapping(manifest, "episode_index_mapping", "release manifest")
    if mapping != manifest_mapping or set(mapping.values()) != set(range(len(accepted_keys))):
        raise ReleaseVerificationError("Accepted episode mapping is not contiguous or does not match the manifest")
    if set(mapping) != {episode_index for _dataset_id, episode_index in accepted_keys}:
        raise ReleaseVerificationError("Accepted episode mapping does not cover its ledger")
    dataset_ids = tuple(sorted({key[0] for key in all_keys}))
    if dataset_ids != (dataset_id,):
        raise ReleaseVerificationError("Release dispositions must reference one source dataset")
    accepted_by_episode = {_decision_key(decision): decision for decision in accepted_decisions}
    episode_source_mapping = tuple(
        EpisodeSourceMapping(
            release_episode_index=release_index,
            source_dataset_id=dataset_id,
            source_episode_index=source_index,
            decision_id=_string(accepted_by_episode[(dataset_id, source_index)], "decision_id", "accepted decision"),
            quality_run_id=_string(
                accepted_by_episode[(dataset_id, source_index)], "quality_run_id", "accepted decision"
            ),
        )
        for source_index, release_index in sorted(mapping.items(), key=lambda item: item[1])
    )
    return dataset_ids, episode_source_mapping


def _validate_quality(root: Path, manifest: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    references = _mapping_list(manifest, "quality_evidence", "release manifest")
    if len(references) != _integer(manifest, "episode_count", "release manifest"):
        raise ReleaseVerificationError("Quality evidence must contain one reference per accepted episode")
    accepted = _read_json(root, _ACCEPTED_PATH)
    decisions = {
        _string(decision, "decision_id", "accepted decision"): decision
        for decision in _mapping_list(accepted, "decisions", "accepted ledger")
    }
    run_ids: set[str] = set()
    profile_versions: set[str] = set()
    artifact_paths: set[str] = set()
    for reference in references:
        decision_id = _string(reference, "decision_id", "quality reference")
        run_id = _string(reference, "quality_run_id", "quality reference")
        path = _normalized_path(_string(reference, "quality_report_path", "quality reference"))
        if path != f"metadata/quality/{run_id}.json" or run_id in run_ids:
            raise ReleaseVerificationError("Quality evidence path or run identity is invalid")
        run_ids.add(run_id)
        artifact_paths.add(path)
        decision = decisions.get(decision_id)
        if decision is None or decision.get("quality_run_id") != run_id:
            raise ReleaseVerificationError("Quality evidence does not match the accepted decision")
        report = _read_json(root, path)
        _require_schema(report, _EVIDENCE_SCHEMA, "quality report")
        if report.get("run_id") != run_id or report.get("source") != decision.get("source"):
            raise ReleaseVerificationError("Quality report identity does not match the accepted decision")
        profile_version = _string(report, "check_set_version", "quality report")
        if reference.get("check_set_version") != profile_version or reference.get("required_outcome") != "pass":
            raise ReleaseVerificationError("Quality evidence profile or required outcome is invalid")
        profile_versions.add(profile_version)
        checks = _mapping_list(report, "episode_checks", "quality report")
        if any(check.get("required") is True and check.get("outcome") != "pass" for check in checks):
            raise ReleaseVerificationError("Release contains a failed required quality check")
    return tuple(sorted(profile_versions)), tuple(sorted(artifact_paths))


def _validate_package_quality(manifest: dict[str, Any], report: dict[str, Any]) -> None:
    if report.get("target_format") != manifest.get("target_format"):
        raise ReleaseVerificationError("Package quality target format does not match the manifest")
    episode_count = _integer(manifest, "episode_count", "release manifest")
    frame_count = _integer(manifest, "frame_count", "release manifest")
    if report.get("episode_count") != episode_count or report.get("frame_count") != frame_count:
        raise ReleaseVerificationError("Package quality counts do not match the manifest")
    if report.get("nonvisual_rows_read_back") != frame_count:
        raise ReleaseVerificationError("Package quality did not read back every nonvisual row")
    if report.get("inventory_verified") is not True or report.get("checksums_verified") is not True:
        raise ReleaseVerificationError("Package verification flags did not pass")
    frame_counts = _index_mapping(report, "episode_frame_counts", "package quality report")
    mapping = _index_mapping(manifest, "episode_index_mapping", "release manifest")
    if set(frame_counts) != set(mapping.values()) or sum(frame_counts.values()) != frame_count:
        raise ReleaseVerificationError("Package episode frame counts do not match the manifest")
    features = report.get("features")
    feature_schema = _mapping(manifest, "feature_schema", "release manifest")
    if not isinstance(features, list) or set(features) != set(feature_schema):
        raise ReleaseVerificationError("Package quality features do not match the manifest")
    visual_features = {
        name
        for name, specification in feature_schema.items()
        if isinstance(specification, dict) and specification.get("dtype") in {"image", "video"}
    }
    expected_samples = {
        (episode_index, feature_name, frame_index)
        for episode_index, count in frame_counts.items()
        for feature_name in visual_features
        for frame_index in {0, count // 2, count - 1}
    }
    samples = _mapping_list(report, "visual_samples", "package quality report")
    actual_samples = {
        (
            _integer(sample, "release_episode_index", "visual sample"),
            _string(sample, "feature_name", "visual sample"),
            _integer(sample, "frame_index", "visual sample"),
        )
        for sample in samples
        if sample.get("outcome") == "pass"
    }
    if actual_samples != expected_samples or len(actual_samples) != len(samples):
        raise ReleaseVerificationError("Package visual sample coverage is incomplete")


def _read_json(root: Path, relative_path: str, *, missing_label: str | None = None) -> dict[str, Any]:
    relative_path = _normalized_path(relative_path)
    path = root / relative_path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReleaseVerificationError(f"Missing {missing_label or relative_path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError(f"Invalid JSON evidence at {relative_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseVerificationError(f"JSON evidence must be an object: {relative_path}")
    return value


def _read_checksums(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReleaseVerificationError(f"Missing or unreadable {_CHECKSUMS_PATH}") from exc
    checksums: dict[str, str] = {}
    for line in lines:
        digest, separator, relative_path = line.partition("  ")
        relative_path = _normalized_path(relative_path)
        if separator != "  " or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ReleaseVerificationError("Invalid checksums.sha256 entry")
        if relative_path in checksums:
            raise ReleaseVerificationError(f"Duplicate checksum path: {relative_path}")
        checksums[relative_path] = digest
    return checksums


def _reject_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ReleaseVerificationError(f"Release package contains a symlink: {path.relative_to(root)}")


def _require_schema(value: dict[str, Any], expected: str, label: str) -> None:
    if value.get("schema_version") != expected:
        raise ReleaseVerificationError(f"Unsupported {label} schema version")


def _normalized_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "\\" in value or ".." in path.parts or path.as_posix() != value:
        raise ReleaseVerificationError(f"Release path is not normalized relative POSIX: {value}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseVerificationError(f"Unable to read release file: {path.name}") from exc
    return digest.hexdigest()


def _mapping(value: dict[str, Any], field: str, label: str) -> dict[str, Any]:
    result = value.get(field)
    if not isinstance(result, dict):
        raise ReleaseVerificationError(f"{label} {field} must be an object")
    return result


def _mapping_list(value: dict[str, Any], field: str, label: str) -> list[dict[str, Any]]:
    result = value.get(field)
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise ReleaseVerificationError(f"{label} {field} must be an array of objects")
    return result


def _string(value: dict[str, Any], field: str, label: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ReleaseVerificationError(f"{label} {field} must be a non-empty string")
    return result


def _integer(value: dict[str, Any], field: str, label: str) -> int:
    result = value.get(field)
    if not isinstance(result, int) or isinstance(result, bool) or result < 0:
        raise ReleaseVerificationError(f"{label} {field} must be a non-negative integer")
    return result


def _string_list(value: dict[str, Any], field: str) -> list[str]:
    result = value.get(field)
    if not isinstance(result, list) or any(not isinstance(item, str) or not item for item in result):
        raise ReleaseVerificationError(f"release manifest {field} must be an array of strings")
    return result


def _integer_list(value: dict[str, Any], field: str) -> list[int]:
    result = value.get(field)
    if not isinstance(result, list) or any(not isinstance(item, int) or isinstance(item, bool) for item in result):
        raise ReleaseVerificationError(f"release manifest {field} must be an array of integers")
    return result


def _index_mapping(value: dict[str, Any], field: str, label: str) -> dict[int, int]:
    raw = _mapping(value, field, label)
    try:
        result = {int(key): item for key, item in raw.items()}
    except (TypeError, ValueError) as exc:
        raise ReleaseVerificationError(f"{label} {field} contains an invalid episode index") from exc
    if any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in result.values()):
        raise ReleaseVerificationError(f"{label} {field} contains an invalid release index")
    return result


def _decision_key(decision: dict[str, Any]) -> tuple[str, int]:
    source = _mapping(decision, "source", "review decision")
    return (
        _string(source, "dataset_id", "decision source"),
        _integer(source, "episode_index", "decision source"),
    )


def _validate_sorted_unique(values: list[dict[str, Any]], field: str, label: str) -> None:
    identities = [value.get(field) for value in values]
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise ReleaseVerificationError(f"{label} entries must be unique and sorted by {field}")
