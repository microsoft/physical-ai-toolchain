"""Validate revision-bound accessibility evidence contracts.

Producer output is untrusted data. Validation confirms contract integrity but
does not make accessibility, regulatory, or legal conformance claims.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

EXIT_SUCCESS = 0
EXIT_FAILURE = 1

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*(?:-[A-Za-z0-9._:-]+)*$")
_DIGEST_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")
_GIT_COMMIT_PATTERN = re.compile(r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?:password|passwd|api[-_ ]?key|access[-_ ]?token|client[-_ ]?secret)\b\s*[:=]\s*\S+"
)
_ENVIRONMENT_PATTERN = re.compile(
    r"(?i)(?:/subscriptions/|\.azure(?:websites)?\.net\b|\.onmicrosoft\.com\b|https?://(?!localhost|127\.0\.0\.1))"
)

_DOCUSAURUS_AUTOMATED_PROBES = {
    "SOURCE_INSPECTION": "project-source",
    "UNIT_TEST": "project-source",
    "COMPONENT_TEST": "project-source",
    "AXE": "project-playwright",
    "PLAYWRIGHT_KEYBOARD": "project-playwright",
    "PLAYWRIGHT_POINTER": "project-playwright",
    "PLAYWRIGHT_TREE": "project-playwright",
    "PLAYWRIGHT_LIVE_REGION": "project-playwright",
    "PLAYWRIGHT_VISUAL": "project-playwright",
}
_DOCUSAURUS_HUMAN_METHODS = {
    "MANUAL_KEYBOARD",
    "NVDA",
    "JAWS",
    "COGNITIVE_REVIEW",
    "MEDIA_EQUIVALENCE_REVIEW",
}
_DOCUSAURUS_SOURCE_METHODS = {"SOURCE_INSPECTION", "UNIT_TEST", "COMPONENT_TEST"}
_DOCUSAURUS_GUARDED_REVIEW_REQUIREMENTS = {
    "WCAG22-2.5.7",
    "WCAG22-3.3.4",
    "WCAG22-3.3.7",
}
_DOCUSAURUS_VALIDATION_COMMANDS = {
    "hve-self-tests": "Pinned HVE Python and Node self-tests",
    "screen-reader-binding": "Docusaurus screen-reader binding closure",
    "evidence-contracts": "Project accessibility evidence contracts",
    "presentation-taxonomy": "Presentation taxonomy activation",
    "accessibility-lint": "Docusaurus accessibility lint",
    "label-consistency": "Docusaurus label consistency",
    "test-coverage": "Docusaurus tests and coverage",
    "browser": "Docusaurus production browser gates",
    "contrast-review": "Docusaurus contrast reviewer manifest",
    "compose-evidence": "HVE accessibility evidence composition",
    "validate-evidence": "Project Docusaurus evidence verdict",
}
_DOCUSAURUS_SCREEN_READER_CASES = (
    "SR-INTEGRITY-001",
    "HVE-NVDA-006",
    "HVE-NVDA-010",
    "HVE-NVDA-011",
    "HVE-NVDA-014",
)


class Framework(StrEnum):
    """Framework identities that must retain separate outcomes."""

    WCAG_2_2 = "WCAG_2_2"
    REVISED_SECTION_508 = "REVISED_SECTION_508"
    SECTION_504 = "SECTION_504"


class Applicability(StrEnum):
    """Applicability state independent from an evidence outcome."""

    APPLICABLE = "APPLICABLE"
    UNDETERMINED = "UNDETERMINED"
    INAPPLICABLE = "INAPPLICABLE"


class Lifecycle(StrEnum):
    """Asset and journey reachability state."""

    ACTIVE = "ACTIVE"
    ENVIRONMENT_GATED = "ENVIRONMENT_GATED"
    CONDITIONAL = "CONDITIONAL"
    DORMANT = "DORMANT"
    BROKEN = "BROKEN"
    ASPIRATIONAL = "ASPIRATIONAL"
    PLANNED = "PLANNED"
    ABSENT = "ABSENT"
    MACHINE_ONLY = "MACHINE_ONLY"


class Ownership(StrEnum):
    """Rendered behavior and evidence ownership boundary."""

    PROJECT = "PROJECT"
    PROVIDER = "PROVIDER"
    SHARED = "SHARED"
    ORGANIZATIONAL = "ORGANIZATIONAL"


class Platform(StrEnum):
    """Asset platform class."""

    WEB = "WEB"
    GENERATED_WEB = "GENERATED_WEB"
    GITHUB_HOSTED = "GITHUB_HOSTED"
    CLI = "CLI"
    CONVERSATION = "CONVERSATION"
    DOCUMENT = "DOCUMENT"
    GENERATED_ARTIFACT = "GENERATED_ARTIFACT"
    PROVIDER = "PROVIDER"
    NATIVE = "NATIVE"
    REMOTE = "REMOTE"
    PHYSICAL = "PHYSICAL"
    MACHINE_ONLY = "MACHINE_ONLY"


class EvidencePack(StrEnum):
    """Risk-based evidence pack identity."""

    E1_SOURCE_CONTENT = "E1_SOURCE_CONTENT"
    E2_DETERMINISTIC_WEB = "E2_DETERMINISTIC_WEB"
    E3_NON_TEXT_GENERATED = "E3_NON_TEXT_GENERATED"
    E4_HUMAN_AT = "E4_HUMAN_AT"
    E5_PROVIDER_NATIVE_ROBOT = "E5_PROVIDER_NATIVE_ROBOT"
    E6_PROCESS_GOVERNANCE = "E6_PROCESS_GOVERNANCE"


class EvidenceStatus(StrEnum):
    """Project evidence outcome vocabulary."""

    PASS = "PASS"
    FAIL = "FAIL"
    CANT_TELL = "CANT_TELL"
    NOT_ASSESSED = "NOT_ASSESSED"
    INAPPLICABLE = "INAPPLICABLE"


class EvidenceMethod(StrEnum):
    """Closed evidence method vocabulary."""

    SOURCE_INSPECTION = "SOURCE_INSPECTION"
    UNIT_TEST = "UNIT_TEST"
    COMPONENT_TEST = "COMPONENT_TEST"
    AXE = "AXE"
    PLAYWRIGHT_KEYBOARD = "PLAYWRIGHT_KEYBOARD"
    PLAYWRIGHT_POINTER = "PLAYWRIGHT_POINTER"
    PLAYWRIGHT_TREE = "PLAYWRIGHT_TREE"
    PLAYWRIGHT_LIVE_REGION = "PLAYWRIGHT_LIVE_REGION"
    PLAYWRIGHT_VISUAL = "PLAYWRIGHT_VISUAL"
    HVE_PROBE = "HVE_PROBE"
    MANUAL_KEYBOARD = "MANUAL_KEYBOARD"
    NVDA = "NVDA"
    JAWS = "JAWS"
    VOICEOVER = "VOICEOVER"
    BRAILLE = "BRAILLE"
    COGNITIVE_REVIEW = "COGNITIVE_REVIEW"
    MEDIA_EQUIVALENCE_REVIEW = "MEDIA_EQUIVALENCE_REVIEW"
    PROVIDER_ASSURANCE = "PROVIDER_ASSURANCE"
    PROVIDER_TASK = "PROVIDER_TASK"
    NATIVE_TASK = "NATIVE_TASK"
    SUPPORT_TASK = "SUPPORT_TASK"
    SAFETY_REVIEW = "SAFETY_REVIEW"
    ARTIFACT_INTEGRITY = "ARTIFACT_INTEGRITY"


class ProbeId(StrEnum):
    """Closed producer probe vocabulary accepted by the project gate."""

    AXE = "probe-axe"
    KEYBOARD_TRAVERSAL = "probe-keyboard-traversal"
    FOCUS_VISIBLE = "probe-focus-visible"
    FOCUS_OBSCURED = "probe-focus-obscured"
    LIVE_REGION = "probe-live-region"
    ARIA_TREE = "probe-aria-tree"
    WIDGET_KEYBOARD = "probe-widget-keyboard"
    REFLOW_RESIZE = "probe-reflow-resize"
    TARGET_SIZE = "probe-target-size"
    CONTRAST = "probe-contrast"
    FORCED_COLORS = "probe-forced-colors"
    REDUCED_MOTION = "probe-reduced-motion"
    STRUCTURE_CRAWL = "probe-structure-crawl"
    NAME_IN_LABEL = "probe-name-in-label"
    USE_OF_COLOR = "probe-use-of-color"
    TEXT_SPACING = "probe-text-spacing"
    HOVER_FOCUS = "probe-hover-focus"
    LINK_PURPOSE = "probe-link-purpose"
    INPUT_PURPOSE = "probe-input-purpose"
    FORMS = "probe-forms"
    CONTEXT_CHANGE = "probe-context-change"
    ORIENTATION = "probe-orientation"
    AUDIO_CONTROL = "probe-audio-control"
    TIMING = "probe-timing"
    ZOOM_BLOCKER = "probe-zoom-blocker"
    CONSOLE_ERRORS = "probe-console-errors"
    BROKEN_LINKS = "probe-broken-links"
    DOM_HYGIENE = "probe-dom-hygiene"
    TITLE_LANG = "probe-title-lang"
    VIRTUAL_SR = "probe-virtual-sr"
    REAL_SR = "probe-real-sr"
    PROJECT_PLAYWRIGHT = "project-playwright"
    PROJECT_SOURCE = "project-source"
    QUALIFIED_HUMAN = "qualified-human"
    PROVIDER_TASK = "provider-task"
    ARTIFACT_INTEGRITY = "artifact-integrity"


class MethodDisposition(StrEnum):
    """Whether a method decides or only informs a proposition."""

    DECIDES = "DECIDES"
    INFORMS = "INFORMS"


class StateProofStatus(StrEnum):
    """Expected-state reachability status."""

    PROVED = "PROVED"
    FAILED = "FAILED"
    UNRESOLVED = "UNRESOLVED"


class QuarantineState(StrEnum):
    """Operational trust state for an evidence bundle."""

    CLEAR = "CLEAR"
    QUARANTINED = "QUARANTINED"


class ReviewState(StrEnum):
    """Review state independent from the product outcome."""

    PENDING = "PENDING"
    AUTOMATION_ACCEPTED = "AUTOMATION_ACCEPTED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    REJECTED = "REJECTED"


class ConflictState(StrEnum):
    """Disposition for explicitly preserved result conflicts."""

    UNRESOLVED = "UNRESOLVED"
    RESOLVED = "RESOLVED"


class EnvironmentClass(StrEnum):
    """Sanitized execution environment class."""

    LOCAL_CI = "LOCAL_CI"
    LOCAL_CONTROLLED = "LOCAL_CONTROLLED"
    NON_PRODUCTION_PROVIDER = "NON_PRODUCTION_PROVIDER"
    AUTHORIZED_PHYSICAL = "AUTHORIZED_PHYSICAL"


class StateAction(StrEnum):
    """Strict ordered setup actions implemented by project-owned runners."""

    NAVIGATE = "NAVIGATE"
    CLICK = "CLICK"
    FOCUS = "FOCUS"
    HOVER = "HOVER"
    FILL = "FILL"
    SELECT = "SELECT"
    PRESS = "PRESS"
    EMULATE = "EMULATE"
    WAIT_FOR = "WAIT_FOR"
    ASSERT = "ASSERT"


_PROJECT_PLAYWRIGHT_METHODS = {
    EvidenceMethod.AXE,
    EvidenceMethod.PLAYWRIGHT_KEYBOARD,
    EvidenceMethod.PLAYWRIGHT_POINTER,
    EvidenceMethod.PLAYWRIGHT_TREE,
    EvidenceMethod.PLAYWRIGHT_LIVE_REGION,
    EvidenceMethod.PLAYWRIGHT_VISUAL,
}
_PROJECT_SOURCE_METHODS = {
    EvidenceMethod.SOURCE_INSPECTION,
    EvidenceMethod.UNIT_TEST,
    EvidenceMethod.COMPONENT_TEST,
}
_QUALIFIED_HUMAN_METHODS = {
    EvidenceMethod.MANUAL_KEYBOARD,
    EvidenceMethod.NVDA,
    EvidenceMethod.JAWS,
    EvidenceMethod.VOICEOVER,
    EvidenceMethod.BRAILLE,
    EvidenceMethod.COGNITIVE_REVIEW,
    EvidenceMethod.MEDIA_EQUIVALENCE_REVIEW,
    EvidenceMethod.NATIVE_TASK,
    EvidenceMethod.SUPPORT_TASK,
    EvidenceMethod.SAFETY_REVIEW,
}


class StrictModel(BaseModel):
    """Closed evidence model with retained-value safety checks."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, validate_assignment=True)

    @model_validator(mode="after")
    def validate_retained_values(self) -> Self:
        """Reject credential-shaped and environment-specific values."""
        for value in _strings(self.model_dump(mode="python")):
            if _CREDENTIAL_PATTERN.search(value):
                raise ValueError("Credential-shaped retained value is forbidden")
            if _ENVIRONMENT_PATTERN.search(value):
                raise ValueError("Environment-specific retained value is forbidden")
        return self


class ContrastMeasurementInput(BaseModel):
    """Rendered evidence for one unresolved contrast signature occurrence."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    background: str | None
    background_image: str | None = Field(alias="backgroundImage")
    evidence_path: str | None = Field(alias="evidencePath")
    foreground: str | None
    method_status: Literal[
        "computed-ratio-pass-candidate",
        "computed-ratio-failure-candidate",
        "qualified-review-required",
        "insufficient-evidence",
    ] = Field(alias="methodStatus")
    obscured: bool | None
    opacity: float | None
    ratio: float | None
    reason: str
    required_ratio: float | None = Field(alias="requiredRatio")
    route: str
    signature: str = Field(pattern=r"^[0-9a-f]{16}$")
    state: str
    target: str
    theme: Literal["light", "dark"]
    tuple_digest: str = Field(alias="tupleDigest", pattern=r"^[0-9a-f]{64}$")


class ContrastRouteInput(BaseModel):
    """Contrast evidence retained for one route, theme, and state."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    contrast_evidence: list[ContrastMeasurementInput] = Field(alias="contrastEvidence", default_factory=list)


class ContrastCrawlInput(BaseModel):
    """Typed subset of the all-route crawl consumed by the review builder."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = Field(alias="schemaVersion")
    results: list[ContrastRouteInput]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, (list, tuple, set)):
        return [text for item in value for text in _strings(item)]
    return []


def _stable_id(value: str | None) -> str | None:
    if value is not None and not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid stable ID: {value}")
    return value


def _digest(value: str | None) -> str | None:
    if value is not None and not _DIGEST_PATTERN.fullmatch(value):
        raise ValueError("Digest must be a 64-character SHA-256 value")
    return value.lower() if value is not None else None


def _git_commit(value: str) -> str:
    if not _GIT_COMMIT_PATTERN.fullmatch(value):
        raise ValueError("Repository commit must be a 40- or 64-character Git object ID")
    return value.lower()


def _duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


class Asset(StrictModel):
    """Asset inventory record."""

    asset_id: str
    name: str = Field(min_length=1)
    lifecycle: Lifecycle
    owner: str = Field(min_length=1)
    ownership: Ownership
    platform: Platform
    activation: str = Field(min_length=1)
    invalidation: str = Field(min_length=1)
    retest: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    dependency_refs: list[str] = Field(default_factory=list)

    _asset_id = field_validator("asset_id")(_stable_id)

    @model_validator(mode="after")
    def validate_reference_lists(self) -> Self:
        """Reject duplicate asset evidence and dependency references."""
        for label, values in (("evidence", self.evidence_refs), ("dependency", self.dependency_refs)):
            if duplicates := _duplicates(values):
                raise ValueError(f"Duplicate asset {label} references: {duplicates}")
        return self


class Fixture(StrictModel):
    """Deterministic or controlled fixture record."""

    fixture_id: str
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    environment_class: EnvironmentClass
    synthetic: bool

    _fixture_id = field_validator("fixture_id")(_stable_id)


class Journey(StrictModel):
    """Exact stateful user task and expected outcome."""

    journey_id: str
    asset_id: str
    lifecycle: Lifecycle
    owner: str = Field(min_length=1)
    role: str = Field(min_length=1)
    platform: Platform
    state: str = Field(min_length=1)
    task: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)
    fixture_id: str
    predecessor_ids: list[str] = Field(default_factory=list)
    successor_ids: list[str] = Field(default_factory=list)
    handoff: str | None = None
    activation: str = Field(min_length=1)
    invalidation: str = Field(min_length=1)
    retest: str = Field(min_length=1)
    required_methods: list[EvidenceMethod] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    dependency_refs: list[str] = Field(default_factory=list)

    _ids = field_validator("journey_id", "asset_id", "fixture_id")(_stable_id)

    @model_validator(mode="after")
    def validate_reference_lists(self) -> Self:
        """Reject duplicate journey evidence and dependency references."""
        for label, values in (("evidence", self.evidence_refs), ("dependency", self.dependency_refs)):
            if duplicates := _duplicates(values):
                raise ValueError(f"Duplicate journey {label} references: {duplicates}")
        return self


class Exclusion(StrictModel):
    """Explicit inventory exclusion."""

    exclusion_id: str
    scope: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    _exclusion_id = field_validator("exclusion_id")(_stable_id)


class AssetJourneyLedger(StrictModel):
    """Canonical asset and journey ledger."""

    schema_version: str = Field(pattern=r"^1\.\d+\.\d+$")
    assets: list[Asset] = Field(min_length=1)
    fixtures: list[Fixture] = Field(min_length=1)
    journeys: list[Journey] = Field(min_length=1)
    exclusions: list[Exclusion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        """Reject duplicate IDs and unresolved inventory references."""
        all_ids = [item.asset_id for item in self.assets]
        all_ids += [item.fixture_id for item in self.fixtures]
        all_ids += [item.journey_id for item in self.journeys]
        all_ids += [item.exclusion_id for item in self.exclusions]
        if duplicates := _duplicates(all_ids):
            raise ValueError(f"Duplicate stable IDs: {duplicates}")

        asset_ids = {item.asset_id for item in self.assets}
        fixture_ids = {item.fixture_id for item in self.fixtures}
        journey_ids = {item.journey_id for item in self.journeys}
        for journey in self.journeys:
            if journey.asset_id not in asset_ids:
                raise ValueError(f"Journey {journey.journey_id} references unknown asset")
            if journey.fixture_id not in fixture_ids:
                raise ValueError(f"Journey {journey.journey_id} references unknown fixture")
            related = journey.predecessor_ids + journey.successor_ids
            if duplicates := _duplicates(related):
                raise ValueError(f"Duplicate related journey IDs: {duplicates}")
            if unknown := sorted(set(related) - journey_ids):
                raise ValueError(f"Journey {journey.journey_id} references unknown journeys {unknown}")
            if journey.journey_id in related:
                raise ValueError(f"Journey {journey.journey_id} cannot reference itself")
        return self


class MethodAssignment(StrictModel):
    """Method adequacy for an exact proposition."""

    method: EvidenceMethod
    disposition: MethodDisposition
    proposition: str = Field(min_length=1)


class Requirement(StrictModel):
    """Framework-specific proposition and evidence contract."""

    requirement_id: str
    framework: Framework
    criterion: str = Field(min_length=1)
    proposition: str = Field(min_length=1)
    journey_ids: list[str] = Field(min_length=1)
    applicability: Applicability
    applicability_rationale: str = Field(min_length=1)
    evidence_status: EvidenceStatus
    evidence_packs: list[EvidencePack] = Field(min_length=1)
    methods: list[MethodAssignment] = Field(min_length=1)
    owner: str = Field(min_length=1)
    freshness_rule: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_digests: list[str] = Field(default_factory=list)

    _requirement_id = field_validator("requirement_id")(_stable_id)

    @model_validator(mode="after")
    def validate_unique_bindings(self) -> Self:
        """Reject repeated journeys, packs, and methods."""
        for label, values in (
            ("journey", self.journey_ids),
            ("evidence pack", [item.value for item in self.evidence_packs]),
            ("method", [item.method.value for item in self.methods]),
            ("evidence reference", self.evidence_refs),
            ("evidence digest", self.evidence_digests),
        ):
            if duplicates := _duplicates(values):
                raise ValueError(f"Duplicate {label} bindings: {duplicates}")
        for digest in self.evidence_digests:
            _digest(digest)
        if self.evidence_status is EvidenceStatus.INAPPLICABLE and self.applicability is not Applicability.INAPPLICABLE:
            raise ValueError("INAPPLICABLE evidence requires INAPPLICABLE applicability")
        return self


class RequirementEvidenceLedger(StrictModel):
    """Canonical requirement and method-adequacy ledger."""

    schema_version: str = Field(pattern=r"^1\.\d+\.\d+$")
    requirements: list[Requirement] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        """Reject duplicate requirement IDs."""
        if duplicates := _duplicates([item.requirement_id for item in self.requirements]):
            raise ValueError(f"Duplicate requirement IDs: {duplicates}")
        return self


class SourceBinding(StrictModel):
    """Repository revision and intentional dirty-tree binding."""

    repository_commit: str
    dirty: bool
    dirty_tree_digest: str | None = None

    _repository_commit = field_validator("repository_commit")(_git_commit)
    _dirty_tree_digest = field_validator("dirty_tree_digest")(_digest)

    @model_validator(mode="after")
    def validate_dirty_state(self) -> Self:
        """Require a tree digest exactly when evidence is dirty."""
        if self.dirty != (self.dirty_tree_digest is not None):
            raise ValueError("dirty_tree_digest must be present exactly when dirty is true")
        return self


class ReproducibilityBindings(StrictModel):
    """Content bindings needed to reproduce or invalidate a run."""

    source: SourceBinding
    build_digest: str
    config_digest: str
    fixture_digest: str
    lockfile_digest: str
    tool_digest: str
    harness_package_digest: str
    criterion_map_digest: str

    _digests = field_validator(
        "build_digest",
        "config_digest",
        "fixture_digest",
        "lockfile_digest",
        "tool_digest",
        "harness_package_digest",
        "criterion_map_digest",
    )(_digest)


class RunScope(StrictModel):
    """Closed nonempty inventory selected for one evidence run."""

    asset_ids: list[str] = Field(min_length=1)
    journey_ids: list[str] = Field(min_length=1)
    states: list[str] = Field(min_length=1)
    fixture_ids: list[str] = Field(min_length=1)
    roles: list[str] = Field(min_length=1)
    probes: list[ProbeId] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_scope(self) -> Self:
        """Reject duplicate scope entries and invalid stable IDs."""
        for label, values in (
            ("asset", self.asset_ids),
            ("journey", self.journey_ids),
            ("state", self.states),
            ("fixture", self.fixture_ids),
            ("role", self.roles),
            ("probe", [item.value for item in self.probes]),
        ):
            if duplicates := _duplicates(values):
                raise ValueError(f"Duplicate run-scope {label} entries: {duplicates}")
        for stable_id in self.asset_ids + self.journey_ids + self.fixture_ids:
            _stable_id(stable_id)
        return self


class RunManifest(StrictModel):
    """Revision-bound run metadata."""

    schema_version: str = Field(pattern=r"^1\.\d+\.\d+$")
    run_id: str
    created_at: datetime
    bindings: ReproducibilityBindings
    scope: RunScope
    environment_class: EnvironmentClass
    application_name: str = Field(min_length=1)
    application_version: str = Field(min_length=1)
    operating_system_name: str = Field(min_length=1)
    operating_system_version: str = Field(min_length=1)
    browser_name: str | None = None
    browser_version: str | None = None
    assistive_technology_name: str | None = None
    assistive_technology_version: str | None = None
    provider_name: str | None = None
    provider_version: str | None = None
    locale: str = Field(min_length=1)
    viewport: str | None = None
    input_modes: list[str] = Field(min_length=1)

    _run_id = field_validator("run_id")(_stable_id)

    @field_validator("created_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        """Require a timezone-aware timestamp."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a UTC offset")
        return value

    @model_validator(mode="after")
    def validate_version_pairs(self) -> Self:
        """Require a version whenever an optional runtime product is named."""
        pairs = (
            ("browser", self.browser_name, self.browser_version),
            ("assistive technology", self.assistive_technology_name, self.assistive_technology_version),
            ("provider", self.provider_name, self.provider_version),
        )
        for label, name, version in pairs:
            if (name is None) != (version is None):
                raise ValueError(f"{label} name and version must be provided together")
        return self


class Artifact(StrictModel):
    """Hashed generated artifact."""

    artifact_id: str
    path: str
    media_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str

    _artifact_id = field_validator("artifact_id")(_stable_id)
    _sha256 = field_validator("sha256")(_digest)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        """Require a repository-relative POSIX artifact path."""
        path = PurePosixPath(value)
        if not value or "\\" in value or path.is_absolute() or ".." in path.parts or ":" in value:
            raise ValueError("Artifact path must be repository-relative POSIX syntax")
        return value


class BundleManifest(StrictModel):
    """Artifact integrity, quarantine, and review envelope."""

    schema_version: str = Field(pattern=r"^1\.\d+\.\d+$")
    bundle_id: str
    run_id: str
    run_manifest_digest: str
    artifacts: list[Artifact] = Field(min_length=1)
    quarantine_state: QuarantineState
    quarantine_reasons: list[str] = Field(default_factory=list)
    review_state: ReviewState
    non_attestation_notice: bool

    _ids = field_validator("bundle_id", "run_id")(_stable_id)
    _digest = field_validator("run_manifest_digest")(_digest)

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        """Reject duplicate artifacts and contradictory quarantine state."""
        if duplicates := _duplicates([item.artifact_id for item in self.artifacts]):
            raise ValueError(f"Duplicate artifact IDs: {duplicates}")
        if (self.quarantine_state is QuarantineState.QUARANTINED) != bool(self.quarantine_reasons):
            raise ValueError("Quarantine state and reasons must agree")
        if not self.non_attestation_notice:
            raise ValueError("Bundle must retain the non-attestation notice")
        return self


class StateStep(StrictModel):
    """One ordered, bounded, project-owned state setup operation."""

    sequence: int = Field(ge=1)
    action: StateAction
    target: str = Field(min_length=1)
    value: str | None = None
    timeout_ms: int = Field(default=5000, ge=1, le=30000)


class StateProof(StrictModel):
    """Proof that a journey reached or failed to reach its expected state."""

    proof_id: str
    run_id: str
    journey_id: str
    fixture_id: str
    role: str = Field(min_length=1)
    route: str = Field(min_length=1)
    status: StateProofStatus
    expected: str = Field(min_length=1)
    observed: str = Field(min_length=1)
    setup_steps: list[StateStep] = Field(min_length=2)
    route_matched: bool
    fixture_matched: bool
    action_errors: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(min_length=1)

    _ids = field_validator("proof_id", "run_id", "journey_id", "fixture_id")(_stable_id)

    @model_validator(mode="after")
    def validate_state_execution(self) -> Self:
        """Require ordered navigation and a positive final postcondition."""
        sequences = [step.sequence for step in self.setup_steps]
        if sequences != list(range(1, len(self.setup_steps) + 1)):
            raise ValueError("State setup sequence must be contiguous and ordered")
        if self.setup_steps[0].action is not StateAction.NAVIGATE:
            raise ValueError("State setup must begin with NAVIGATE")
        if self.setup_steps[-1].action is not StateAction.ASSERT:
            raise ValueError("State setup must end with ASSERT")

        state_reached = self.route_matched and self.fixture_matched and not self.action_errors
        if self.status is StateProofStatus.PROVED and not state_reached:
            raise ValueError("PROVED state requires route, fixture, and action success")
        if self.status is not StateProofStatus.PROVED and state_reached:
            raise ValueError("Failed or unresolved state proof requires an explicit setup failure")
        return self


class ExpectedCell(StrictModel):
    """One required proposition, journey, method, and probe execution cell."""

    cell_id: str
    requirement_id: str
    journey_id: str
    method: EvidenceMethod
    disposition: MethodDisposition
    probe: ProbeId
    expected_assertion: str = Field(min_length=1)
    required: bool = True

    _ids = field_validator("cell_id", "requirement_id", "journey_id")(_stable_id)

    @model_validator(mode="after")
    def validate_probe_method(self) -> Self:
        """Reject probe and method combinations outside their trust domain."""
        if self.probe is ProbeId.PROJECT_PLAYWRIGHT and self.method not in _PROJECT_PLAYWRIGHT_METHODS:
            raise ValueError("project-playwright does not support the selected method")
        if self.probe is ProbeId.PROJECT_SOURCE and self.method not in _PROJECT_SOURCE_METHODS:
            raise ValueError("project-source does not support the selected method")
        if self.probe is ProbeId.QUALIFIED_HUMAN and self.method not in _QUALIFIED_HUMAN_METHODS:
            raise ValueError("qualified-human does not support the selected method")
        if self.probe is ProbeId.PROVIDER_TASK and self.method not in {
            EvidenceMethod.PROVIDER_ASSURANCE,
            EvidenceMethod.PROVIDER_TASK,
        }:
            raise ValueError("provider-task does not support the selected method")
        if self.probe is ProbeId.ARTIFACT_INTEGRITY and self.method is not EvidenceMethod.ARTIFACT_INTEGRITY:
            raise ValueError("artifact-integrity does not support the selected method")
        if self.probe.value.startswith("probe-") and self.method is not EvidenceMethod.HVE_PROBE:
            raise ValueError("HVE probes require the HVE_PROBE method")
        return self


class EvidenceResult(StrictModel):
    """One framework-specific result for an exact journey proposition."""

    result_id: str
    run_id: str
    framework: Framework
    requirement_id: str
    journey_id: str
    method: EvidenceMethod
    disposition: MethodDisposition
    status: EvidenceStatus
    applicability: Applicability
    state_proof_id: str | None = None
    expected_result: str = Field(min_length=1)
    observed_result: str = Field(min_length=1)
    artifact_ids: list[str] = Field(default_factory=list)
    defect_id: str | None = None
    limitation: str | None = None
    reviewer_id: str = Field(min_length=1)
    review_state: ReviewState
    observed_at: datetime
    valid_until: datetime | None = None
    invalidated: bool = False
    invalidation_reasons: list[str] = Field(default_factory=list)
    quarantined: bool = False
    quarantine_reasons: list[str] = Field(default_factory=list)
    supersedes_result_id: str | None = None

    _ids = field_validator(
        "result_id", "run_id", "requirement_id", "journey_id", "state_proof_id", "supersedes_result_id"
    )(_stable_id)

    @model_validator(mode="after")
    def validate_transition(self) -> Self:
        """Reject invalid status, method, applicability, and review transitions."""
        decisive = self.status in {EvidenceStatus.PASS, EvidenceStatus.FAIL}
        if self.disposition is MethodDisposition.INFORMS and decisive:
            raise ValueError("Informing methods cannot produce PASS or FAIL")
        if decisive:
            if self.applicability is not Applicability.APPLICABLE:
                raise ValueError("PASS and FAIL require APPLICABLE status")
            if self.state_proof_id is None or not self.artifact_ids:
                raise ValueError("PASS and FAIL require state proof and artifacts")
            if self.review_state not in {ReviewState.AUTOMATION_ACCEPTED, ReviewState.HUMAN_APPROVED}:
                raise ValueError("PASS and FAIL require accepted review")
        if self.status is EvidenceStatus.INAPPLICABLE and (
            self.applicability is not Applicability.INAPPLICABLE
            or not self.limitation
            or self.review_state is not ReviewState.HUMAN_APPROVED
        ):
            raise ValueError("INAPPLICABLE requires rationale and human approval")
        if self.status is EvidenceStatus.NOT_ASSESSED and not self.limitation:
            raise ValueError("NOT_ASSESSED requires a limitation and re-entry reason")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a UTC offset")
        if self.valid_until is not None:
            if self.valid_until.tzinfo is None or self.valid_until.utcoffset() is None:
                raise ValueError("valid_until must include a UTC offset")
            if self.valid_until <= self.observed_at:
                raise ValueError("valid_until must be later than observed_at")
        if self.invalidated != bool(self.invalidation_reasons):
            raise ValueError("Result invalidation state and reasons must agree")
        if self.quarantined != bool(self.quarantine_reasons):
            raise ValueError("Result quarantine state and reasons must agree")
        if self.quarantined and decisive:
            raise ValueError("Quarantined results cannot produce PASS or FAIL")
        if self.supersedes_result_id == self.result_id:
            raise ValueError("A result cannot supersede itself")
        return self


class EvidenceConflict(StrictModel):
    """Explicit preservation and review of conflicting current outcomes."""

    conflict_id: str
    result_ids: list[str] = Field(min_length=2)
    state: ConflictState
    resolution: str | None = None
    reviewer_id: str | None = None

    _conflict_id = field_validator("conflict_id")(_stable_id)

    @model_validator(mode="after")
    def validate_conflict_state(self) -> Self:
        """Require unique results and reviewed evidence for resolved conflicts."""
        for result_id in self.result_ids:
            _stable_id(result_id)
        if duplicates := _duplicates(self.result_ids):
            raise ValueError(f"Duplicate conflict result IDs: {duplicates}")
        if self.state is ConflictState.RESOLVED and (not self.resolution or not self.reviewer_id):
            raise ValueError("Resolved conflict requires resolution and reviewer_id")
        if self.state is ConflictState.UNRESOLVED and (self.resolution or self.reviewer_id):
            raise ValueError("Unresolved conflict cannot include a resolution or reviewer")
        return self


class EvidenceBundle(StrictModel):
    """Complete trust boundary for canonical and generated evidence."""

    asset_ledger: AssetJourneyLedger
    requirement_ledger: RequirementEvidenceLedger
    run_manifest: RunManifest
    bundle_manifest: BundleManifest
    evaluated_at: datetime
    expected_cells: list[ExpectedCell] = Field(min_length=1)
    state_proofs: list[StateProof] = Field(min_length=1)
    results: list[EvidenceResult] = Field(min_length=1)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        """Reject unresolved references, framework reuse, and current conflicts."""
        journeys = {item.journey_id: item for item in self.asset_ledger.journeys}
        requirements = {item.requirement_id: item for item in self.requirement_ledger.requirements}
        artifacts = {item.artifact_id for item in self.bundle_manifest.artifacts}
        proofs = {item.proof_id: item for item in self.state_proofs}
        results = {item.result_id: item for item in self.results}
        cells = {item.cell_id: item for item in self.expected_cells}
        conflicts = {item.conflict_id: item for item in self.conflicts}
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must include a UTC offset")
        if duplicates := _duplicates([item.conflict_id for item in self.conflicts]):
            raise ValueError(f"Duplicate conflict IDs: {duplicates}")
        if duplicates := _duplicates([item.cell_id for item in self.expected_cells]):
            raise ValueError(f"Duplicate expected cell IDs: {duplicates}")
        if duplicates := _duplicates([item.proof_id for item in self.state_proofs]):
            raise ValueError(f"Duplicate state proof IDs: {duplicates}")
        if duplicates := _duplicates([item.result_id for item in self.results]):
            raise ValueError(f"Duplicate result IDs: {duplicates}")
        if self.bundle_manifest.run_id != self.run_manifest.run_id:
            raise ValueError("Bundle references an unknown run")

        asset_ids = {item.asset_id for item in self.asset_ledger.assets}
        fixture_ids = {item.fixture_id for item in self.asset_ledger.fixtures}
        scope = self.run_manifest.scope
        if unknown := sorted(set(scope.asset_ids) - asset_ids):
            raise ValueError(f"Run scope references unknown assets {unknown}")
        if unknown := sorted(set(scope.journey_ids) - set(journeys)):
            raise ValueError(f"Run scope references unknown journeys {unknown}")
        if unknown := sorted(set(scope.fixture_ids) - fixture_ids):
            raise ValueError(f"Run scope references unknown fixtures {unknown}")

        for cell in cells.values():
            requirement = requirements.get(cell.requirement_id)
            if requirement is None or cell.journey_id not in journeys:
                raise ValueError(f"Expected cell {cell.cell_id} has an unknown journey or requirement")
            if cell.journey_id not in requirement.journey_ids:
                raise ValueError(f"Expected cell {cell.cell_id} uses a journey outside its requirement")
            assignment = next((item for item in requirement.methods if item.method is cell.method), None)
            if assignment is None or assignment.disposition is not cell.disposition:
                raise ValueError(f"Expected cell {cell.cell_id} uses an unsupported method")
            if cell.journey_id not in scope.journey_ids or cell.probe not in scope.probes:
                raise ValueError(f"Expected cell {cell.cell_id} is outside the declared run scope")

        covered_journeys = {cell.journey_id for cell in cells.values()}
        if missing := sorted(set(scope.journey_ids) - covered_journeys):
            raise ValueError(f"Run scope journeys are missing expected cells: {missing}")
        covered_probes = {cell.probe for cell in cells.values()}
        if missing := sorted(set(scope.probes) - covered_probes):
            raise ValueError(f"Run scope probes are missing expected cells: {missing}")

        for proof in self.state_proofs:
            if proof.run_id != self.run_manifest.run_id or proof.journey_id not in journeys:
                raise ValueError(f"State proof {proof.proof_id} has an unresolved reference")
            if (
                proof.journey_id not in scope.journey_ids
                or proof.fixture_id not in scope.fixture_ids
                or proof.role not in scope.roles
            ):
                raise ValueError(f"State proof {proof.proof_id} is outside the declared run scope")
            if unknown := sorted(set(proof.artifact_ids) - artifacts):
                raise ValueError(f"State proof {proof.proof_id} references unknown artifacts {unknown}")

        superseded = {item.supersedes_result_id for item in self.results if item.supersedes_result_id}
        current: dict[tuple[str, str], list[EvidenceResult]] = {}
        for result in self.results:
            requirement = requirements.get(result.requirement_id)
            if requirement is None or result.journey_id not in journeys:
                raise ValueError(f"Result {result.result_id} has an unknown journey or requirement")
            if result.journey_id not in requirement.journey_ids:
                raise ValueError(f"Result {result.result_id} uses a journey outside its requirement")
            if result.framework is not requirement.framework:
                raise ValueError(f"Result {result.result_id} reuses an outcome across frameworks")
            assignment = next((item for item in requirement.methods if item.method is result.method), None)
            if assignment is None or assignment.disposition != result.disposition:
                raise ValueError(f"Result {result.result_id} uses an unknown or mismatched method")
            if result.run_id != self.run_manifest.run_id or set(result.artifact_ids) - artifacts:
                raise ValueError(f"Result {result.result_id} has an unresolved run or artifact")
            if result.state_proof_id:
                proof = proofs.get(result.state_proof_id)
                if proof is None or proof.journey_id != result.journey_id:
                    raise ValueError(f"Result {result.result_id} has unresolved or mismatched state proof")
                if (
                    result.status in {EvidenceStatus.PASS, EvidenceStatus.FAIL}
                    and proof.status is not StateProofStatus.PROVED
                ):
                    raise ValueError(f"Result {result.result_id} requires a proved state")
                if proof.status is not StateProofStatus.PROVED and not result.quarantined:
                    raise ValueError(f"Result {result.result_id} must be quarantined after state setup failure")
                if proof.status is not StateProofStatus.PROVED and result.status not in {
                    EvidenceStatus.CANT_TELL,
                    EvidenceStatus.NOT_ASSESSED,
                }:
                    raise ValueError(f"Result {result.result_id} must retain uncertainty after state setup failure")
                if proof.fixture_id != journeys[result.journey_id].fixture_id:
                    raise ValueError(f"Result {result.result_id} state proof uses the wrong fixture")
            if result.supersedes_result_id:
                previous = results.get(result.supersedes_result_id)
                if previous is None:
                    raise ValueError(f"Result {result.result_id} supersedes an unknown result")
                if (previous.framework, previous.requirement_id, previous.journey_id) != (
                    result.framework,
                    result.requirement_id,
                    result.journey_id,
                ):
                    raise ValueError(f"Result {result.result_id} supersedes a different proposition")
                if result.observed_at <= previous.observed_at:
                    raise ValueError(f"Result {result.result_id} must be newer than the superseded result")
            if result.observed_at > self.evaluated_at:
                raise ValueError(f"Result {result.result_id} cannot be observed after bundle evaluation")
            if result.result_id not in superseded and result.disposition is MethodDisposition.DECIDES:
                current.setdefault((result.requirement_id, result.journey_id), []).append(result)

        for conflict in conflicts.values():
            conflict_results = [results.get(result_id) for result_id in conflict.result_ids]
            if any(result is None for result in conflict_results):
                raise ValueError(f"Conflict {conflict.conflict_id} references an unknown result")
            propositions = {
                (result.framework, result.requirement_id, result.journey_id)
                for result in conflict_results
                if result is not None
            }
            statuses = {result.status for result in conflict_results if result is not None}
            if len(propositions) != 1 or len(statuses) < 2:
                raise ValueError(
                    f"Conflict {conflict.conflict_id} must preserve different outcomes for one proposition"
                )

        for key, key_results in current.items():
            statuses = {result.status for result in key_results}
            if {EvidenceStatus.PASS, EvidenceStatus.FAIL} <= statuses:
                result_ids = {result.result_id for result in key_results}
                matching_conflicts = [
                    conflict for conflict in conflicts.values() if result_ids <= set(conflict.result_ids)
                ]
                if not matching_conflicts:
                    raise ValueError(f"Conflicting current outcomes for {key} require an explicit conflict record")

        current_results = [item for item in self.results if item.result_id not in superseded]
        for cell in cells.values():
            matches = [
                result
                for result in current_results
                if result.requirement_id == cell.requirement_id
                and result.journey_id == cell.journey_id
                and result.method is cell.method
                and result.disposition is cell.disposition
            ]
            if cell.required and not matches:
                raise ValueError(f"Expected cell {cell.cell_id} requires a current result")
            if len(matches) > 1:
                match_ids = {result.result_id for result in matches}
                if not any(match_ids <= set(conflict.result_ids) for conflict in conflicts.values()):
                    raise ValueError(f"Expected cell {cell.cell_id} has unrecorded duplicate current results")
            if matches and (
                cell.probe is ProbeId.PROJECT_PLAYWRIGHT or cell.probe.value.startswith("probe-")
            ) and matches[0].state_proof_id is None:
                raise ValueError(f"Expected cell {cell.cell_id} requires project-owned state proof")

        if self.bundle_manifest.quarantine_state is QuarantineState.QUARANTINED and any(
            result.status is EvidenceStatus.PASS for result in self.results
        ):
            raise ValueError("Quarantined bundles cannot contain PASS results")
        return self


class EvidenceSummary(StrictModel):
    """Fail-safe project verdict without a synthetic compliance score."""

    run_id: str
    bundle_id: str
    evaluated_at: datetime
    verdict: EvidenceStatus
    status_counts: dict[str, int]
    reasons: list[str]
    attestation: bool = False

    _ids = field_validator("run_id", "bundle_id")(_stable_id)

    @field_validator("attestation")
    @classmethod
    def validate_non_attestation(cls, value: bool) -> bool:
        """Reject any attempt to use a gate summary as attestation."""
        if value:
            raise ValueError("Evidence summary cannot be an attestation")
        return value


def canonical_model_digest(model: BaseModel) -> str:
    """Return a stable SHA-256 digest for one validated model."""
    payload = json.dumps(model.model_dump(mode="json"), separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_bundle_integrity(bundle: EvidenceBundle, repository_root: Path) -> None:
    """Verify the run-manifest digest and every generated artifact byte."""
    errors: list[str] = []
    expected_run_digest = canonical_model_digest(bundle.run_manifest)
    if bundle.bundle_manifest.run_manifest_digest != expected_run_digest:
        errors.append("run manifest digest mismatch")

    resolved_root = repository_root.resolve()
    for artifact in bundle.bundle_manifest.artifacts:
        artifact_path = (resolved_root / artifact.path).resolve()
        if not artifact_path.is_relative_to(resolved_root):
            errors.append(f"artifact {artifact.artifact_id} escapes the repository root")
            continue
        if not artifact_path.is_file():
            errors.append(f"artifact {artifact.artifact_id} is missing")
            continue

        content = artifact_path.read_bytes()
        if len(content) != artifact.size_bytes:
            errors.append(f"artifact {artifact.artifact_id} size mismatch")
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            errors.append(f"artifact {artifact.artifact_id} digest mismatch")

    if errors:
        raise ValueError("Bundle integrity failed: " + "; ".join(errors))


def summarize_evidence(bundle: EvidenceBundle) -> EvidenceSummary:
    """Aggregate current results using fail-safe project rules."""
    superseded = {item.supersedes_result_id for item in bundle.results if item.supersedes_result_id}
    current = [item for item in bundle.results if item.result_id not in superseded]
    counts = Counter(item.status.value for item in current)
    reasons: list[str] = []

    deciding_failures = [
        item
        for item in current
        if item.disposition is MethodDisposition.DECIDES
        and item.status is EvidenceStatus.FAIL
        and not item.invalidated
        and not item.quarantined
        and (item.valid_until is None or item.valid_until >= bundle.evaluated_at)
    ]
    if deciding_failures:
        verdict = EvidenceStatus.FAIL
        reasons.append("A current deciding failure controls the project verdict")
    else:
        stale = [
            item
            for item in current
            if item.invalidated or (item.valid_until is not None and item.valid_until < bundle.evaluated_at)
        ]
        unresolved_conflicts = [
            conflict for conflict in bundle.conflicts if conflict.state is ConflictState.UNRESOLVED
        ]
        if bundle.bundle_manifest.quarantine_state is QuarantineState.QUARANTINED:
            reasons.append("The evidence bundle is quarantined")
        if any(item.quarantined for item in current):
            reasons.append("At least one current result is quarantined")
        if stale:
            reasons.append("At least one current result is stale or invalidated")
        if unresolved_conflicts:
            reasons.append("At least one current result conflict is unresolved")
        if any(item.disposition is MethodDisposition.INFORMS for item in current):
            reasons.append("Informing evidence cannot establish a project pass")
        if any(item.status is EvidenceStatus.CANT_TELL for item in current):
            reasons.append("At least one current result cannot be determined")
        if any(item.status is EvidenceStatus.NOT_ASSESSED for item in current):
            reasons.append("At least one required current result is not assessed")

        if reasons:
            if all(item.status is EvidenceStatus.NOT_ASSESSED for item in current):
                verdict = EvidenceStatus.NOT_ASSESSED
            else:
                verdict = EvidenceStatus.CANT_TELL
        elif all(item.status is EvidenceStatus.INAPPLICABLE for item in current):
            verdict = EvidenceStatus.INAPPLICABLE
        elif all(
            item.disposition is MethodDisposition.DECIDES
            and item.status in {EvidenceStatus.PASS, EvidenceStatus.INAPPLICABLE}
            for item in current
        ):
            verdict = EvidenceStatus.PASS
        else:
            verdict = EvidenceStatus.CANT_TELL
            reasons.append("Current results do not establish a complete deciding outcome")

    return EvidenceSummary(
        run_id=bundle.run_manifest.run_id,
        bundle_id=bundle.bundle_manifest.bundle_id,
        evaluated_at=bundle.evaluated_at,
        verdict=verdict,
        status_counts=dict(sorted(counts.items())),
        reasons=reasons,
    )


def format_human_summary(summary: EvidenceSummary) -> str:
    """Render a concise non-attestation summary for human review."""
    reasons = "; ".join(summary.reasons) if summary.reasons else "All required current deciding outcomes passed"
    return "\n".join(
        (
            f"Accessibility evidence verdict: {summary.verdict.value}",
            f"Run: {summary.run_id}",
            f"Bundle: {summary.bundle_id}",
            f"Basis: {reasons}",
            "Attestation: false",
        )
    )


def _hve_canonicalize(value: Any, parent_key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {key: _hve_canonicalize(value[key], key) for key in sorted(value)}
    if isinstance(value, list):
        items = [_hve_canonicalize(item) for item in value]
        if parent_key != "steps":
            items.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        return items
    return value


def _hve_canonical_digest(value: Any, domain: str) -> str:
    payload = json.dumps(
        _hve_canonicalize(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(domain.encode() + b"\0" + (payload + "\n").encode()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _digest_paths(paths: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        resolved = path.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(root.resolve()):
            raise ValueError(f"Digest input must be a file inside the repository: {path}")
        digest.update(resolved.relative_to(root.resolve()).as_posix().encode())
        digest.update(b"\0")
        digest.update(resolved.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _artifact_record(path: Path, repository_root: Path, artifact_id: str) -> dict[str, Any]:
    resolved = path.resolve()
    root = repository_root.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise ValueError(f"Evidence artifact must be a file inside the repository: {path}")
    content = resolved.read_bytes()
    return {
        "artifactId": artifact_id,
        "path": resolved.relative_to(root).as_posix(),
        "mediaType": "application/json",
        "sizeBytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def prepare_docusaurus_source_manifest(
    repository_root: Path,
    output_path: Path,
    cadence: Literal["pull-request", "scheduled", "release"],
) -> dict[str, Any]:
    """Inventory controlling Docusaurus source bytes before generated output exists."""
    root = repository_root.resolve()
    output = output_path.resolve()
    if not output.is_relative_to(root):
        raise ValueError("Docusaurus source manifest must remain inside the repository")
    declared_roots = (
        ".github/accessibility",
        ".github/workflows/docusaurus-tests.yml",
        "docs/docusaurus",
        "scripts/accessibility/evidence_gate.py",
        "tests/test_accessibility_evidence.py",
    )
    excluded_prefixes = (
        "docs/docusaurus/build/",
        "docs/docusaurus/coverage/",
        "docs/docusaurus/node_modules/",
        "docs/docusaurus/playwright-report/",
        "docs/docusaurus/test-results/",
    )
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *declared_roots],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout.decode().split("\0")
    paths = sorted(
        path
        for path in listed
        if path and not any(path.startswith(prefix) for prefix in excluded_prefixes)
    )
    tracked_modes = {
        entry.split(maxsplit=3)[3]: entry.split(maxsplit=1)[0]
        for entry in subprocess.run(
            ["git", "ls-files", "--stage", *paths],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if len(entry.split(maxsplit=3)) == 4
    }
    files = [
        {
            "path": path,
            "mode": tracked_modes.get(path, "100644"),
            "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
        }
        for path in paths
        if (root / path).is_file()
    ]
    status_output = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *declared_roots],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    dirty_entries = sorted(line for line in status_output.splitlines() if line.strip())
    if cadence == "release" and dirty_entries:
        raise ValueError("Release source manifest requires clean declared source roots")
    source_input_digest = hashlib.sha256(
        json.dumps(files, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    payload = {
        "schemaVersion": "1.0.0",
        "sourceRevision": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "sourceInputDigest": source_input_digest,
        "dirty": bool(dirty_entries),
        "dirtyEntries": dirty_entries,
        "declaredRoots": list(declared_roots),
        "excludedGeneratedPrefixes": list(excluded_prefixes),
        "files": files,
    }
    _write_json(output, payload)
    return payload


def _docusaurus_exact_results(
    manifest: dict[str, Any],
    requirement_catalog: list[dict[str, Any]],
    project: str = "system-chrome",
) -> dict[str, dict[str, Any]]:
    cells = manifest.get("cells")
    if manifest.get("schemaVersion") != "1.0.0" or not isinstance(cells, list):
        raise ValueError("Invalid Playwright exact-cell manifest")

    expected = {
        item["requirementId"]: item
        for item in requirement_catalog
        if not item["methods"][0]["human"] and item["methods"][0]["probe"] == "project-playwright"
    }
    observed: dict[str, list[dict[str, Any]]] = {requirement_id: [] for requirement_id in expected}
    seen: set[tuple[str, str, str]] = set()
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("project") != project:
            continue
        requirement_id = cell.get("requirementId")
        test_id = cell.get("testId")
        key = (project, str(requirement_id), str(test_id))
        if key in seen:
            raise ValueError(f"duplicate exact Playwright cell: {requirement_id} / {test_id}")
        seen.add(key)
        requirement = expected.get(str(requirement_id))
        if requirement is None:
            raise ValueError(f"unknown exact Playwright requirement cell: {requirement_id}")
        assignment = requirement["methods"][0]
        if (
            cell.get("journeyId") != requirement["journeyIds"][0]
            or cell.get("method") != assignment["method"]
            or cell.get("probe") != assignment["probe"]
        ):
            raise ValueError(f"mismatched exact Playwright cell: {requirement_id}")
        if cell.get("status") not in {"PASS", "FAIL", "CANT_TELL"}:
            raise ValueError(f"invalid exact Playwright status: {requirement_id}")
        observed[str(requirement_id)].append(cell)

    results: dict[str, dict[str, Any]] = {}
    for requirement_id, matching in observed.items():
        statuses = {cell["status"] for cell in matching}
        if "FAIL" in statuses:
            status = "FAIL"
        elif matching and statuses == {"PASS"}:
            status = "PASS"
        else:
            status = "CANT_TELL"
        results[requirement_id] = {
            "status": status,
            "observed": (
                f"Exact {project} cell reported {status} from "
                + ", ".join(sorted(str(cell["testId"]) for cell in matching))
                if matching
                else f"Required exact {project} cell was absent"
            ),
        }
    return results


def _docusaurus_catalogs(
    asset_ledger: AssetJourneyLedger,
    requirement_ledger: RequirementEvidenceLedger,
    cadence: Literal["pull-request", "scheduled", "release"],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    catalog_ids = {f"DCS{number:02d}" for number in range(1, 14)}
    selected_ids = {f"DCS{number:02d}" for number in range(1, 14 if cadence == "release" else 13)}
    journeys = [item for item in asset_ledger.journeys if item.journey_id in catalog_ids]
    if {item.journey_id for item in journeys} != catalog_ids:
        raise ValueError("Canonical asset ledger is missing required Docusaurus journeys")

    asset_catalog = {
        "schemaVersion": "1.0.0",
        "assets": [{"assetId": "ASSET-DOCUSAURUS"}],
        "fixtures": [{"fixtureId": "FIXTURE-DOCUSAURUS"}],
        "journeys": [
            {
                "journeyId": item.journey_id,
                "assetId": item.asset_id,
                "fixtureId": item.fixture_id,
                "state": item.state,
                "role": item.role,
                "requiredMethods": [method.value for method in item.required_methods],
            }
            for item in journeys
        ],
    }

    requirements: list[dict[str, Any]] = []
    for requirement in requirement_ledger.requirements:
        for journey_id in sorted(set(requirement.journey_ids) & catalog_ids):
            journey = next(item for item in journeys if item.journey_id == journey_id)
            required_methods = {item.value for item in journey.required_methods}
            for assignment in requirement.methods:
                method = assignment.method.value
                guarded_review = (
                    journey_id == "DCS13"
                    and requirement.requirement_id in _DOCUSAURUS_GUARDED_REVIEW_REQUIREMENTS
                )
                if method not in required_methods and not guarded_review:
                    continue
                if guarded_review:
                    probe = "qualified-human"
                    human = True
                elif method in _DOCUSAURUS_AUTOMATED_PROBES:
                    probe = _DOCUSAURUS_AUTOMATED_PROBES[method]
                    human = False
                elif method in _DOCUSAURUS_HUMAN_METHODS:
                    probe = "qualified-human"
                    human = True
                else:
                    continue
                requirements.append(
                    {
                        "requirementId": f"{requirement.requirement_id}:{journey_id}:{method}",
                        "sourceRequirementId": requirement.requirement_id,
                        "criterionId": requirement.criterion,
                        "framework": "wcag-22",
                        "journeyIds": [journey_id],
                        "freshnessRule": requirement.freshness_rule,
                        "methods": [
                            {
                                "method": method,
                                "probe": probe,
                                "disposition": assignment.disposition.value.lower(),
                                "proposition": assignment.proposition,
                                "human": human,
                            }
                        ],
                    }
                )
    if not requirements:
        raise ValueError("Docusaurus composer catalog contains no requirement mappings")

    requirement_catalog = {"schemaVersion": "1.0.0", "requirements": requirements}
    selected_requirements = [
        item
        for item in requirements
        if item["journeyIds"][0] in selected_ids and (cadence == "release" or not item["methods"][0]["human"])
    ]
    methods = sorted({item["methods"][0]["method"] for item in selected_requirements})
    probes = sorted({item["methods"][0]["probe"] for item in selected_requirements})
    scope = {
        "schemaVersion": "1.0.0",
        "profileId": f"docusaurus-{cadence}",
        "cadenceClass": cadence,
        "manualEvidencePolicy": "required" if cadence == "release" else "defer",
        "selectors": {
            "assetIds": ["ASSET-DOCUSAURUS"],
            "journeyIds": sorted(selected_ids),
            "states": sorted({item.state for item in journeys}),
            "methods": methods,
            "probes": probes,
            "fixtureIds": ["FIXTURE-DOCUSAURUS"],
        },
    }
    return asset_catalog, requirement_catalog, scope


def prepare_docusaurus_composition(
    repository_root: Path,
    output_dir: Path,
    playwright_report: Path,
    harness_root: Path,
    source_revision: str,
    observed_at: str,
    cadence: Literal["pull-request", "scheduled", "release"],
    source_check_status: Literal["passed", "failed", "unknown"],
) -> dict[str, str]:
    root = repository_root.resolve()
    output = output_dir.resolve()
    if not output.is_relative_to(root):
        raise ValueError("Docusaurus composition output must remain inside the repository")
    _git_commit(source_revision)
    timestamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Docusaurus composition time must include a UTC offset")

    asset_ledger = AssetJourneyLedger.model_validate_json(
        (root / ".github" / "accessibility" / "asset-journeys.json").read_text(encoding="utf-8")
    )
    requirement_ledger = RequirementEvidenceLedger.model_validate_json(
        (root / ".github" / "accessibility" / "requirement-evidence.json").read_text(encoding="utf-8")
    )
    asset_catalog, requirement_catalog, scope = _docusaurus_catalogs(asset_ledger, requirement_ledger, cadence)

    docs_root = root / "docs" / "docusaurus"
    source_check_path = output / "source-check-result.json"
    _write_json(
        source_check_path,
        {
            "observedAt": observed_at,
            "producer": "physical-ai-toolchain Docusaurus source and unit gates",
            "status": source_check_status,
        },
    )
    report_path = playwright_report.resolve()
    playwright_artifact_paths = {
        "docusaurus-playwright": report_path,
        "docusaurus-method-results": report_path.parent / "evidence-method-results.json",
        "docusaurus-browser": report_path.parent / "browser-version.json",
        "docusaurus-site-crawl": report_path.parent / "site-crawl-results.json",
        "docusaurus-contrast": report_path.parent / "contrast-ledger.json",
        "docusaurus-routes": docs_root / "build" / "deployed-routes.json",
        "docusaurus-mermaid": docs_root / "build" / "mermaid-routes.json",
        "docusaurus-source-inputs": root / "artifacts" / "accessibility" / "docusaurus" / "source-input-manifest.json",
    }
    playwright_artifacts = [
        _artifact_record(path, root, artifact_id)
        for artifact_id, path in sorted(playwright_artifact_paths.items())
    ]
    source_artifacts = [_artifact_record(source_check_path, root, "docusaurus-source-checks")]
    playwright_artifact_ids = [item["artifactId"] for item in playwright_artifacts]
    source_artifact_ids = [item["artifactId"] for item in source_artifacts]
    method_manifest = json.loads(
        playwright_artifact_paths["docusaurus-method-results"].read_text(encoding="utf-8")
    )
    exact_results = _docusaurus_exact_results(method_manifest, requirement_catalog["requirements"])
    browser = json.loads(playwright_artifact_paths["docusaurus-browser"].read_text(encoding="utf-8"))

    harness_files = [
        path
        for path in harness_root.rglob("*")
        if path.is_file() and not {"node_modules", ".venv", "__pycache__"} & set(path.parts)
    ]
    build_files = [path for path in (docs_root / "build").rglob("*") if path.is_file()]
    config_files = [docs_root / "playwright.config.ts", docs_root / "package.json"]
    tool_files = [*sorted((docs_root / "e2e").glob("*.ts")), *sorted((docs_root / "e2e").glob("*.mjs"))]
    mapping_files = [
        root / ".github" / "accessibility" / "asset-journeys.json",
        root / ".github" / "accessibility" / "requirement-evidence.json",
    ]
    fixture_files = [
        playwright_artifact_paths["docusaurus-routes"],
        playwright_artifact_paths["docusaurus-mermaid"],
    ]
    run_id = f"docusaurus-{cadence}-{source_revision[:12]}-{timestamp.strftime('%Y%m%dT%H%M%SZ')}"
    run_context = {
        "schemaVersion": "1.0.0",
        "runId": run_id,
        "campaignId": "docusaurus-accessibility",
        "composedAt": observed_at,
        "sourceRevision": source_revision,
        "buildDigest": _digest_paths(build_files, root),
        "configDigest": _digest_paths(config_files, root),
        "fixtureDigest": _digest_paths(fixture_files, root),
        "lockfileDigest": _digest_paths([docs_root / "package-lock.json"], root),
        "toolDigest": _digest_paths(tool_files, root),
        "harnessDigest": _digest_paths(harness_files, harness_root.resolve()),
        "mappingDigest": _digest_paths(mapping_files, root),
        "environment": {
            "class": "local-ci",
            "operatingSystem": platform.system(),
            "browser": f"{browser['channel']} {browser['version']}",
            "locale": "en-US",
            "viewport": "1280x720",
            "inputModes": ["keyboard", "pointer"],
        },
    }

    journeys = {item["journeyId"]: item for item in asset_catalog["journeys"]}
    playwright_requirements = [
        requirement
        for requirement in requirement_catalog["requirements"]
        if not requirement["methods"][0]["human"]
        and requirement["methods"][0]["probe"] == "project-playwright"
    ]
    automated_journeys = sorted({requirement["journeyIds"][0] for requirement in playwright_requirements})
    state_proofs = []
    for journey_id in automated_journeys:
        journey_results = [
            exact_results[requirement["requirementId"]]
            for requirement in playwright_requirements
            if requirement["journeyIds"][0] == journey_id
        ]
        statuses = {result["status"] for result in journey_results}
        if "FAIL" in statuses:
            status = "FAIL"
        elif statuses == {"PASS"}:
            status = "PASS"
        else:
            status = "CANT_TELL"
        observed = f"Exact Playwright cells for {journey_id} reported {status}"
        state_proofs.append(
            {
                "schemaVersion": "1.0.0",
                "proofId": f"proof-{run_id}-{journey_id}",
                "runId": run_id,
                "journeyId": journey_id,
                "fixtureId": journeys[journey_id]["fixtureId"],
                "state": journeys[journey_id]["state"],
                "status": "PROVED" if status in {"PASS", "FAIL"} else "UNRESOLVED",
                "expected": "Required Docusaurus checks execute against the local production build",
                "observed": observed,
                "steps": [{"action": "navigate"}, {"action": "assert"}],
                "artifactIds": playwright_artifact_ids,
                **({"errors": [observed]} if status == "CANT_TELL" else {}),
            }
        )

    playwright_results = []
    source_results = []
    for requirement in requirement_catalog["requirements"]:
        assignment = requirement["methods"][0]
        if assignment["human"]:
            continue
        journey_id = requirement["journeyIds"][0]
        result = {
            "requirementId": requirement["requirementId"],
            "journeyId": journey_id,
            "state": journeys[journey_id]["state"],
            "method": assignment["method"],
            "probe": assignment["probe"],
            "disposition": assignment["disposition"],
            "expected": assignment["proposition"],
        }
        if assignment["method"] in _DOCUSAURUS_SOURCE_METHODS:
            status = "PASS" if source_check_status == "passed" else "CANT_TELL"
            observed = f"Docusaurus source and unit gates reported {source_check_status}"
            source_results.append(
                {
                    **result,
                    "resultId": f"result-source-{run_id}-{len(source_results) + 1}",
                    "status": status,
                    "observed": observed,
                    "artifactIds": source_artifact_ids,
                    **({"limitation": observed} if status == "CANT_TELL" else {}),
                }
            )
        else:
            exact_result = exact_results[requirement["requirementId"]]
            status = exact_result["status"]
            observed = exact_result["observed"]
            playwright_results.append(
                {
                    **result,
                    "resultId": f"result-playwright-{run_id}-{len(playwright_results) + 1}",
                    "status": status,
                    "observed": observed,
                    "stateProofId": f"proof-{run_id}-{journey_id}",
                    "artifactIds": playwright_artifact_ids,
                    **({"limitation": observed} if status == "CANT_TELL" else {}),
                }
            )

    binding = {
        key: run_context[key] for key in ("sourceRevision", "buildDigest", "configDigest", "fixtureDigest")
    }
    playwright_source = {
        "schemaVersion": "1.0.0",
        "sourceKind": "playwright",
        "producer": "physical-ai-toolchain Docusaurus tests",
        "sourceRunId": f"source-playwright-{run_id}",
        "observedAt": observed_at,
        "sourceDigest": "",
        "boundTo": binding,
        "results": playwright_results,
        "artifacts": playwright_artifacts,
    }
    playwright_source["sourceDigest"] = _hve_canonical_digest(
        {key: value for key, value in playwright_source.items() if key != "sourceDigest"},
        "hve-a11y:evidence-source:v1",
    )
    source = {
        "schemaVersion": "1.0.0",
        "sourceKind": "source",
        "producer": "physical-ai-toolchain Docusaurus source and unit gates",
        "sourceRunId": f"source-checks-{run_id}",
        "observedAt": observed_at,
        "sourceDigest": "",
        "boundTo": binding,
        "results": source_results,
        "artifacts": source_artifacts,
    }
    source["sourceDigest"] = _hve_canonical_digest(
        {key: value for key, value in source.items() if key != "sourceDigest"},
        "hve-a11y:evidence-source:v1",
    )

    documents = {
        "assetCatalog": ("asset-journeys.json", asset_catalog),
        "requirementCatalog": ("requirement-methods.json", requirement_catalog),
        "scope": ("evidence-scope.json", scope),
        "runContext": ("run-context.json", run_context),
        "stateProofs": ("state-proofs.json", state_proofs),
        "source": ("evidence-source.json", source),
        "playwrightSource": ("evidence-playwright.json", playwright_source),
    }
    paths: dict[str, str] = {}
    for label, (name, document) in documents.items():
        path = output / name
        _write_json(path, document)
        paths[label] = path.relative_to(root).as_posix()
    return paths


def validate_composed_docusaurus_bundle(path: Path, repository_root: Path) -> dict[str, Any]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if bundle.get("schemaVersion") != "1.0.0":
        raise ValueError("Composed bundle must use schemaVersion 1.0.0")
    if (bundle.get("nonAttestation") or {}).get("attestation") is not False:
        raise ValueError("Composed bundle must remain non-attesting")
    requirements = ((bundle.get("catalogs") or {}).get("requirementMethod") or {}).get("requirements") or []
    source_requirements = {item.get("sourceRequirementId") for item in requirements}
    requirement_ledger = RequirementEvidenceLedger.model_validate_json(
        (repository_root / ".github" / "accessibility" / "requirement-evidence.json").read_text(encoding="utf-8")
    )
    expected_requirements = {
        item.requirement_id for item in requirement_ledger.requirements if item.framework is Framework.WCAG_2_2
    }
    if source_requirements != expected_requirements:
        raise ValueError("Composed bundle does not retain all 55 WCAG 2.2 A/AA source requirements")
    asset_catalog = (bundle.get("catalogs") or {}).get("assetJourney") or {}
    journeys = {item.get("journeyId") for item in asset_catalog.get("journeys", [])}
    required = {f"DCS{number:02d}" for number in range(1, 13)}
    if not required <= journeys:
        raise ValueError("Composed bundle is missing deterministic DCS01-DCS12 journeys")
    expected_cells = {
        item["cellId"]: item for item in bundle.get("expectedCells", []) if item.get("disposition") == "decides"
    }
    results = {item.get("cellId"): item.get("status") for item in bundle.get("evidenceResults", [])}
    statuses = [results.get(cell_id, "NOT_ASSESSED") for cell_id in expected_cells]
    if "FAIL" in statuses:
        verdict = EvidenceStatus.FAIL
    elif any(status not in {"PASS", "INAPPLICABLE"} for status in statuses):
        verdict = EvidenceStatus.CANT_TELL
    else:
        verdict = EvidenceStatus.PASS
    return {
        "attestation": False,
        "bundleDigest": bundle.get("bundleDigest"),
        "docusaurusJourneys": len(journeys),
        "sourceRequirements": len(source_requirements),
        "scopeCompleteness": bundle.get("scopeCompleteness"),
        "verdict": verdict.value,
    }


def prepare_docusaurus_contrast_review(
    repository_root: Path,
    baseline_path: Path,
    crawl_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Build a deterministic contrast-family review manifest without approving entries."""
    root = repository_root.resolve()
    baseline_file = baseline_path.resolve()
    crawl_file = crawl_path.resolve()
    output_file = output_path.resolve()
    for path in (baseline_file, crawl_file, output_file):
        if not path.is_relative_to(root):
            raise ValueError("Contrast review paths must remain inside the repository")

    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    if baseline.get("schemaVersion") != 1 or not isinstance(baseline.get("entries"), list):
        raise ValueError("Contrast baseline must use schemaVersion 1 with entries")
    crawl = ContrastCrawlInput.model_validate_json(crawl_file.read_text(encoding="utf-8"))
    measurements = [item for route in crawl.results for item in route.contrast_evidence]
    by_signature: dict[str, list[ContrastMeasurementInput]] = {}
    for measurement in measurements:
        by_signature.setdefault(measurement.signature, []).append(measurement)

    baseline_entries = baseline["entries"]
    baseline_signatures = [str(entry.get("signature", "")) for entry in baseline_entries]
    duplicates = _duplicates(baseline_signatures)
    if duplicates:
        raise ValueError(f"Contrast baseline contains duplicate signatures: {duplicates}")
    missing = sorted(set(baseline_signatures) - set(by_signature))
    unknown = sorted(set(by_signature) - set(baseline_signatures))
    if missing or unknown:
        raise ValueError(f"Contrast evidence closure failed; missing={missing}, unknown={unknown}")

    def canonical_digest(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    def signature_status(items: list[ContrastMeasurementInput]) -> str:
        statuses = {item.method_status for item in items}
        if "computed-ratio-failure-candidate" in statuses:
            return "computed-ratio-failure-candidate"
        if "insufficient-evidence" in statuses:
            return "insufficient-evidence"
        if "qualified-review-required" in statuses or len(statuses) > 1:
            return "qualified-review-required"
        return "computed-ratio-pass-candidate"

    family_members: dict[str, list[dict[str, Any]]] = {}
    signature_records = []
    crop_root = crawl_file.parent.resolve()
    for entry in sorted(baseline_entries, key=lambda item: str(item["signature"])):
        signature = str(entry["signature"])
        evidence_items = sorted(
            by_signature[signature],
            key=lambda item: (item.route, item.theme, item.state, item.tuple_digest),
        )
        rendered_evidence = []
        for item in evidence_items:
            if item.evidence_path is None:
                raise ValueError(f"Contrast signature lacks a retained crop: {signature}")
            crop_path = (crop_root / PurePosixPath(item.evidence_path)).resolve()
            if not crop_path.is_relative_to(crop_root) or not crop_path.is_file():
                raise ValueError(f"Contrast crop is missing or outside the crawl root: {item.evidence_path}")
            rendered_evidence.append(
                {
                    **item.model_dump(mode="json", by_alias=True),
                    "evidenceDigest": hashlib.sha256(crop_path.read_bytes()).hexdigest(),
                }
            )

        family_content = {
            "html": entry["html"],
            "reason": entry["reason"],
            "target": entry["target"],
        }
        family_id = f"contrast-family-{canonical_digest(family_content)[:16]}"
        status = signature_status(evidence_items)
        record = {
            "count": entry["count"],
            "evidence": rendered_evidence,
            "familyId": family_id,
            "html": entry["html"],
            "methodStatus": status,
            "reason": entry["reason"],
            "requiredReviewerFields": {
                "classification": None,
                "evidence": None,
                "owner": None,
                "rationale": None,
                "reviewBy": None,
                "reviewedOn": None,
            },
            "routeFamilies": sorted(entry["routeFamilies"]),
            "signature": signature,
            "state": entry["state"],
            "target": entry["target"],
            "theme": entry["theme"],
        }
        signature_records.append(record)
        family_members.setdefault(family_id, []).append(record)

    families = []
    for family_id, members in sorted(family_members.items()):
        member_statuses = [
            {item["methodStatus"] for item in member["evidence"]} for member in members
        ]
        member_tuple_digests = [
            {item["tupleDigest"] for item in member["evidence"]} for member in members
        ]
        stable_statuses = {next(iter(statuses)) for statuses in member_statuses if len(statuses) == 1}
        stable_tuple_digests = {
            next(iter(tuple_digests)) for tuple_digests in member_tuple_digests if len(tuple_digests) == 1
        }
        can_share = (
            all(len(statuses) == 1 for statuses in member_statuses)
            and all(len(tuple_digests) == 1 for tuple_digests in member_tuple_digests)
            and len(stable_statuses) == 1
            and len(stable_tuple_digests) == 1
        )
        shared_conclusion = next(iter(stable_statuses)) if can_share else None
        families.append(
            {
                "familyId": family_id,
                "html": members[0]["html"],
                "memberSignatures": sorted(member["signature"] for member in members),
                "reason": members[0]["reason"],
                "sharedConclusion": shared_conclusion,
                "target": members[0]["target"],
            }
        )

    status_counts = Counter(record["methodStatus"] for record in signature_records)
    payload = {
        "baselineDigest": hashlib.sha256(baseline_file.read_bytes()).hexdigest(),
        "crawlDigest": hashlib.sha256(crawl_file.read_bytes()).hexdigest(),
        "families": families,
        "policy": (
            "Candidate evidence only. A qualified reviewer must supply ownership, deciding evidence, "
            "and review expiry before any baseline entry becomes reviewed."
        ),
        "schemaVersion": "1.0.0",
        "signatures": signature_records,
        "totals": {
            "families": len(families),
            "signatures": len(signature_records),
            "statuses": dict(sorted(status_counts.items())),
        },
    }
    _write_json(output_file, payload)
    return payload


def prepare_docusaurus_reviewer_handoff(
    repository_root: Path,
    release_bundle_path: Path,
    contrast_review_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Write reviewer-owned work templates without manufacturing qualified results."""
    root = repository_root.resolve()
    release_file = release_bundle_path.resolve()
    contrast_file = contrast_review_path.resolve()
    output = output_dir.resolve()
    for path in (release_file, contrast_file, output):
        if not path.is_relative_to(root):
            raise ValueError("Reviewer handoff paths must remain inside the repository")

    bundle = json.loads(release_file.read_text(encoding="utf-8"))
    contrast = json.loads(contrast_file.read_text(encoding="utf-8"))
    if bundle.get("schemaVersion") != "1.0.0" or contrast.get("schemaVersion") != "1.0.0":
        raise ValueError("Reviewer handoff inputs must use schemaVersion 1.0.0")
    if (bundle.get("scopeCompleteness") or {}).get("reviewerEvidence") != "pending":
        raise ValueError("Reviewer handoff requires pending reviewer evidence")
    run_manifest = bundle.get("runManifest") or {}
    required_run_fields = ("buildDigest", "campaignId", "configDigest", "fixtureDigest", "sourceRevision")
    if any(not run_manifest.get(field) for field in required_run_fields):
        raise ValueError("Reviewer handoff requires complete release run identity")
    binding_path = root / "docs" / "docusaurus" / "a11y-screen-reader.bindings.json"
    if not binding_path.is_file():
        raise ValueError("Reviewer handoff requires the Docusaurus screen-reader binding")
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    execution_recipes = []
    for case_id, case_binding in sorted((binding.get("caseBindings") or {}).items()):
        for execution in case_binding.get("executions") or []:
            execution_recipes.append(
                {
                    "assertions": execution.get("assertions") or [],
                    "caseId": case_id,
                    "commands": execution.get("commands") or [],
                    "executionId": execution["id"],
                    "route": execution["route"],
                    "state": execution["state"],
                    "targetRefs": case_binding.get("targetRefs") or [],
                    "title": execution["title"],
                    "trigger": execution.get("trigger"),
                    "triggerSequence": execution.get("triggerSequence") or [],
                }
            )
    if len(execution_recipes) != 6:
        raise ValueError(
            f"Reviewer handoff requires exactly 6 screen-reader recipes; received {len(execution_recipes)}"
        )

    journeys = {
        item["journeyId"]: item
        for item in ((bundle.get("catalogs") or {}).get("assetJourney") or {}).get("journeys", [])
    }
    human_cells = sorted(
        (
            item
            for item in bundle.get("expectedCells", [])
            if item.get("human") is True and item.get("probe") == "qualified-human"
        ),
        key=lambda item: item["cellId"],
    )
    if len(human_cells) != 54:
        raise ValueError(f"Reviewer handoff requires exactly 54 human cells; received {len(human_cells)}")
    expected_methods = {
        "COGNITIVE_REVIEW": 25,
        "JAWS": 4,
        "MANUAL_KEYBOARD": 1,
        "MEDIA_EQUIVALENCE_REVIEW": 14,
        "NVDA": 4,
        "PLAYWRIGHT_KEYBOARD": 3,
        "PLAYWRIGHT_POINTER": 1,
        "UNIT_TEST": 2,
    }
    method_counts = Counter(item["method"] for item in human_cells)
    if dict(sorted(method_counts.items())) != expected_methods:
        raise ValueError(f"Reviewer handoff method inventory drifted: {dict(sorted(method_counts.items()))}")

    output.mkdir(parents=True, exist_ok=True)
    template_root = output / "supplement-templates"
    template_root.mkdir(parents=True, exist_ok=True)
    release_digest = hashlib.sha256(release_file.read_bytes()).hexdigest()
    contrast_digest = hashlib.sha256(contrast_file.read_bytes()).hexdigest()
    templates = []
    for cell in human_cells:
        journey = journeys.get(cell["journeyId"])
        if journey is None:
            raise ValueError(f"Reviewer handoff cell has unknown journey: {cell['journeyId']}")
        template = {
            "artifactDigests": [release_digest, contrast_digest],
            "buildDigest": run_manifest["buildDigest"],
            "campaignId": run_manifest["campaignId"],
            "cellId": cell["cellId"],
            "configDigest": run_manifest["configDigest"],
            "expected": cell["expected"],
            "fixtureId": journey["fixtureId"],
            "journeyId": cell["journeyId"],
            "method": cell["method"],
            "privacyBoundary": {
                "personalDataPresent": None,
                "redacted": True,
                "restrictedObservationsStoredInGit": False,
            },
            "qualifiedResultSchema": "qualified-human-result.schema.json",
            "requirementId": cell["requirementId"],
            "requiredReviewerFields": {
                "approvalRecordDigest": None,
                "approvalRecordId": None,
                "digest": None,
                "observedAt": None,
                "observedSummary": None,
                "qualificationRecordDigest": None,
                "qualificationRecordId": None,
                "reviewerId": None,
                "status": None,
                "supplementId": None,
                "validUntil": None,
            },
            "schemaVersion": "1.0.0-template",
            "sourceRevision": run_manifest["sourceRevision"],
            "state": cell["state"],
            "templateStatus": "awaiting-qualified-review",
        }
        template_path = template_root / f"{cell['cellId']}.json"
        _write_json(template_path, template)
        templates.append(
            {
                "path": template_path.relative_to(output).as_posix(),
                "sha256": hashlib.sha256(template_path.read_bytes()).hexdigest(),
            }
        )

    payload = {
        "artifacts": {
            "contrastReview": {
                "path": contrast_file.relative_to(root).as_posix(),
                "sha256": contrast_digest,
            },
            "releaseBundle": {
                "path": release_file.relative_to(root).as_posix(),
                "sha256": release_digest,
            },
        },
        "blockers": [
            "Qualified reviewer identity, qualification, approval, observations, status, and validity are required.",
            "An independently controlled reviewer-registry digest is required.",
            "A JAWS-capable environment is required for the four JAWS cells.",
        ],
        "campaignId": run_manifest["campaignId"],
        "contrast": contrast["totals"],
        "assistiveTechnologyRunbook": {
            "bindingDigest": hashlib.sha256(binding_path.read_bytes()).hexdigest(),
            "executionRecipes": execution_recipes,
            "methods": ["NVDA", "JAWS"],
            "policy": (
                "Execute the functional task with native NVDA and human-led JAWS. Product wording is an "
                "expected outcome, not a required verbatim speech transcript."
            ),
        },
        "methodCounts": dict(sorted(method_counts.items())),
        "policy": (
            "Preparation only. Templates are not qualified-human results and cannot support release completeness "
            "until an authorized reviewer supplies every required field and the HVE schema validates each supplement."
        ),
        "recomposition": {
            "priorBundleDigest": bundle.get("bundleDigest"),
            "requiredInputs": [
                "prior bundle",
                "independently retained prior bundle digest",
                "completed qualified-human supplements",
                "review registry",
                "independently controlled expected registry digest",
            ],
        },
        "schemaVersion": "1.0.0",
        "templates": templates,
        "totals": {"humanCells": len(human_cells), "templates": len(templates)},
    }
    _write_json(output / "reviewer-campaign.json", payload)
    return payload


def validate_presentation_taxonomy(repository_root: Path) -> dict[str, Any]:
    root = repository_root.resolve()
    signals: list[str] = []
    for relative_path in (
        "slides",
        "docs/slides",
        "docs/docusaurus/static/slides",
        "docs/docusaurus/src/pages/slides",
    ):
        if (root / relative_path).exists():
            signals.append(relative_path)

    config_path = root / "docs" / "docusaurus" / "docusaurus.config.js"
    if config_path.is_file():
        config = config_path.read_text(encoding="utf-8")
        for token in ("loadSlideBundles", "slideDecks"):
            if token in config:
                signals.append(f"docusaurus.config.js:{token}")

    manifest_path = root / "docs" / "docusaurus" / "build" / "deployed-routes.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for route in manifest.get("routes", []):
            if re.search(r"(?:^|/)(?:slides?|decks?|presentations?)(?:/|$)", route, re.IGNORECASE):
                signals.append(f"route:{route}")

    if not signals:
        return {"signals": [], "status": "inactive"}

    missing: list[str] = []
    binding_path = root / "docs" / "docusaurus" / "a11y-screen-reader.bindings.json"
    if not binding_path.is_file():
        missing.append("presentation binding")
    else:
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        profiles = (binding.get("product") or {}).get("surfaceProfiles") or []
        if "presentation" not in profiles or not binding.get("bundleDiscovery"):
            missing.append("presentation profile and bundle discovery")

    presentation_test = root / "docs" / "docusaurus" / "e2e" / "slides.spec.ts"
    if not presentation_test.is_file():
        missing.append("implementation-specific presentation tests")
    if missing:
        raise ValueError(
            "Presentation taxonomy is active but accessibility coverage is incomplete: "
            + ", ".join(missing)
            + f"; signals: {', '.join(sorted(signals))}"
        )
    return {"signals": sorted(signals), "status": "active"}


def prepare_docusaurus_validation_input(
    repository_root: Path,
    output_path: Path,
    source_revision: str,
    validation_outcomes: list[str],
) -> dict[str, Any]:
    root = repository_root.resolve()
    output = output_path.resolve()
    if not output.is_relative_to(root):
        raise ValueError("Validation input output must remain inside the repository")
    _git_commit(source_revision)

    outcomes: dict[str, str] = {}
    for value in validation_outcomes:
        command_id, separator, outcome = value.partition("=")
        if not separator or command_id not in _DOCUSAURUS_VALIDATION_COMMANDS:
            raise ValueError(f"Unknown Docusaurus validation outcome: {value}")
        if outcome not in {"success", "failure", "cancelled", "skipped"}:
            raise ValueError(f"Unsupported GitHub step outcome for {command_id}: {outcome}")
        if command_id in outcomes:
            raise ValueError(f"Duplicate Docusaurus validation outcome: {command_id}")
        outcomes[command_id] = outcome
    missing = sorted(set(_DOCUSAURUS_VALIDATION_COMMANDS) - set(outcomes))
    if missing:
        raise ValueError(f"Missing Docusaurus validation outcomes: {missing}")

    artifact_root = output.parent
    result_root = artifact_root / "command-results"
    result_root.mkdir(parents=True, exist_ok=True)

    def file_digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    commands = []
    result_paths = []
    for command_id, command in _DOCUSAURUS_VALIDATION_COMMANDS.items():
        outcome = outcomes[command_id]
        status = "passed" if outcome == "success" else "failed" if outcome in {"failure", "cancelled"} else "skipped"
        result = {"commandId": command_id, "jobOutcome": outcome, "status": status}
        result_path = result_root / f"{command_id}.json"
        _write_json(result_path, result)
        result_paths.append(result_path)
        record = {
            "commandId": command_id,
            "command": command,
            "workingDirectory": ".",
            "status": status,
        }
        if status == "skipped":
            record["reason"] = f"GitHub step outcome was {outcome}"
        else:
            record["resultArtifactDigest"] = file_digest(result_path)
        commands.append(record)

    def inventory(paths: list[Path]) -> list[dict[str, str]]:
        return [
            {"path": path.relative_to(root).as_posix(), "sha256": file_digest(path)}
            for path in sorted(paths)
            if path.is_file()
        ]

    generated_paths = [
        artifact_root / "contrast-review.json",
        artifact_root / "evidence-bundle.json",
        artifact_root / "screen-reader-method-cells.json",
    ]
    if len(inventory(generated_paths)) != len(generated_paths):
        raise ValueError("Docusaurus validation input requires the evidence bundle and screen-reader method cells")

    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    node_version = subprocess.run(
        ["node", "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    uv_version = subprocess.run(
        ["uv", "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    payload = {
        "revision": {
            "sourceRevision": source_revision,
            "diffDigest": hashlib.sha256(diff).hexdigest(),
            "tracked": not bool(status.strip()),
        },
        "environment": {
            "operatingSystem": platform.platform(),
            "toolVersions": {
                "node": node_version,
                "python": platform.python_version(),
                "uv": uv_version,
            },
        },
        "commands": commands,
        "generatedInventory": inventory(generated_paths),
        "untrackedDeliverables": inventory(
            [
                *result_paths,
                artifact_root / "evidence-summary.json",
                artifact_root / "presentation-taxonomy.json",
                artifact_root / "reviewer-handoff" / "reviewer-campaign.json",
                *(artifact_root / "reviewer-handoff" / "supplement-templates").glob("*.json"),
            ]
        ),
        "assistiveTechnologySample": {
            "advisory": True,
            "journeys": list(_DOCUSAURUS_SCREEN_READER_CASES),
            "boundary": (
                "Local bindings prepare advisory evidence; qualified NVDA with Edge and human-led JAWS "
                "remain release authority."
            ),
        },
    }
    _write_json(output, payload)
    return payload


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as error:
        raise ValueError("PyYAML is required for GitHub surface validation") from error

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"GitHub source must contain a mapping: {path}")
    return data


def validate_github_surfaces(repository_root: Path) -> dict[str, int | str]:
    """Validate repository-authored GitHub intake and dispatch contracts."""
    issue_root = repository_root / ".github" / "ISSUE_TEMPLATE"
    workflow_root = repository_root / ".github" / "workflows"
    issue_forms = {
        "01-bug-report.yml",
        "02-feature-request.yml",
        "03-infrastructure-issue.yml",
        "04-training-issue.yml",
        "05-documentation.yml",
    }
    actual_forms = {path.name for path in issue_root.glob("*.yml") if path.name != "config.yml"}
    if actual_forms != issue_forms:
        raise ValueError(f"Issue form inventory mismatch: expected {sorted(issue_forms)}, found {sorted(actual_forms)}")

    for form_name in sorted(issue_forms):
        form = _load_yaml(issue_root / form_name)
        for field in ("name", "description", "title", "labels", "body"):
            if not form.get(field):
                raise ValueError(f"Issue form {form_name} is missing {field}")
        body = form["body"]
        if not isinstance(body, list) or not body:
            raise ValueError(f"Issue form {form_name} has an empty body")

        field_ids: list[str] = []
        required_count = 0
        for item in body:
            if not isinstance(item, dict) or not item.get("type"):
                raise ValueError(f"Issue form {form_name} contains an invalid body item")
            if item["type"] == "markdown":
                if not (item.get("attributes") or {}).get("value"):
                    raise ValueError(f"Issue form {form_name} contains empty Markdown guidance")
                continue

            field_id = item.get("id")
            attributes = item.get("attributes") or {}
            if not field_id or not attributes.get("label"):
                raise ValueError(f"Issue form {form_name} contains an unlabeled field")
            _stable_id(field_id)
            field_ids.append(field_id)
            if item["type"] == "dropdown" and not attributes.get("options"):
                raise ValueError(f"Issue form {form_name} dropdown {field_id} has no options")
            validations = item.get("validations") or {}
            if not isinstance(validations.get("required", False), bool):
                raise ValueError(f"Issue form {form_name} field {field_id} has an invalid required state")
            required_count += int(validations.get("required", False))

        if duplicates := _duplicates(field_ids):
            raise ValueError(f"Issue form {form_name} has duplicate field IDs: {duplicates}")
        if required_count == 0:
            raise ValueError(f"Issue form {form_name} has no required fields")

    infrastructure_text = (issue_root / "03-infrastructure-issue.yml").read_text(encoding="utf-8").lower()
    if "sanitized any secrets" not in infrastructure_text:
        raise ValueError("Infrastructure issue form lacks required secret-sanitization acknowledgment")

    markdown_template = issue_root / "00-general.md"
    markdown_text = markdown_template.read_text(encoding="utf-8")
    for expected in ("## Summary", "## Category", "## Description", "## Environment", "- [ ]"):
        if expected not in markdown_text:
            raise ValueError(f"General issue template is missing {expected}")

    chooser = _load_yaml(issue_root / "config.yml")
    if chooser.get("blank_issues_enabled") is not False:
        raise ValueError("Blank GitHub issues must remain disabled")
    contact_links = chooser.get("contact_links")
    if not isinstance(contact_links, list) or len(contact_links) != 2:
        raise ValueError("Issue chooser must contain exactly two contact links")
    for link in contact_links:
        if not isinstance(link, dict) or not all(link.get(field) for field in ("name", "url", "about")):
            raise ValueError("Issue chooser contact links require name, URL, and purpose")
        if not str(link["url"]).startswith("https://"):
            raise ValueError("Issue chooser contact links must use HTTPS")

    excluded_workflows = {"deploy-docs.yml"}
    zero_input_dispatches: list[str] = []
    typed_dispatches: list[tuple[str, dict[str, Any]]] = []
    for workflow_path in sorted(workflow_root.glob("*.yml")):
        workflow = _load_yaml(workflow_path)
        triggers = workflow.get("on", workflow.get(True))
        if not isinstance(triggers, dict) or "workflow_dispatch" not in triggers:
            continue
        if workflow_path.name in excluded_workflows:
            continue
        dispatch = triggers["workflow_dispatch"]
        inputs = dispatch.get("inputs") if isinstance(dispatch, dict) else None
        if inputs:
            if not isinstance(inputs, dict):
                raise ValueError(f"Workflow {workflow_path.name} has invalid dispatch inputs")
            typed_dispatches.append((workflow_path.name, inputs))
        else:
            zero_input_dispatches.append(workflow_path.name)

    if len(zero_input_dispatches) != 9:
        raise ValueError(f"Expected 9 zero-input dispatch workflows, found {zero_input_dispatches}")
    typed_by_name = dict(typed_dispatches)
    if set(typed_by_name) != {"accessibility-evidence.yml", "sha-staleness-check.yml"}:
        raise ValueError(f"Expected accessibility and SHA typed dispatches, found {typed_dispatches}")
    sha_inputs = typed_by_name["sha-staleness-check.yml"]
    if set(sha_inputs) != {"max-age-days"}:
        raise ValueError("SHA staleness dispatch must expose only max-age-days")
    threshold = sha_inputs["max-age-days"]
    if not isinstance(threshold, dict) or threshold.get("type") != "number" or threshold.get("default") != 30:
        raise ValueError("SHA staleness max-age-days must be numeric with default 30")
    accessibility_inputs = typed_by_name["accessibility-evidence.yml"]
    if set(accessibility_inputs) != {"evidence-cadence"}:
        raise ValueError("Accessibility evidence dispatch must expose only evidence-cadence")

    return {
        "issueForms": len(issue_forms),
        "markdownTemplates": 1,
        "contactLinks": len(contact_links),
        "zeroInputDispatches": len(zero_input_dispatches),
        "typedDispatches": len(typed_dispatches),
        "providerOutcomes": EvidenceStatus.NOT_ASSESSED.value,
    }


def load_bundle(path: Path) -> EvidenceBundle:
    """Load and validate an evidence bundle from JSON."""
    return EvidenceBundle.model_validate_json(path.read_text(encoding="utf-8"))


def create_parser() -> argparse.ArgumentParser:
    """Create the evidence gate command parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", nargs="?", type=Path, help="Evidence bundle JSON")
    parser.add_argument("--config-preview", action="store_true", help="Print the no-mutation contract summary")
    parser.add_argument("--validate-github", action="store_true", help="Validate GitHub intake and dispatch source")
    parser.add_argument(
        "--prepare-docusaurus-composition",
        type=Path,
        help="Write HVE composer inputs for current Docusaurus evidence",
    )
    parser.add_argument(
        "--prepare-docusaurus-source-manifest",
        type=Path,
        help="Write a deterministic pre-build Docusaurus source-input manifest",
    )
    parser.add_argument(
        "--validate-composed-docusaurus-bundle",
        type=Path,
        help="Validate a composed HVE Docusaurus evidence bundle",
    )
    parser.add_argument(
        "--validate-presentation-taxonomy",
        action="store_true",
        help="Fail when slide taxonomy activates without presentation coverage",
    )
    parser.add_argument(
        "--prepare-docusaurus-contrast-review",
        type=Path,
        help="Write a deterministic contrast-family reviewer manifest",
    )
    parser.add_argument("--contrast-baseline", type=Path, help="Reviewed Docusaurus contrast baseline")
    parser.add_argument("--site-crawl-results", type=Path, help="Docusaurus route crawl with rendered contrast data")
    parser.add_argument(
        "--prepare-docusaurus-reviewer-handoff",
        type=Path,
        help="Write privacy-minimized qualified-review templates",
    )
    parser.add_argument("--release-bundle", type=Path, help="Composed Docusaurus release evidence bundle")
    parser.add_argument("--contrast-review", type=Path, help="Generated Docusaurus contrast review manifest")
    parser.add_argument(
        "--prepare-docusaurus-validation-input",
        type=Path,
        help="Write deterministic HVE validation-manifest input",
    )
    parser.add_argument(
        "--validation-outcome",
        action="append",
        default=[],
        help="GitHub step outcome as command-id=outcome",
    )
    parser.add_argument("--playwright-report", type=Path, help="Current Docusaurus Playwright JSON report")
    parser.add_argument("--harness-root", type=Path, help="Reviewed HVE accessibility skill root")
    parser.add_argument("--source-revision", help="Exact repository commit bound to generated evidence")
    parser.add_argument("--observed-at", help="Timezone-aware composition timestamp")
    parser.add_argument(
        "--source-check-status",
        choices=("passed", "failed", "unknown"),
        help="Combined result of Docusaurus source, unit, and build gates",
    )
    parser.add_argument(
        "--cadence",
        choices=("pull-request", "scheduled", "release"),
        default="pull-request",
        help="Evidence scope cadence",
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd(), help="Root for artifact verification")
    parser.add_argument("--output-format", choices=("json", "text"), default="json", help="Summary output format")
    return parser


def run(arguments: argparse.Namespace) -> int:
    """Run contract preview or evidence validation."""
    if arguments.config_preview:
        preview = {
            "frameworks": [item.value for item in Framework],
            "methods": [item.value for item in EvidenceMethod],
            "mutation": "none",
            "schemaVersion": "1.0.0",
            "statuses": [item.value for item in EvidenceStatus],
        }
        print(json.dumps(preview, indent=2, sort_keys=True))
        return EXIT_SUCCESS
    if arguments.validate_github:
        print(json.dumps(validate_github_surfaces(arguments.repository_root), indent=2, sort_keys=True))
        return EXIT_SUCCESS
    if arguments.prepare_docusaurus_source_manifest:
        payload = prepare_docusaurus_source_manifest(
            arguments.repository_root,
            arguments.prepare_docusaurus_source_manifest,
            arguments.cadence,
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_SUCCESS
    if arguments.prepare_docusaurus_composition:
        required = {
            "--playwright-report": arguments.playwright_report,
            "--harness-root": arguments.harness_root,
            "--source-revision": arguments.source_revision,
            "--observed-at": arguments.observed_at,
            "--source-check-status": arguments.source_check_status,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError("Docusaurus composition requires " + ", ".join(missing))
        paths = prepare_docusaurus_composition(
            arguments.repository_root,
            arguments.prepare_docusaurus_composition,
            arguments.playwright_report,
            arguments.harness_root,
            arguments.source_revision,
            arguments.observed_at,
            arguments.cadence,
            arguments.source_check_status,
        )
        print(json.dumps(paths, indent=2, sort_keys=True))
        return EXIT_SUCCESS
    if arguments.validate_composed_docusaurus_bundle:
        summary = validate_composed_docusaurus_bundle(
            arguments.validate_composed_docusaurus_bundle,
            arguments.repository_root,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return EXIT_SUCCESS if summary["verdict"] == EvidenceStatus.PASS.value else EXIT_FAILURE
    if arguments.validate_presentation_taxonomy:
        print(json.dumps(validate_presentation_taxonomy(arguments.repository_root), indent=2, sort_keys=True))
        return EXIT_SUCCESS
    if arguments.prepare_docusaurus_contrast_review:
        if arguments.contrast_baseline is None or arguments.site_crawl_results is None:
            raise ValueError(
                "Docusaurus contrast review requires --contrast-baseline and --site-crawl-results"
            )
        payload = prepare_docusaurus_contrast_review(
            arguments.repository_root,
            arguments.contrast_baseline,
            arguments.site_crawl_results,
            arguments.prepare_docusaurus_contrast_review,
        )
        print(json.dumps(payload["totals"], indent=2, sort_keys=True))
        return EXIT_SUCCESS
    if arguments.prepare_docusaurus_reviewer_handoff:
        if arguments.release_bundle is None or arguments.contrast_review is None:
            raise ValueError("Docusaurus reviewer handoff requires --release-bundle and --contrast-review")
        payload = prepare_docusaurus_reviewer_handoff(
            arguments.repository_root,
            arguments.release_bundle,
            arguments.contrast_review,
            arguments.prepare_docusaurus_reviewer_handoff,
        )
        print(json.dumps(payload["totals"], indent=2, sort_keys=True))
        return EXIT_SUCCESS
    if arguments.prepare_docusaurus_validation_input:
        if arguments.source_revision is None:
            raise ValueError("Docusaurus validation input requires --source-revision")
        payload = prepare_docusaurus_validation_input(
            arguments.repository_root,
            arguments.prepare_docusaurus_validation_input,
            arguments.source_revision,
            arguments.validation_outcome,
        )
        summary = {
            "commands": len(payload["commands"]),
            "output": str(arguments.prepare_docusaurus_validation_input),
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return EXIT_SUCCESS
    if arguments.bundle is None:
        raise ValueError("bundle is required unless a validation-only option is used")

    bundle = load_bundle(arguments.bundle)
    verify_bundle_integrity(bundle, arguments.repository_root)
    summary = summarize_evidence(bundle)
    if arguments.output_format == "text":
        print(format_human_summary(summary))
    else:
        print(summary.model_dump_json(indent=2))
    return EXIT_SUCCESS if summary.verdict in {EvidenceStatus.PASS, EvidenceStatus.INAPPLICABLE} else EXIT_FAILURE


def main(argv: list[str] | None = None) -> int:
    """Validate accessibility evidence and return a stable exit code."""
    try:
        return run(create_parser().parse_args(argv))
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as error:
        print(f"Accessibility evidence validation failed: {error}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
