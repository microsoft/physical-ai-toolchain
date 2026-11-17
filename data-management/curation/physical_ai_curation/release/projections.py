"""Materialize portable annotation and label projections from a frozen release input."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from physical_ai_curation.models import DatasetLabelsFile
from physical_ai_curation.release.canonical import canonical_json_bytes
from physical_ai_curation.release.media import ReleasePlanError, SubsetEpisodeInput
from physical_ai_curation.release.models import FrozenInput

ANNOTATION_KIND = "annotation"
LABELS_KIND = "labels"
LABELS_PATH = "meta/episode_labels.json"
PROJECTION_SCHEMA_VERSION = "1.0.0"
PRIVATE_KEYS = frozenset({"principal_scope_id", "source_id", "source_revision", "endpoint", "api_key", "token"})


def annotation_path(output_index: int) -> str:
    """Return the release path for one output episode's annotations."""
    return f"annotations/episodes/episode_{output_index:06d}.json"


def materialize_saved_projections(
    frozen: FrozenInput, episodes: Sequence[SubsetEpisodeInput], *, release_dataset_id: str
) -> dict[str, bytes]:
    """Return canonical annotation and label files keyed by release path, in output episode order."""
    if tuple(episode.episode_id for episode in episodes) != frozen.selected_episode_ids:
        raise ReleasePlanError("projection episodes must match the frozen selection order")
    resources = _resources_by_kind(frozen)
    files: dict[str, bytes] = {}
    for output_index, episode in enumerate(episodes):
        key = _episode_key(episode.episode_id)
        saved = resources[ANNOTATION_KIND].get(key)
        if saved:
            entries = [entry for _, value in sorted(saved) for entry in _annotation_entries(value, episode)]
            payload = {
                "schema_version": PROJECTION_SCHEMA_VERSION,
                "dataset_id": release_dataset_id,
                "output_episode_index": output_index,
                "source_episode_id": episode.episode_id,
                "annotations": entries,
            }
            files[annotation_path(output_index)] = canonical_json_bytes(payload)
    labels = _labels_projection(frozen, episodes, resources[LABELS_KIND], release_dataset_id)
    if labels is not None:
        files[LABELS_PATH] = canonical_json_bytes(labels)
    return files


def _episode_key(episode_id: str) -> str:
    return episode_id.rsplit("/", 1)[-1]


def _resources_by_kind(frozen: FrozenInput) -> dict[str, dict[str, list[tuple[str, Any]]]]:
    grouped: dict[str, dict[str, list[tuple[str, Any]]]] = {
        ANNOTATION_KIND: defaultdict(list),
        LABELS_KIND: defaultdict(list),
    }
    for resource in frozen.saved_resources:
        kind, _, rest = resource.resource_id.partition("/")
        if kind in grouped:
            grouped[kind][rest.split("/", 1)[0]].append((resource.resource_id, json.loads(resource.value_json)))
    return grouped


def _annotation_entries(value: Any, episode: SubsetEpisodeInput) -> list[dict[str, Any]]:
    _reject_private(value)
    if not isinstance(value, dict) or not isinstance(value.get("annotations"), list):
        raise ReleasePlanError(f"annotation value for {episode.episode_id} must contain an annotations list")
    mapping = _output_frames(episode)
    return [_remap_entry(entry, episode, mapping) for entry in value["annotations"]]


def _remap_entry(entry: Any, episode: SubsetEpisodeInput, mapping: dict[int, int]) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ReleasePlanError(f"annotation entry for {episode.episode_id} must be an object")
    remapped = dict(entry)
    if episode.removed_frames:
        source_claims = {
            key: remapped.pop(key) for key in ("task_completeness", "trajectory_quality") if key in remapped
        }
        if source_claims:
            remapped["source_claims"] = source_claims
    anomalies = entry.get("anomalies")
    if isinstance(anomalies, dict) and isinstance(anomalies.get("anomalies"), list):
        remapped["anomalies"] = {
            **anomalies,
            "anomalies": [
                fragment for item in anomalies["anomalies"] for fragment in _remap_anomaly(item, episode, mapping)
            ],
        }
    quality = entry.get("data_quality")
    if isinstance(quality, dict) and isinstance(quality.get("issues"), list):
        remapped["data_quality"] = {
            "issues": [
                fragment
                for issue in quality["issues"]
                for fragment in _remap_span_item(issue, "affected_frames", episode, mapping)
            ]
        }
        if "overall_quality" in quality:
            if episode.removed_frames:
                remapped.setdefault("source_claims", {})["overall_quality"] = quality["overall_quality"]
            else:
                remapped["data_quality"]["overall_quality"] = quality["overall_quality"]
    detections = entry.get("object_detections")
    if isinstance(detections, list):
        remapped["object_detections"] = [
            projected for item in detections if (projected := _remap_detection(item, episode, mapping)) is not None
        ]
    return remapped


def _remap_anomaly(item: Any, episode: SubsetEpisodeInput, mapping: dict[int, int]) -> list[Any]:
    return _remap_span_item(item, "frame_range", episode, mapping)


def _remap_span_item(item: Any, field: str, episode: SubsetEpisodeInput, mapping: dict[int, int]) -> list[Any]:
    if isinstance(item, dict) and item.get(field) is not None:
        return [{**item, field: span} for span in _span(item[field], episode, mapping)]
    return [item]


def _remap_detection(item: Any, episode: SubsetEpisodeInput, mapping: dict[int, int]) -> Any:
    if isinstance(item, dict) and "frame_index" in item:
        index = item["frame_index"]
        if not _is_frame(index) or index >= episode.length:
            raise ReleasePlanError(f"frame index {index!r} is outside {episode.episode_id}")
        if index not in mapping:
            return None
        return {**item, "frame_index": mapping[index]}
    return item


def _span(value: Any, episode: SubsetEpisodeInput, mapping: dict[int, int]) -> list[list[int]]:
    if not (isinstance(value, list) and len(value) == 2 and all(_is_frame(item) for item in value)):
        raise ReleasePlanError(f"frame range must be two integers for {episode.episode_id}")
    start, end = value
    if start > end:
        raise ReleasePlanError(f"frame range {value} is inverted for {episode.episode_id}")
    if end >= episode.length:
        raise ReleasePlanError(f"frame range {value} exceeds {episode.episode_id} length {episode.length}")
    spans = []
    previous = None
    for frame in range(start, end + 1):
        if frame not in mapping:
            continue
        if previous is None or frame != previous + 1:
            spans.append([mapping[frame], mapping[frame]])
        else:
            spans[-1][1] = mapping[frame]
        previous = frame
    return spans


def _output_frames(episode: SubsetEpisodeInput) -> dict[int, int]:
    kept = [frame for frame in range(episode.length) if frame not in episode.removed_frames]
    return {source: output for output, source in enumerate(kept)}


def _is_frame(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _reject_private(value: Any) -> None:
    if isinstance(value, dict):
        if PRIVATE_KEYS.intersection(value):
            raise ReleasePlanError("annotation projection contains a private principal field")
        for item in value.values():
            _reject_private(item)
    elif isinstance(value, list):
        for item in value:
            _reject_private(item)


def _portable_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    """Drop machine source revisions, which are private to the source, from a provenance ledger."""
    contributions = ledger.get("contributions")
    if not isinstance(contributions, list):
        return ledger
    portable = []
    for item in contributions:
        machine = item.get("machine") if isinstance(item, dict) else None
        if isinstance(machine, dict):
            item = {**item, "machine": {key: val for key, val in machine.items() if key != "source_revision"}}
        portable.append(item)
    return {**ledger, "contributions": portable}


def _portable_labels_source(value: Any) -> Any:
    """Return labels JSON with machine source revisions removed for the private-field check only."""
    if not isinstance(value, dict) or not isinstance(value.get("provenance"), dict):
        return value
    return {
        **value,
        "provenance": {
            key: _portable_ledger(ledger) if isinstance(ledger, dict) else ledger
            for key, ledger in value["provenance"].items()
        },
    }


def _labels_projection(
    frozen: FrozenInput,
    episodes: Sequence[SubsetEpisodeInput],
    saved: dict[str, list[tuple[str, Any]]],
    release_dataset_id: str,
) -> dict[str, Any] | None:
    if not saved:
        return None
    dataset_id = frozen.selected_episode_ids[0].rsplit("/", 1)[0]
    episode_labels: dict[str, list[str]] = {}
    analysis: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    available_labels = []
    for output_index, episode in enumerate(episodes):
        for _, value in saved.get(_episode_key(episode.episode_id), []):
            _reject_private(_portable_labels_source(value))
            source = DatasetLabelsFile.model_validate(value)
            available_labels.extend(label for label in source.available_labels if label not in available_labels)
            if source.dataset_id != dataset_id:
                raise ReleasePlanError(f"labels for {episode.episode_id} belong to another dataset")
            source_key = str(episode.original_episode_index)
            output_key = str(output_index)
            if source_key in source.episodes:
                episode_labels[output_key] = source.episodes[source_key]
            if source_key in source.analysis and not episode.removed_frames:
                analysis[output_key] = source.analysis[source_key].model_dump(mode="json")
            if source_key in source.provenance:
                provenance[output_key] = _portable_ledger(source.provenance[source_key].model_dump(mode="json"))
    labels = DatasetLabelsFile(
        dataset_id=release_dataset_id,
        available_labels=available_labels,
        episodes=episode_labels,
        analysis=analysis,
    )
    payload = labels.model_dump(mode="json")
    payload["provenance"] = provenance
    return payload
