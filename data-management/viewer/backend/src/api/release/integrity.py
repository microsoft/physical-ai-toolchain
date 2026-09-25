"""Canonical release ledgers, manifests, and application-owned checksums."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from ..models.release_workflow import EligibilityCandidate
from ..models.releases import PackageQualityReport, ReleaseFile, ReleaseManifest, canonical_json_bytes
from ..models.reviews import (
    ContractId,
    ImmutableContract,
    QualityOutcome,
    QualityReport,
    ReviewDecision,
    ReviewDecisionValue,
)

_ACCEPTED_PATH = "metadata/accepted.json"
_REJECTED_PATH = "metadata/rejected.json"
_EXCLUDED_PATH = "metadata/excluded.json"
_QUALITY_DIRECTORY = "metadata/quality"
_PACKAGE_QUALITY_PATH = "metadata/package-quality.json"
_MANIFEST_PATH = "metadata/release-manifest.json"
_CHECKSUMS_PATH = "checksums.sha256"
_PUBLICATION_MARKER = ".published.json"


class DecisionLedger(ImmutableContract):
    """Canonical decisions for one release disposition."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    disposition: Literal["accept", "reject"]
    decisions: tuple[ReviewDecision, ...]
    episode_index_mapping: dict[int, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_disposition(self) -> DecisionLedger:
        if any(decision.decision.value != self.disposition for decision in self.decisions):
            raise ValueError("ledger decisions must match disposition")
        decision_ids = [decision.decision_id for decision in self.decisions]
        if decision_ids != sorted(decision_ids) or len(decision_ids) != len(set(decision_ids)):
            raise ValueError("ledger decisions must be unique and sorted by decision_id")
        source_indices = {decision.source.episode_index for decision in self.decisions}
        if self.disposition == "accept":
            if set(self.episode_index_mapping) != source_indices:
                raise ValueError("accepted ledger mapping must cover accepted source episodes")
            if set(self.episode_index_mapping.values()) != set(range(len(self.decisions))):
                raise ValueError("accepted ledger release indices must be contiguous")
        elif self.episode_index_mapping:
            raise ValueError("rejected ledger cannot contain a release episode mapping")
        return self


class ExcludedLedger(ImmutableContract):
    """Canonical non-reject exclusions for one release assessment."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    dataset_id: ContractId
    candidates: tuple[EligibilityCandidate, ...]

    @model_validator(mode="after")
    def validate_candidates(self) -> ExcludedLedger:
        if any(candidate.disposition != "excluded" for candidate in self.candidates):
            raise ValueError("excluded ledger candidates must have excluded disposition")
        indices = [candidate.episode_index for candidate in self.candidates]
        if indices != sorted(indices) or len(indices) != len(set(indices)):
            raise ValueError("excluded ledger candidates must be unique and sorted by episode_index")
        if any(
            candidate.source is not None and candidate.source.dataset_id != self.dataset_id
            for candidate in self.candidates
        ):
            raise ValueError("excluded ledger source must match dataset_id")
        return self


def finalize_release(
    package_root: Path,
    manifest: ReleaseManifest,
    accepted: tuple[ReviewDecision, ...],
    rejected: tuple[ReviewDecision, ...],
    quality_reports: tuple[QualityReport, ...],
    package_quality: PackageQualityReport,
    excluded: tuple[EligibilityCandidate, ...] = (),
) -> ReleaseManifest:
    """Write canonical ledgers, complete the manifest inventory, and write checksums."""
    if not package_root.is_dir():
        raise FileNotFoundError(package_root)
    _reject_symbolic_links(package_root)
    accepted = tuple(sorted(accepted, key=lambda decision: decision.decision_id))
    rejected = tuple(sorted(rejected, key=lambda decision: decision.decision_id))
    accepted_ids = tuple(decision.decision_id for decision in accepted)
    accepted_ids_match = len(accepted_ids) == len(manifest.accepted_decision_ids) and set(accepted_ids) == set(
        manifest.accepted_decision_ids
    )
    if not accepted_ids_match:
        raise ValueError("accepted ledger must match manifest accepted_decision_ids")
    if any(decision.decision is not ReviewDecisionValue.ACCEPT for decision in accepted):
        raise ValueError("accepted ledger contains a non-accepted decision")
    if any(decision.decision is not ReviewDecisionValue.REJECT for decision in rejected):
        raise ValueError("rejected ledger contains a non-rejected decision")
    excluded = tuple(sorted(excluded, key=lambda candidate: candidate.episode_index))
    _validate_disposition_evidence(manifest, accepted, rejected, excluded)
    _validate_quality_evidence(manifest, accepted, quality_reports, package_quality)

    _write_canonical(
        package_root / _ACCEPTED_PATH,
        DecisionLedger(
            disposition="accept",
            decisions=accepted,
            episode_index_mapping=manifest.episode_index_mapping,
        ),
    )
    _write_canonical(
        package_root / _REJECTED_PATH,
        DecisionLedger(disposition="reject", decisions=rejected),
    )
    _write_canonical(
        package_root / _EXCLUDED_PATH,
        ExcludedLedger(dataset_id=accepted[0].source.dataset_id, candidates=excluded),
    )
    for report in sorted(quality_reports, key=lambda value: value.run_id):
        _write_canonical(package_root / _QUALITY_DIRECTORY / f"{report.run_id}.json", report)
    _write_canonical(package_root / _PACKAGE_QUALITY_PATH, package_quality)
    excluded = {_MANIFEST_PATH, _CHECKSUMS_PATH, _PUBLICATION_MARKER}
    files = tuple(
        _release_file(path, package_root)
        for path in sorted(candidate for candidate in package_root.rglob("*") if candidate.is_file())
        if path.relative_to(package_root).as_posix() not in excluded
    )
    finalized = manifest.model_copy(update={"files": files})
    _write_canonical(package_root / _MANIFEST_PATH, finalized)

    covered_paths = (_MANIFEST_PATH, *(entry.path for entry in files))
    checksum_lines = [f"{_sha256_file(package_root / path)}  {path}\n" for path in covered_paths]
    _write_exclusive(package_root / _CHECKSUMS_PATH, "".join(checksum_lines).encode())
    return finalized


def verify_release(package_root: Path) -> ReleaseManifest:
    """Verify exact release inventory, sizes, and application-owned SHA-256 values."""
    _reject_symbolic_links(package_root)
    manifest_path = package_root / _MANIFEST_PATH
    checksums_path = package_root / _CHECKSUMS_PATH
    try:
        manifest = ReleaseManifest.model_validate_json(manifest_path.read_bytes())
        checksum_lines = checksums_path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid release metadata: {exc}") from exc

    checksums: dict[str, str] = {}
    for line in checksum_lines:
        digest, separator, relative_path = line.partition("  ")
        _validate_relative_path(relative_path)
        if separator != "  " or len(digest) != 64 or relative_path in checksums:
            raise ValueError("Invalid checksums.sha256 entry")
        checksums[relative_path] = digest

    expected = {_MANIFEST_PATH, *(entry.path for entry in manifest.files)}
    if set(checksums) != expected:
        raise ValueError("Checksum inventory does not match release manifest")
    actual_files = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and path.name not in {_CHECKSUMS_PATH, _PUBLICATION_MARKER}
    }
    if actual_files != expected:
        raise ValueError("Package inventory does not match release manifest")
    manifest_files = {entry.path: entry for entry in manifest.files}
    for relative_path, expected_digest in checksums.items():
        path = package_root / relative_path
        if relative_path in manifest_files and path.stat().st_size != manifest_files[relative_path].size_bytes:
            raise ValueError(f"Size mismatch for {relative_path}")
        if _sha256_file(path) != expected_digest:
            raise ValueError(f"SHA-256 mismatch for {relative_path}")
    try:
        accepted_ledger = DecisionLedger.model_validate_json((package_root / _ACCEPTED_PATH).read_bytes())
        rejected_ledger = DecisionLedger.model_validate_json((package_root / _REJECTED_PATH).read_bytes())
        excluded_ledger = ExcludedLedger.model_validate_json((package_root / _EXCLUDED_PATH).read_bytes())
        package_quality = PackageQualityReport.model_validate_json(
            (package_root / manifest.package_quality_path).read_bytes()
        )
        quality_reports = tuple(
            QualityReport.model_validate_json((package_root / reference.quality_report_path).read_bytes())
            for reference in manifest.quality_evidence
        )
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid release quality evidence: {exc}") from exc
    if accepted_ledger.disposition != "accept" or rejected_ledger.disposition != "reject":
        raise ValueError("Release decision ledger disposition is invalid")
    _validate_disposition_evidence(
        manifest,
        accepted_ledger.decisions,
        rejected_ledger.decisions,
        excluded_ledger.candidates,
    )
    if accepted_ledger.episode_index_mapping != manifest.episode_index_mapping:
        raise ValueError("Accepted ledger mapping does not match the manifest")
    _validate_quality_evidence(manifest, accepted_ledger.decisions, quality_reports, package_quality)
    return manifest


def _validate_disposition_evidence(
    manifest: ReleaseManifest,
    accepted: tuple[ReviewDecision, ...],
    rejected: tuple[ReviewDecision, ...],
    excluded: tuple[EligibilityCandidate, ...],
) -> None:
    accepted_ids = {decision.decision_id for decision in accepted}
    rejected_ids = {decision.decision_id for decision in rejected}
    if accepted_ids != set(manifest.accepted_decision_ids):
        raise ValueError("accepted ledger must match manifest accepted_decision_ids")
    if rejected_ids != set(manifest.rejected_decision_ids):
        raise ValueError("rejected ledger must match manifest rejected_decision_ids")
    excluded_indices = {candidate.episode_index for candidate in excluded}
    if excluded_indices != set(manifest.excluded_episode_indices):
        raise ValueError("excluded ledger must match manifest excluded_episode_indices")
    accepted_keys = {(decision.source.dataset_id, decision.source.episode_index) for decision in accepted}
    rejected_keys = {(decision.source.dataset_id, decision.source.episode_index) for decision in rejected}
    excluded_keys = {
        (
            candidate.source.dataset_id if candidate.source is not None else accepted[0].source.dataset_id,
            candidate.episode_index,
        )
        for candidate in excluded
    }
    dataset_ids = {dataset_id for dataset_id, _episode_index in accepted_keys | rejected_keys | excluded_keys}
    if dataset_ids != {accepted[0].source.dataset_id}:
        raise ValueError("release disposition ledgers must reference one dataset")
    all_keys = accepted_keys | rejected_keys | excluded_keys
    if len(all_keys) != len(accepted) + len(rejected) + len(excluded):
        raise ValueError("release disposition ledgers overlap")
    if len(all_keys) != manifest.candidate_count:
        raise ValueError("release disposition ledgers do not cover every candidate")


def _validate_quality_evidence(
    manifest: ReleaseManifest,
    accepted: tuple[ReviewDecision, ...],
    quality_reports: tuple[QualityReport, ...],
    package_quality: PackageQualityReport,
) -> None:
    decisions = {decision.decision_id: decision for decision in accepted}
    reports = {report.run_id: report for report in quality_reports}
    if len(reports) != len(quality_reports):
        raise ValueError("quality_run_id values must be unique")
    if manifest.package_quality_path != _PACKAGE_QUALITY_PATH:
        raise ValueError("package_quality_path must use the canonical location")
    for reference in manifest.quality_evidence:
        decision = decisions.get(reference.decision_id)
        report = reports.get(reference.quality_run_id)
        if decision is None or decision.quality_run_id != reference.quality_run_id:
            raise ValueError("quality_run_id does not match the accepted decision")
        if report is None or report.run_id != reference.quality_run_id:
            raise ValueError("quality_run_id does not match the quality report")
        if report.source != decision.source:
            raise ValueError("quality report source does not match the accepted decision")
        if report.check_set_version != reference.check_set_version:
            raise ValueError("quality check-set version mismatch")
        if reference.quality_report_path != f"{_QUALITY_DIRECTORY}/{report.run_id}.json":
            raise ValueError("quality report path does not use the canonical location")
        if any(check.required and check.outcome is not QualityOutcome.PASS for check in report.episode_checks):
            raise ValueError("included episode has a failed required quality check")
    if set(reports) != {reference.quality_run_id for reference in manifest.quality_evidence}:
        raise ValueError("quality reports must match manifest quality_evidence")
    if package_quality.target_format != manifest.target_format:
        raise ValueError("package quality target format mismatch")
    if package_quality.episode_count != manifest.episode_count or package_quality.frame_count != manifest.frame_count:
        raise ValueError("package quality counts do not match the manifest")
    expected_episode_indices = set(manifest.episode_index_mapping.values())
    if set(package_quality.episode_frame_counts) != expected_episode_indices:
        raise ValueError("package quality episode counts do not match the manifest mapping")
    if any(frame_count <= 0 for frame_count in package_quality.episode_frame_counts.values()):
        raise ValueError("package quality contains an empty episode")
    if sum(package_quality.episode_frame_counts.values()) != manifest.frame_count:
        raise ValueError("package quality episode frame counts do not match the manifest")
    if set(package_quality.features) != set(manifest.feature_schema):
        raise ValueError("package quality features do not match the manifest")
    if package_quality.nonvisual_rows_read_back != manifest.frame_count:
        raise ValueError("package quality did not read back every nonvisual row")
    visual_features = {
        name
        for name, specification in manifest.feature_schema.items()
        if isinstance(specification, dict) and specification.get("dtype") in {"image", "video"}
    }
    expected_samples = {
        (episode_index, feature_name, frame_index)
        for episode_index, frame_count in package_quality.episode_frame_counts.items()
        for feature_name in visual_features
        for frame_index in {0, frame_count // 2, frame_count - 1}
    }
    actual_samples = {
        (sample.release_episode_index, sample.feature_name, sample.frame_index)
        for sample in package_quality.visual_samples
        if sample.outcome is QualityOutcome.PASS
    }
    if actual_samples != expected_samples or len(actual_samples) != len(package_quality.visual_samples):
        raise ValueError("package quality visual sample coverage is incomplete")
    if not package_quality.inventory_verified or not package_quality.checksums_verified:
        raise ValueError("package quality verification did not pass")


def _release_file(path: Path, package_root: Path) -> ReleaseFile:
    relative_path = path.relative_to(package_root).as_posix()
    _validate_relative_path(relative_path)
    return ReleaseFile(path=relative_path, size_bytes=path.stat().st_size, sha256=_sha256_file(path))


def _reject_symbolic_links(package_root: Path) -> None:
    if package_root.is_symlink() or any(path.is_symlink() for path in package_root.rglob("*")):
        raise ValueError("Release packages cannot contain symbolic links")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_canonical(path: Path, contract: ImmutableContract) -> None:
    _write_exclusive(path, canonical_json_bytes(contract))


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "\\" in value or ".." in path.parts:
        raise ValueError("Release file path must be a normalized relative POSIX path")
