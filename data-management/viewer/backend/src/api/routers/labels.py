"""Episode label API endpoints.

Provides CRUD endpoints for episode labels (multi-select text tags)
and managing the set of available label options per dataset.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from evaluation.vlm_judge.curation import DEFAULT_LABELS, DatasetLabelsFile, EpisodeAnalysisRecord
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import Field

from ..auth import PrincipalContext, require_principal_context
from ..csrf import require_csrf_token
from ..models.contributions import ContributionLedger
from ..services.dataset_service import DatasetService, get_dataset_service
from ..services.label_storage import (
    BlobLabelStorage as BlobLabelStorage,
)
from ..services.label_storage import (
    LabelStorage,
    _create_label_storage,
    _labels_path_for_base,
)
from ..services.label_storage import (
    LocalLabelStorage as LocalLabelStorage,
)
from ..storage import RevisionConflictError, VersionedValue
from ..validation import (
    SAFE_DATASET_ID_PATTERN,
    SanitizedModel,
    path_int_param,
    path_string_param,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

router = APIRouter()


class EpisodeLabels(SanitizedModel):
    """Labels assigned to a single episode."""

    episode_index: int
    labels: list[str] = Field(default_factory=list)


class BulkLabelUpdate(SanitizedModel):
    """Request body for updating labels on a single episode."""

    labels: list[str]
    intent: Literal["human-edit", "legacy-unknown"] = "legacy-unknown"


class AddLabelOption(SanitizedModel):
    """Request body for adding a new available label option."""

    label: str = Field(min_length=1, max_length=100)


# Analysis fields that make sensible categorical labels, mapped to their default
# label prefix. Free-text fields (movement_quality, notes, instruction) are
# intentionally excluded because they produce unbounded, unfilterable labels.
ANALYSIS_IMPORT_FIELDS: dict[str, str] = {
    "object": "OBJECT",
    "pick_from": "PICK",
    "grasp_success": "GRASP",
    "place_success": "PLACE",
    "motion_score": "MOTION",
    "motion_flags": "FLAG",
    "source": "SOURCE",
}


class ImportAnalysisRequest(SanitizedModel):
    """Request to promote an analysis field into filterable episode labels."""

    field: str = Field(min_length=1, max_length=64)
    prefix: str | None = Field(default=None, max_length=32)
    overwrite: bool = False


class ImportAnalysisResult(SanitizedModel):
    """Outcome of importing an analysis field into episode labels."""

    dataset_id: str
    available_labels: list[str]
    episodes: dict[str, list[str]]
    field: str
    prefix: str
    labels_added: list[str]
    episodes_updated: int


def _normalize_label(label: str) -> str:
    return label.strip().upper()


def _analysis_value_labels(prefix: str, value: object) -> list[str]:
    """Turn an analysis field value into zero or more normalized labels.

    Lists (e.g. motion_flags) yield one label per item; booleans render as
    yes/no; everything else is stringified. Empty values yield nothing.
    """
    items = value if isinstance(value, list) else [value]
    labels: list[str] = []
    for item in items:
        if item is None:
            continue
        text = ("yes" if item else "no") if isinstance(item, bool) else str(item).strip()
        if not text:
            continue
        labels.append(_normalize_label(f"{prefix}: {text}"))
    return labels


# ============================================================================
# Label Storage Backends
# ============================================================================


_label_storage: LabelStorage | None = None


def _get_label_storage() -> LabelStorage:
    """Get or create the global label storage singleton."""
    global _label_storage
    if _label_storage is None:
        from ..config import get_app_config

        config = get_app_config()
        blob_provider = None
        if config.storage_backend == "azure":
            from ..config import create_blob_dataset_provider

            blob_provider = create_blob_dataset_provider(config)
        _label_storage = _create_label_storage(config.storage_backend, blob_provider)
    return _label_storage


def _get_base_path() -> str:
    return os.environ.get("DATA_DIR", "./data")


def _labels_path(dataset_id: str) -> Path:
    return _labels_path_for_base(dataset_id, _get_base_path())


async def _load_labels(dataset_id: str) -> DatasetLabelsFile:
    return await _get_label_storage().load(dataset_id)


async def _load_labels_versioned(dataset_id: str) -> VersionedValue[DatasetLabelsFile]:
    return await _get_label_storage().load_versioned(dataset_id)


@dataclass(frozen=True)
class RevisionPrecondition:
    if_match: str | None
    if_none_match: bool


def require_revision_precondition(
    if_match: str | None = Header(default=None),
    if_none_match: str | None = Header(default=None),
) -> RevisionPrecondition:
    """Require exactly one strong revision precondition."""
    if if_match is None and if_none_match is None:
        raise HTTPException(status_code=428, detail="If-Match or If-None-Match is required")
    if if_match is not None and if_none_match is not None:
        raise HTTPException(status_code=400, detail="Specify only one revision precondition")
    if if_none_match is not None and if_none_match != "*":
        raise HTTPException(status_code=400, detail="If-None-Match must be '*'")
    return RevisionPrecondition(if_match=if_match, if_none_match=if_none_match == "*")


async def _load_labels_for_update(
    dataset_id: str,
    precondition: RevisionPrecondition,
) -> DatasetLabelsFile:
    versioned = await _load_labels_versioned(dataset_id)
    if precondition.if_none_match and versioned.etag is not None:
        raise RevisionConflictError(versioned.etag)
    if precondition.if_match is not None and precondition.if_match != versioned.etag:
        raise RevisionConflictError(versioned.etag)
    return versioned.value or DatasetLabelsFile(dataset_id=dataset_id)


async def _save_labels(
    dataset_id: str,
    labels_file: DatasetLabelsFile,
    precondition: RevisionPrecondition,
) -> str:
    return await _get_label_storage().save(
        dataset_id,
        labels_file,
        if_match=precondition.if_match,
        if_none_match=precondition.if_none_match,
    )


@router.get("/{dataset_id}/labels")
async def get_dataset_labels(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
) -> DatasetLabelsFile:
    """Get all episode labels and available label options for a dataset."""
    versioned = await _load_labels_versioned(dataset_id)
    if versioned.etag:
        response.headers["ETag"] = versioned.etag
    return versioned.value or DatasetLabelsFile(dataset_id=dataset_id)


@router.get("/{dataset_id}/labels/options")
async def get_label_options(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
) -> list[str]:
    """Get the list of available label options for a dataset."""
    labels_file = await _load_labels(dataset_id)
    return labels_file.available_labels


@router.post("/{dataset_id}/labels/options", dependencies=[Depends(require_csrf_token)])
async def add_label_option(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    body: AddLabelOption = ...,
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
) -> list[str]:
    """Add a new label option to the available set."""
    labels_file = await _load_labels_for_update(dataset_id, precondition)
    normalized = _normalize_label(body.label)
    if not normalized:
        raise HTTPException(status_code=400, detail="Label cannot be empty")
    if normalized not in labels_file.available_labels:
        labels_file.available_labels.append(normalized)
    response.headers["ETag"] = await _save_labels(dataset_id, labels_file, precondition)
    return labels_file.available_labels


@router.delete(
    "/{dataset_id}/labels/options/{label}",
    dependencies=[Depends(require_csrf_token)],
)
async def delete_label_option(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    label: str = Depends(path_string_param("label", label="label")),
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
) -> list[str]:
    """Delete a label option and remove it from all episode assignments."""
    labels_file = await _load_labels_for_update(dataset_id, precondition)
    normalized = _normalize_label(label)

    if not normalized:
        raise HTTPException(status_code=400, detail="Label cannot be empty")

    if normalized in DEFAULT_LABELS:
        raise HTTPException(status_code=400, detail="Built-in labels cannot be deleted")

    labels_file.available_labels = [existing for existing in labels_file.available_labels if existing != normalized]

    labels_file.episodes = {
        episode_idx: [existing for existing in labels if existing != normalized]
        for episode_idx, labels in labels_file.episodes.items()
    }
    for ledger in labels_file.provenance.values():
        ledger.withdraw([item.id for item in ledger.contributions if item.field == f"labels/{normalized}"])

    response.headers["ETag"] = await _save_labels(dataset_id, labels_file, precondition)
    return labels_file.available_labels


@router.get("/{dataset_id}/episodes/{episode_idx}/labels")
async def get_episode_labels(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
) -> EpisodeLabels:
    """Get labels for a specific episode."""
    labels_file = await _load_labels(dataset_id)
    key = str(episode_idx)
    return EpisodeLabels(
        episode_index=episode_idx,
        labels=labels_file.episodes.get(key, []),
    )


@router.put(
    "/{dataset_id}/episodes/{episode_idx}/labels",
    dependencies=[Depends(require_csrf_token)],
)
async def set_episode_labels(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
    body: BulkLabelUpdate = ...,
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
    dataset_service: DatasetService = Depends(get_dataset_service),
    principal: PrincipalContext = Depends(require_principal_context),
) -> EpisodeLabels:
    """Set labels for a specific episode (replaces existing labels)."""
    labels_file = await _load_labels_for_update(dataset_id, precondition)
    key = str(episode_idx)

    try:
        labels_file.apply_labels(
            int(episode_idx),
            body.labels,
            author_id=principal.scope_id,
            origin="human" if body.intent == "human-edit" else "legacy-unknown",
        )
    except ValueError:
        logger.warning("Label contribution ownership requires explicit resolution")
        raise HTTPException(status_code=409, detail="Label ownership requires explicit author selection") from None
    logger.debug(
        "Saving label contributions dataset=%s episode=%d intent=%s",
        dataset_id.replace("\r", "").replace("\n", ""),
        int(episode_idx),
        body.intent,
    )
    response.headers["ETag"] = await _save_labels(dataset_id, labels_file, precondition)
    dataset_service.invalidate_episode_cache(dataset_id, episode_idx)

    return EpisodeLabels(
        episode_index=episode_idx,
        labels=labels_file.episodes[key],
    )


@router.post("/{dataset_id}/labels/save", dependencies=[Depends(require_csrf_token)])
async def save_all_labels(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
) -> DatasetLabelsFile:
    """Persist all labels to disk (already persisted on each write, but
    this endpoint lets the frontend trigger an explicit save/confirmation)."""
    labels_file = await _load_labels_for_update(dataset_id, precondition)
    response.headers["ETag"] = await _save_labels(dataset_id, labels_file, precondition)
    return labels_file


@router.post(
    "/{dataset_id}/labels/import-from-analysis",
    dependencies=[Depends(require_csrf_token)],
)
async def import_analysis_labels(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    body: ImportAnalysisRequest = ...,
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
    dataset_service: DatasetService = Depends(get_dataset_service),
    principal: PrincipalContext = Depends(require_principal_context),
) -> ImportAnalysisResult:
    """Promote a categorical analysis field into filterable/editable episode labels.

    Each episode with a value for ``field`` gains a namespaced label such as
    ``OBJECT: MARKER``. Labels merge into existing assignments and are added to
    the available options, so the operation is idempotent. When ``overwrite`` is
    set, stale labels sharing the prefix are cleared unless human-owned.
    """
    field = body.field.strip()
    if field not in ANALYSIS_IMPORT_FIELDS:
        allowed = ", ".join(sorted(ANALYSIS_IMPORT_FIELDS))
        raise HTTPException(status_code=400, detail=f"Field '{field}' is not importable. Allowed: {allowed}")

    prefix = _normalize_label(body.prefix) if body.prefix else ANALYSIS_IMPORT_FIELDS[field]
    namespace = f"{prefix}:"
    labels_file = await _load_labels_for_update(dataset_id, precondition)
    original_provenance = {key: ledger.model_dump() for key, ledger in labels_file.provenance.items()}

    generated_by_episode: dict[str, list[str]] = {}
    generated_options: list[str] = []
    source_by_episode: dict[str, str] = {}
    for key, record in labels_file.analysis.items():
        ledger = labels_file.provenance.get(key, ContributionLedger())
        try:
            evidence = ledger.resolve(
                f"analysis/{field}", human_author_id=principal.scope_id, legacy_value=getattr(record, field, None)
            )
            generated = _analysis_value_labels(prefix, evidence.value)
            if generated and not evidence.contribution_ids:
                ledger.record_changes(
                    {}, {f"analysis/{field}": evidence.value}, author_id=principal.scope_id, origin="legacy-unknown"
                )
                evidence = ledger.resolve(f"analysis/{field}", human_author_id=principal.scope_id)
            if generated:
                labels_file.provenance[key] = ledger
                source_by_episode[key] = evidence.contribution_ids[0]
        except ValueError:
            logger.warning("Analysis promotion requires explicit source ownership resolution")
            raise HTTPException(
                status_code=409, detail="Analysis ownership requires explicit author selection"
            ) from None
        generated_by_episode[key] = generated
        for label in generated:
            if label not in generated_options:
                generated_options.append(label)

    original_options = labels_file.available_labels.copy()
    if body.overwrite:
        next_options = [label for label in original_options if not label.startswith(namespace)]
    else:
        next_options = original_options.copy()
    for label in generated_options:
        if label not in next_options:
            next_options.append(label)
    labels_file.available_labels = next_options
    added_options = [label for label in generated_options if label not in original_options]

    updated: set[str] = set()
    episode_keys = set(labels_file.episodes) | set(generated_by_episode)
    for key in episode_keys:
        current = labels_file.episodes.get(key, [])
        ledger = labels_file.provenance.get(key, ContributionLedger())
        generated = generated_by_episode.get(key, [])
        if not current and not generated:
            continue
        try:
            removed = {
                label
                for label in current
                if body.overwrite
                and label.startswith(namespace)
                and label not in generated
                and ledger.resolve(f"labels/{label}", human_author_id=principal.scope_id, legacy_value=True).origin
                != "human"
            }
            ledger.withdraw(
                [
                    item.id
                    for item in ledger.contributions
                    if item.field in {f"labels/{label}" for label in removed} and item.origin != "human"
                ]
            )
            for label in generated:
                identity = ledger.derive(source_by_episode[key], f"labels/{label}", True)
                ledger.accept(identity, principal.scope_id)
            labels_file.episodes[key] = list(
                dict.fromkeys([*(label for label in current if label not in removed), *generated])
            )
            labels_file.materialize_episode(int(key), author_id=principal.scope_id)
        except ValueError:
            logger.warning("Label promotion requires explicit contribution ownership resolution")
            raise HTTPException(status_code=409, detail="Label ownership requires explicit author selection") from None
        for label in labels_file.episodes[key]:
            if label not in labels_file.available_labels:
                labels_file.available_labels.append(label)
        if labels_file.episodes[key] != current:
            updated.add(key)

    provenance_changed = original_provenance != {
        key: ledger.model_dump() for key, ledger in labels_file.provenance.items()
    }
    if updated or labels_file.available_labels != original_options or provenance_changed:
        response.headers["ETag"] = await _save_labels(dataset_id, labels_file, precondition)
        dataset_service.invalidate_episode_cache(dataset_id)
    elif precondition.if_match:
        response.headers["ETag"] = precondition.if_match

    logger.debug(
        "Promoted analysis contributions dataset=%s field=%s episodes=%d",
        dataset_id.replace("\r", "").replace("\n", ""),
        field,
        len(updated),
    )
    return ImportAnalysisResult(
        dataset_id=dataset_id,
        available_labels=labels_file.available_labels,
        episodes=labels_file.episodes,
        field=field,
        prefix=prefix,
        labels_added=added_options,
        episodes_updated=len(updated),
    )


@router.get("/{dataset_id}/episodes/{episode_idx}/analysis")
async def get_episode_analysis(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
) -> EpisodeAnalysisRecord | None:
    """Get the structured analysis record for a specific episode, if any."""
    labels_file = await _load_labels(dataset_id)
    return labels_file.analysis.get(str(episode_idx))


@router.put(
    "/{dataset_id}/episodes/{episode_idx}/analysis",
    dependencies=[Depends(require_csrf_token)],
)
async def set_episode_analysis(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
    body: EpisodeAnalysisRecord = ...,
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
    dataset_service: DatasetService = Depends(get_dataset_service),
    principal: PrincipalContext = Depends(require_principal_context),
    intent: Literal["human-edit", "legacy-unknown"] = Header(default="legacy-unknown", alias="X-Curation-Intent"),
) -> EpisodeAnalysisRecord:
    """Merge supplied analysis fields, preserving omitted fields and explicit clears."""
    labels_file = await _load_labels_for_update(dataset_id, precondition)
    key = str(episode_idx)
    try:
        labels_file.apply_analysis(
            int(episode_idx),
            body,
            author_id=principal.scope_id,
            origin="human" if intent == "human-edit" else "legacy-unknown",
        )
    except ValueError:
        logger.warning("Analysis contribution ownership requires explicit resolution")
        raise HTTPException(status_code=409, detail="Analysis ownership requires explicit author selection") from None
    logger.debug(
        "Saving analysis contributions dataset=%s episode=%d intent=%s",
        dataset_id.replace("\r", "").replace("\n", ""),
        int(episode_idx),
        intent,
    )
    response.headers["ETag"] = await _save_labels(dataset_id, labels_file, precondition)
    dataset_service.invalidate_episode_cache(dataset_id, episode_idx)
    return labels_file.analysis[key]
