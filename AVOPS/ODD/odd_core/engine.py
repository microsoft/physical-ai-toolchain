# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""ODD Coverage Analysis & Gap Variation Engine.

Consolidates ODD domain specifications, coverage computation, entropy-delta simulation solving,
candidate video selection, and summary reporting into a single unified module.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# =====================================================================
# 1. CONSTANTS & ALIASES
# =====================================================================

QA_SPEC_ATTRIBUTE_ALIASES: dict[str, str] = {
    "weatherType": "weather",
    "weather_type": "weather",
    "trafficLights": "roadSegmentTurnIndication",
    "traffic_lights": "roadSegmentTurnIndication",
    "any_vehicle_lane_change": "AgentLaneChange",
    "any_vehicle_cut_in": "AgentCutIn",
    "pedestrianDetails": "pedestrianDetails",
    "roadParameters": "roadParameters",
    "timeOfDay": "timeOfDay",
}

BASE_VIDEO_FILTERS: dict[str, dict[str, str]] = {
    "weather": {"weather": "sunny", "roadCondition": "dry", "visibility": "clear"},
    "weatherType": {"weatherType": "sunny", "roadCondition": "dry", "visibility": "clear"},
    "visibility": {"visibility": "clear", "weather": "sunny"},
    "roadCondition": {"roadCondition": "dry", "weather": "sunny"},
    "egoVehicleTrajectory": {"egoVehicleTrajectory": "cruising"},
    "egoVehicleDirection": {"egoVehicleDirection": "straight_ahead"},
    "otherElements": {"otherElements": "no"},
    "roadSegmentPassageway": {"roadSegmentPassageway": "no"},
}


# =====================================================================
# 2. STRING NORMALIZATION & DOMAIN SPECIFICATION MODELS
# =====================================================================


def normalize_attribute_string(value: Any) -> str:
    """Normalize attribute keys or values to lowercase snake_case alphanumeric strings."""
    text = str(value or "").strip().lower()
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s-]+", "_", text)
    return text.strip("_")


@dataclass(slots=True)
class AttributeSpec:
    """Specification for a single ODD attribute."""

    name: str
    source_keys: list[str] = field(default_factory=list)
    expected_values: list[str] = field(default_factory=list)
    weight: float = 1.0
    recommendation: str | None = None
    threshold: float | None = None

    def __post_init__(self) -> None:
        self.source_keys = [str(k).strip() for k in self.source_keys if str(k).strip()]
        self.expected_values = [str(v).strip() for v in self.expected_values if str(v).strip()]
        self.weight = max(0.0, float(self.weight))
        if self.threshold is not None:
            self.threshold = float(self.threshold)


@dataclass(slots=True)
class CategorySpec:
    """Specification for a logical ODD category (e.g., sceneDetails, roadDetails)."""

    name: str
    attributes: dict[str, AttributeSpec] = field(default_factory=dict)
    threshold: float | None = None

    def __post_init__(self) -> None:
        if self.threshold is not None:
            self.threshold = float(self.threshold)


@dataclass(slots=True)
class ODDCoverageConfig:
    """Configuration for the ODD coverage engine."""

    categories: dict[str, CategorySpec] = field(default_factory=dict)
    synonyms: dict[str, str] = field(default_factory=dict)
    completeness_weight: float = 0.3
    coverage_weight: float = 0.35
    entropy_weight: float = 0.35
    underrepresentation_fraction: float = 0.02
    underrepresentation_min_count: int = 2

    @classmethod
    def load(cls, source: str | os.PathLike | dict[str, Any]) -> ODDCoverageConfig:
        """Load configuration from a dictionary, JSON file path, or YAML file path."""
        if isinstance(source, (str, os.PathLike)):
            p = Path(source)
            if not p.exists():
                raise FileNotFoundError(f"ODD configuration file not found: {p}")
            text = p.read_text(encoding="utf-8")
            if p.suffix in (".yaml", ".yml"):
                import yaml

                source_dict = yaml.safe_load(text)
            else:
                source_dict = json.loads(text)
        elif isinstance(source, dict):
            source_dict = copy.deepcopy(source)
        else:
            raise TypeError("ODD config must be a dictionary or a path to a JSON/YAML file.")

        categories: dict[str, CategorySpec] = {}
        for category_name, category_data in source_dict.get("categories", {}).items():
            category_threshold = category_data.get("threshold")
            if category_threshold is not None:
                category_threshold = float(category_threshold)

            attributes: dict[str, AttributeSpec] = {}
            for attr_name, attr_data in category_data.items():
                if attr_name == "threshold":
                    continue
                attributes[attr_name] = AttributeSpec(
                    name=attr_name,
                    source_keys=[str(k).strip() for k in attr_data.get("source_keys", [])],
                    expected_values=[str(v).strip() for v in attr_data.get("expected_values", [])],
                    weight=float(attr_data.get("weight", 1.0)),
                    recommendation=attr_data.get("recommendation"),
                    threshold=float(attr_data["threshold"]) if attr_data.get("threshold") is not None else None,
                )
            categories[category_name] = CategorySpec(
                name=category_name, attributes=attributes, threshold=category_threshold
            )

        synonyms: dict[str, str] = {}
        for key, value in source_dict.get("synonyms", {}).items():
            normalized_key = normalize_attribute_string(key)
            synonyms[normalized_key] = str(value).strip()
            synonyms[str(key).strip().lower()] = str(value).strip()

        weights = source_dict.get("score_weights", {})
        underrep = source_dict.get("underrepresentation", {})

        return ODDCoverageConfig(
            categories=categories,
            synonyms=synonyms,
            completeness_weight=float(weights.get("completeness", 0.3)),
            coverage_weight=float(weights.get("coverage", 0.35)),
            entropy_weight=float(weights.get("entropy", 0.35)),
            underrepresentation_fraction=float(underrep.get("fraction", 0.02)),
            underrepresentation_min_count=int(underrep.get("min_count", 2)),
        )


@dataclass(slots=True)
class SceneRecord:
    """Normalized scene record structure representing an annotated scene."""

    scene_id: str
    video_path: str | None = None
    raw_annotation: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AttributeReport:
    """Calculated coverage report metrics for a specific ODD attribute."""

    attribute: str
    category: str
    filled: int
    total: int
    missing: int
    completeness: float
    coverage: float
    entropy: float
    odd_score: float
    threshold_coverage: float = 0.0
    distribution: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert report to JSON-serializable dictionary."""
        return {
            "filled": self.filled,
            "total": self.total,
            "missing": self.missing,
            "completeness": round(self.completeness, 4),
            "coverage": round(self.coverage, 4),
            "entropy": round(self.entropy, 4),
            "threshold_coverage": round(self.threshold_coverage, 4),
            "odd_score": round(self.odd_score, 4),
            "distribution": self.distribution,
        }


def _compute_odd_score(
    distribution: Counter,
    expected: Sequence[str],
    total: int,
    completeness_weight: float,
    coverage_weight: float,
    entropy_weight: float,
) -> float:
    filled = sum(distribution.values())
    completeness = float(filled / total) if total else 0.0

    support = len(expected) if expected else len(distribution)
    if support <= 0 or filled <= 0:
        coverage = 0.0
        entropy = 0.0
    else:
        balance_threshold = filled / support
        coverage = float(min(sum(min(count, balance_threshold) for count in distribution.values()) / filled, 1.0))

        entropy_value = 0.0
        for count in distribution.values():
            probability = count / filled
            if probability > 0:
                entropy_value -= probability * math.log10(probability)
        max_entropy = math.log10(support) if support > 1 else 0.0
        if max_entropy > 0:
            entropy = float(min(max(entropy_value / max_entropy, 0.0), 1.0))
        else:
            entropy = 1.0 if support == 1 else 0.0

    return completeness * completeness_weight + coverage * coverage_weight + entropy * entropy_weight


# =====================================================================
# 3. ODD COVERAGE ENGINE
# =====================================================================


class ODDCoverageEngine:
    """Core analytical engine for evaluating dataset coverage against an ODD specification."""

    def __init__(self, config: ODDCoverageConfig) -> None:
        self.config = config
        self.scenes: list[SceneRecord] = []

    def load_dataset(self, scene_records: Sequence[dict[str, Any]]) -> None:
        """Load and normalize scene records from annotation dictionaries."""
        self.scenes = []
        for record in scene_records:
            scene_id = record.get("scene_id") or record.get("scene_token")
            if not scene_id:
                raise ValueError("Each scene record must contain 'scene_id' or 'scene_token'.")
            self.scenes.append(
                SceneRecord(
                    scene_id=str(scene_id),
                    video_path=record.get("video_path"),
                    raw_annotation=record.get("text_annotation", {}),
                )
            )
        logger.info("Loaded %d scene records into ODDCoverageEngine.", len(self.scenes))

    def analyze(self) -> dict[str, Any]:
        """Runs metric analysis (completeness, entropy, coverage, overall score, and gaps).

        Returns:
            Dictionary containing:
                - 'overall_odd_score': float
                - 'category_scores': Dict[str, float]
                - 'attributes': Dict[str, Dict[str, Any]]
                - 'gaps': List[Dict[str, str]]
        """
        if not self.scenes:
            raise ValueError("Dataset is empty. Load scene records before calling analyze().")

        extracted_attributes = [self._extract_scene_attributes(scene) for scene in self.scenes]
        attribute_reports: dict[str, AttributeReport] = {}

        for category_name, category_spec in self.config.categories.items():
            for attribute_name, attribute_spec in category_spec.attributes.items():
                values = [attrs.get(attribute_name) for attrs in extracted_attributes]
                attribute_reports[attribute_name] = self._compute_attribute_report(
                    category_name=category_name,
                    attribute_spec=attribute_spec,
                    values=values,
                )

        category_totals: dict[str, list[float]] = {}
        for report in attribute_reports.values():
            category_totals.setdefault(report.category, []).append(report.odd_score)

        category_scores = {cat: float(sum(scores) / len(scores)) for cat, scores in category_totals.items()}
        overall_score = float(sum(category_scores.values()) / len(category_scores)) if category_scores else 0.0
        gaps = self._detect_gaps(attribute_reports)

        return {
            "overall_odd_score": round(overall_score, 4),
            "category_scores": {cat: round(score, 4) for cat, score in category_scores.items()},
            "attributes": {name: r.to_dict() for name, r in attribute_reports.items()},
            "gaps": gaps,
        }

    def _extract_scene_attributes(self, scene: SceneRecord) -> dict[str, str | None]:
        answers = self._flatten_answers(scene.raw_annotation)
        normalized_answers = {normalize_attribute_string(k): v for k, v in answers.items()}
        result: dict[str, str | None] = {}

        for category_spec in self.config.categories.values():
            for attribute_name, attribute_spec in category_spec.attributes.items():
                result[attribute_name] = self._extract_attribute_value(normalized_answers, attribute_spec)
        return result

    def _extract_attribute_value(self, answer_map: dict[str, str], spec: AttributeSpec) -> str | None:
        candidate: str | None = None
        keys = [normalize_attribute_string(k) for k in spec.source_keys]

        for path, value in answer_map.items():
            if any(key in path for key in keys):
                candidate = value
                break

        if candidate is None:
            name = normalize_attribute_string(spec.name)
            for path, value in answer_map.items():
                if name in path:
                    candidate = value
                    break

        if candidate is None:
            return None

        return self._canonicalize_value(normalize_attribute_string(candidate), spec.expected_values)

    def _canonicalize_value(self, value: str, expected: Sequence[str]) -> str | None:
        if value in self.config.synonyms:
            return self.config.synonyms[value]
        if value in expected:
            return value
        # If no match is found, return None
        for item in expected:
            clean = item.replace("_", " ")
            if value == clean:
                return item
        return None

    def _flatten_answers(self, annotation: Any, prefix: str | None = None) -> dict[str, str]:
        flattened: dict[str, str] = {}
        if isinstance(annotation, dict):
            if "answer" in annotation and isinstance(annotation["answer"], str):
                flattened[prefix or ""] = annotation["answer"]
                return flattened
            for key, value in annotation.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                flattened.update(self._flatten_answers(value, path))
        elif isinstance(annotation, list):
            for idx, value in enumerate(annotation):
                path = f"{prefix}.{idx}" if prefix else str(idx)
                flattened.update(self._flatten_answers(value, path))
        return flattened

    def _compute_attribute_report(
        self,
        category_name: str,
        attribute_spec: AttributeSpec,
        values: list[str | None],
    ) -> AttributeReport:
        total = len(values)
        filled_values = [v for v in values if v is not None and v != ""]
        filled = len(filled_values)
        missing = total - filled

        distribution = Counter(filled_values)

        coverage = self._compute_coverage(distribution, attribute_spec.expected_values)
        entropy = self._compute_entropy(distribution, attribute_spec.expected_values)
        threshold_cov = self._compute_threshold_coverage(distribution, attribute_spec.expected_values, filled)
        completeness = float(filled / total) if total else 0.0

        score = _compute_odd_score(
            distribution,
            attribute_spec.expected_values,
            total,
            self.config.completeness_weight,
            self.config.coverage_weight,
            self.config.entropy_weight,
        )

        full_dist = Counter({v: 0 for v in attribute_spec.expected_values})
        full_dist.update(filled_values)

        return AttributeReport(
            attribute=attribute_spec.name,
            category=category_name,
            filled=filled,
            total=total,
            missing=missing,
            completeness=completeness,
            coverage=coverage,
            entropy=entropy,
            threshold_coverage=threshold_cov,
            odd_score=score,
            distribution={k: int(v) for k, v in sorted(full_dist.items(), key=lambda x: (-x[1], x[0]))},
        )

    def _compute_coverage(self, distribution: Counter, expected: Sequence[str]) -> float:
        """Compute coverage score using the dynamic threshold average logic."""
        filled_count = sum(distribution.values())
        K = len(expected) if expected else len(distribution)
        if K <= 0 or filled_count <= 0:
            return 0.0
        threshold = filled_count / K
        components = [min(count, threshold) for count in distribution.values()]
        cov_score_val = sum(components) / filled_count
        return float(min(cov_score_val, 1.0))

    def _compute_entropy(self, distribution: Counter, expected: Sequence[str]) -> float:
        """Compute normalized Shannon entropy over the distribution with respect to support K."""
        filled_count = sum(distribution.values())
        K = len(expected) if expected else len(distribution)
        if K <= 0 or filled_count <= 0:
            return 0.0
        if len(distribution) <= 1 and K > 1:
            return 0.0

        entropy = 0.0
        for count in distribution.values():
            p = count / filled_count
            if p > 0:
                entropy -= p * math.log10(p)

        max_entropy = math.log10(K) if K > 1 else 0.0
        if max_entropy > 0:
            return float(min(max(entropy / max_entropy, 0.0), 1.0))
        elif K == 1:
            return 1.0
        return 0.0

    def _compute_threshold_coverage(self, distribution: Counter, expected: Sequence[str], filled: int) -> float:
        return self._compute_coverage(distribution, expected)

    def _detect_gaps(self, reports: dict[str, AttributeReport]) -> list[dict[str, str]]:
        gaps: list[dict[str, str]] = []
        underrep_threshold = max(
            self.config.underrepresentation_min_count,
            math.ceil(len(self.scenes) * self.config.underrepresentation_fraction),
        )

        for category_spec in self.config.categories.values():
            for attribute_name, attribute_spec in category_spec.attributes.items():
                report = reports.get(attribute_name)
                if not report:
                    continue
                missing_vals = [v for v in attribute_spec.expected_values if report.distribution.get(v, 0) == 0]
                underrep_vals = [
                    v for v in attribute_spec.expected_values if 0 < report.distribution.get(v, 0) < underrep_threshold
                ]

                rec_template = attribute_spec.recommendation or "Generate additional {attribute} scenarios for {value}."
                if missing_vals:
                    val_text = ", ".join(missing_vals)
                    gaps.append(
                        {
                            "odd_gap": attribute_name,
                            "description": (
                                f"{val_text.replace('_', ' ').capitalize()} "
                                f"{attribute_name.replace('_', ' ')} conditions are not represented."
                            ),
                            "variation_required": rec_template.format(value=val_text, attribute=attribute_name),
                        }
                    )
                if underrep_vals:
                    val_text = ", ".join(underrep_vals)
                    gaps.append(
                        {
                            "odd_gap": attribute_name,
                            "description": (
                                f"{val_text.replace('_', ' ').capitalize()} "
                                f"{attribute_name.replace('_', ' ')} conditions are underrepresented."
                            ),
                            "variation_required": rec_template.format(value=val_text, attribute=attribute_name),
                        }
                    )

        return gaps


# =====================================================================
# 4. GAP-FILLING SIMULATION SOLVER & VIDEO SELECTION
# =====================================================================


def calculate_needed_variations(
    distribution: dict[str, int],
    expected_values: list[str],
    target_score: float,
    *,
    total_count: int,
    completeness_weight: float = 0.3,
    coverage_weight: float = 0.35,
    entropy_weight: float = 0.35,
) -> dict[str, int]:
    """Simulate balanced additions until the composite ODD score meets the target."""
    if not expected_values:
        return {}

    dist = Counter({val: int(count) for val, count in distribution.items()})
    dist.update({val: 0 for val in expected_values})
    filled = sum(dist.values())
    dataset_size = total_count
    if dataset_size < filled:
        raise ValueError("total_count cannot be smaller than the filled distribution count.")

    def get_score(sim_dist: Counter, added_count: int) -> float:
        return _compute_odd_score(
            sim_dist,
            expected_values,
            dataset_size + added_count,
            completeness_weight,
            coverage_weight,
            entropy_weight,
        )

    if get_score(dist, 0) >= target_score - 1e-12:
        return {}

    missing_count = dataset_size - filled
    max_score = completeness_weight + coverage_weight + entropy_weight
    if target_score > max_score:
        return {}

    max_target_count = max(dist.values(), default=0)
    non_completeness_score = coverage_weight + entropy_weight
    if completeness_weight > 0:
        # Bound equalization by the completeness required if coverage and entropy reach 1.
        required_completeness = (target_score - non_completeness_score) / completeness_weight
        if required_completeness >= 1.0 and missing_count > 0:
            return {}
        if 0.0 < required_completeness < 1.0:
            additional_count = math.ceil(
                max(
                    0.0,
                    (required_completeness * dataset_size - filled) / (1.0 - required_completeness),
                )
            )
            max_target_count += additional_count

    needed: dict[str, int] = {}
    for target_count in range(1, max_target_count + 1):
        sim_dist = Counter(dist)
        temp_needed: dict[str, int] = {}
        for val in expected_values:
            curr_count = dist.get(val, 0)
            if curr_count < target_count:
                sim_dist[val] = target_count
                temp_needed[val] = target_count - curr_count

        added_count = sum(temp_needed.values())
        if get_score(sim_dist, added_count) >= target_score - 1e-12:
            needed = temp_needed
            break

    return needed


def select_base_videos_for_attribute(
    records: list[dict[str, Any]],
    scene_attribute_maps: dict[str, dict[str, str]],
    attribute_name: str,
    required_count: int = -1,
    custom_filters: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Filters available records to identify suitable base videos matching filter constraints."""
    if required_count == 0 or not records:
        return []

    filters = custom_filters if custom_filters is not None else BASE_VIDEO_FILTERS
    req_conditions = filters.get(attribute_name, {})

    suitable: list[dict[str, Any]] = []
    for r in records:
        scene_id = r["scene_id"]
        attrs = scene_attribute_maps.get(scene_id, {})

        match = True
        for filter_attr, expected_val in req_conditions.items():
            actual_val = attrs.get(filter_attr)
            if actual_val is None or normalize_attribute_string(actual_val) != normalize_attribute_string(expected_val):
                match = False
                break

        if match:
            v_path = r.get("video_path") or f"{scene_id}.mp4"
            suitable.append({"scene_name": scene_id, "video_path": str(v_path)})

    if not suitable:
        candidates = []
        for r in records:
            scene_id = r["scene_id"]
            v_path = r.get("video_path") or f"{scene_id}.mp4"
            candidates.append({"scene_name": scene_id, "video_path": str(v_path)})
    else:
        candidates = suitable

    if not candidates:
        return []

    if required_count < 0:
        return candidates

    result: list[dict[str, Any]] = []
    for i in range(required_count):
        result.append(candidates[i % len(candidates)])
    return result


def filter_spec_by_qa_template(spec_dict: dict[str, Any], qa_template: dict[str, Any]) -> dict[str, Any]:
    """Dynamically prunes and aligns ODD coverage specification against qa_template.json."""
    filtered_categories: dict[str, Any] = {}
    spec_categories = spec_dict.get("categories", {})

    for cat_name, qa_attrs in qa_template.items():
        cat_data = spec_categories.get(cat_name, {})
        filtered_attrs: dict[str, Any] = {}

        if "threshold" in cat_data:
            filtered_attrs["threshold"] = cat_data["threshold"]

        for qa_attr in qa_attrs:
            if qa_attr in cat_data:
                filtered_attrs[qa_attr] = copy.deepcopy(cat_data[qa_attr])
            else:
                target_alias = QA_SPEC_ATTRIBUTE_ALIASES.get(qa_attr)
                if target_alias and target_alias in cat_data:
                    attr_spec = copy.deepcopy(cat_data[target_alias])
                    src_keys = list(attr_spec.get("source_keys", []))
                    if qa_attr not in src_keys:
                        src_keys.append(qa_attr)
                    attr_spec["source_keys"] = src_keys
                    filtered_attrs[qa_attr] = attr_spec

        if filtered_attrs:
            filtered_categories[cat_name] = filtered_attrs

    result = copy.deepcopy(spec_dict)
    result["categories"] = filtered_categories
    return result


def load_scenes_from_annotations(
    annotation_dir: str | Path, video_dir: str | Path | None = None
) -> list[dict[str, Any]]:
    """Scan annotation directory and build normalized scene records."""
    annot_path = Path(annotation_dir)
    records: list[dict[str, Any]] = []
    if not annot_path.exists():
        return records

    video_extensions = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    video_files = (
        [f for f in Path(video_dir).rglob("*") if f.suffix.lower() in video_extensions]
        if video_dir and Path(video_dir).exists()
        else []
    )

    for file in sorted(annot_path.glob("*.json")):
        if "_var_" in file.name:
            continue
        match = re.match(r"^(scene-\d+)", file.name)
        scene_id = match.group(1) if match else file.stem

        try:
            content = json.loads(file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to read annotation file %s: %s", file, e)
            continue

        video_path = None
        for vfile in video_files:
            if vfile.stem == scene_id:
                video_path = str(vfile)
                break

        records.append(
            {
                "scene_id": scene_id,
                "scene_token": scene_id,
                "video_path": video_path,
                "text_annotation": content,
            }
        )
    return records


def modify_annotation_for_attribute(
    annotation: dict[str, Any], attribute_spec: AttributeSpec, new_value: str
) -> dict[str, Any]:
    """Traverse and modify target attribute value in the annotation dict dynamically."""
    updated = copy.deepcopy(annotation)

    def update_val(sub_dict: Any) -> bool:
        if isinstance(sub_dict, dict):
            if "answer" in sub_dict:
                return False
            for key, val in list(sub_dict.items()):
                if (
                    isinstance(val, dict)
                    and "answer" in val
                    and (
                        key.lower() == attribute_spec.name.lower()
                        or any(k.lower() in key.lower() for k in attribute_spec.source_keys)
                    )
                ):
                    val["answer"] = str(new_value).capitalize()
                    if not val["answer"].endswith("."):
                        val["answer"] += "."
                    return True
                if update_val(val):
                    return True
        elif isinstance(sub_dict, list):
            for item in sub_dict:
                if update_val(item):
                    return True
        return False

    success = update_val(updated)
    if not success:
        category = "sceneDetails"
        name_lower = attribute_spec.name.lower()
        if "road" in name_lower:
            category = "roadDetails"
        elif "ego" in name_lower:
            category = "egoVehicleDetails"
        elif any(k in name_lower for k in ["agent", "pedestrian", "cyclist"]):
            category = "dynamicAgentDetails"

        if category not in updated:
            updated[category] = {}
        updated[category][attribute_spec.name] = {
            "question": f"Generated variation for {attribute_spec.name}",
            "answer": str(new_value).capitalize() + ".",
        }

    return updated


def _resolve_attribute_threshold(attribute_spec: AttributeSpec, category_spec: CategorySpec) -> float:
    if attribute_spec.threshold is not None:
        return attribute_spec.threshold
    if category_spec.threshold is not None:
        return category_spec.threshold
    raise ValueError(
        f"No threshold configured for ODD attribute '{attribute_spec.name}' in category '{category_spec.name}'."
    )


def generate_variations_for_gaps(
    annotation_dir: str | Path,
    output_dir: str | Path,
    spec_path: str | Path,
    video_dir: str | Path | None = None,
    qa_template_path: str | Path | None = None,
) -> dict[str, Any]:
    """Core gap-filling pipeline execution compiling summary, video-input map, and master integration file."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    records = load_scenes_from_annotations(annotation_dir, video_dir=video_dir)
    if not records:
        return {
            "status": "error",
            "message": f"No annotations found in {annotation_dir}",
        }

    if qa_template_path and Path(qa_template_path).exists():
        with open(spec_path, encoding="utf-8") as f:
            spec_dict = json.load(f)
        with open(qa_template_path, encoding="utf-8") as f:
            qa_template = json.load(f)

        filtered_spec = filter_spec_by_qa_template(spec_dict, qa_template)
        config = ODDCoverageConfig.load(filtered_spec)
    else:
        config = ODDCoverageConfig.load(spec_path)

    thresholds = {
        category_name: {
            attribute_name: _resolve_attribute_threshold(attribute_spec, category_spec)
            for attribute_name, attribute_spec in category_spec.attributes.items()
        }
        for category_name, category_spec in config.categories.items()
    }

    engine = ODDCoverageEngine(config)
    engine.load_dataset(records)
    report = engine.analyze()

    needed_variations: dict[str, dict[str, dict[str, int]]] = {}
    for category_name, category_spec in config.categories.items():
        needed_variations[category_name] = {}
        for attribute_name, attribute_spec in category_spec.attributes.items():
            attr_report = report["attributes"].get(attribute_name)
            threshold = thresholds[category_name][attribute_name]
            needed: dict[str, int] = {}
            if attr_report and attr_report["odd_score"] < threshold:
                needed = calculate_needed_variations(
                    attr_report["distribution"],
                    attribute_spec.expected_values,
                    threshold,
                    total_count=attr_report["total"],
                    completeness_weight=config.completeness_weight,
                    coverage_weight=config.coverage_weight,
                    entropy_weight=config.entropy_weight,
                )
                if not needed:
                    raise RuntimeError(
                        f"ODD gap sizing returned no variations for attribute "
                        f"'{attribute_name}' in category '{category_name}' "
                        f"(score={attr_report['odd_score']}, threshold={threshold}, "
                        f"dataset_size={attr_report['total']})."
                    )
            needed_variations[category_name][attribute_name] = needed

    variation_sets: dict[str, list[dict[str, Any]]] = {}
    total_variations = 0

    for category_name, category_spec in config.categories.items():
        category_variations = []
        for attribute_name, attribute_spec in category_spec.attributes.items():
            attr_report = report["attributes"].get(attribute_name)
            if not attr_report:
                continue

            threshold = thresholds[category_name][attribute_name]
            odd_score = attr_report["odd_score"]
            needed = needed_variations[category_name][attribute_name]
            if needed:
                category_variations.append(
                    {
                        "attribute": attribute_name,
                        "odd_score": odd_score,
                        "threshold": threshold,
                        "needed_variations": needed,
                    }
                )

                for val, needed_count in needed.items():
                    generated = 0
                    idx = 0
                    while generated < needed_count:
                        rec = records[idx % len(records)]
                        scene_id = rec["scene_id"]
                        modified = modify_annotation_for_attribute(rec["text_annotation"], attribute_spec, val)

                        run_id = (generated // len(records)) + 1
                        filename = f"{scene_id}_var_{category_name}_{attribute_name}_{val}_run{run_id}.json"

                        scene_folder = out_path / scene_id
                        scene_folder.mkdir(parents=True, exist_ok=True)
                        (scene_folder / filename).write_text(json.dumps(modified, indent=4), encoding="utf-8")

                        total_variations += 1
                        generated += 1
                        idx += 1
        if category_variations:
            variation_sets[category_name] = category_variations

    # 1. Save variation_summary.json
    summary_data = {
        "dataset_scenes_count": len(records),
        "thresholds": thresholds,
        "total_variations_generated": total_variations,
        "variation_sets": variation_sets,
    }
    (out_path / "variation_summary.json").write_text(json.dumps(summary_data, indent=4), encoding="utf-8")

    # 2. Extract scene attributes map for video filtering
    scene_attribute_maps = {
        r["scene_id"]: engine._extract_scene_attributes(
            SceneRecord(r["scene_id"], r["video_path"], r["text_annotation"])
        )
        for r in records
    }

    # 3. Save variations_video_input.json & wfm_generation_input.json (Master Schema)
    video_json_data: dict[str, Any] = {"categories": {}}
    wfm_gaps: dict[str, Any] = {}

    for category_name, category_spec in config.categories.items():
        video_json_data["categories"][category_name] = {}
        for attribute_name, attribute_spec in category_spec.attributes.items():
            video_json_data["categories"][category_name][attribute_name] = {}

            needed = needed_variations[category_name][attribute_name]

            for val in attribute_spec.expected_values:
                count = needed.get(val, 0)
                suitable = select_base_videos_for_attribute(
                    records, scene_attribute_maps, attribute_name, required_count=count
                )
                video_json_data["categories"][category_name][attribute_name][val] = {
                    "count": count,
                    "suitable_videos": suitable,
                }

    for category_name, attr_list in variation_sets.items():
        wfm_gaps[category_name] = {}
        for attr_data in attr_list:
            attr_name = attr_data["attribute"]
            values_data = {}
            for val, count in attr_data["needed_variations"].items():
                values_data[val] = {
                    "required_count": count,
                    "suitable_videos": video_json_data["categories"][category_name][attr_name][val]["suitable_videos"],
                }
            wfm_gaps[category_name][attr_name] = {
                "odd_score": attr_data["odd_score"],
                "values": values_data,
            }

    wfm_input_data = {
        "dataset_scenes_count": len(records),
        "thresholds": thresholds,
        "total_variations_generated": total_variations,
        "generation_gaps": wfm_gaps,
    }

    (out_path / "variations_video_input.json").write_text(json.dumps(video_json_data, indent=4), encoding="utf-8")
    (out_path / "wfm_generation_input.json").write_text(json.dumps(wfm_input_data, indent=4), encoding="utf-8")

    return {
        "status": "success",
        "total_variations_generated": total_variations,
        "variation_sets": variation_sets,
        "overall_odd_score": report["overall_odd_score"],
        "category_scores": report["category_scores"],
        "report": report,
        "summary_data": summary_data,
        "wfm_input_data": wfm_input_data,
        "video_json_data": video_json_data,
    }


# =====================================================================
# 5. ODD SUMMARY REPORTING
# =====================================================================


def build_category_summary(
    report: dict[str, Any],
    scene_count: int,
    minimum_score: float = 0.3,
    config_path: str | Path | ODDCoverageConfig | None = None,
) -> list[dict[str, Any]]:
    """Build category-level summaries showing detected attributes, score, status, and delta."""
    if isinstance(config_path, ODDCoverageConfig):
        config = config_path
    elif config_path:
        config = ODDCoverageConfig.load(config_path)
    else:
        config = None

    summaries: list[dict[str, Any]] = []
    attributes = report.get("attributes", {})
    category_scores = report.get("category_scores", {})
    category_attribute_map: dict[str, list[dict[str, Any]]] = {category: [] for category in category_scores}

    for name, metric in attributes.items():
        category = metric.get("category")
        if not category:
            for gap_category in category_scores:
                if name.startswith(gap_category):
                    category = gap_category
                    break
        if category and category in category_attribute_map:
            category_attribute_map[category].append(metric)

    for category, score in category_scores.items():
        metrics = category_attribute_map.get(category, [])
        detected = sum(x.get("filled", 0) for x in metrics)
        total = sum(x.get("total", 0) for x in metrics)

        resolved_threshold = minimum_score
        if config and category in config.categories:
            category_spec = config.categories[category]
            if category_spec.threshold is not None:
                resolved_threshold = category_spec.threshold

        summaries.append(
            {
                "category": category,
                "set": scene_count,
                "detected": detected,
                "delta": detected - total,
                "status": "ok" if score >= resolved_threshold else "gap",
                "odd_score": score,
            }
        )
    return summaries


def format_category_summary(summaries: Sequence[dict[str, Any]]) -> str:
    """Format category summary dicts into a clean human-readable text table."""
    if not summaries:
        return "No category summaries available."

    lines = [
        f"{'Category':<24} {'Scenes':<8} {'Detected':<10} {'Delta':<8} {'Status':<8} {'ODD Score':<10}",
        "-" * 72,
    ]
    for item in summaries:
        cat = str(item.get("category", ""))
        set_count = item.get("set", 0)
        detected = item.get("detected", 0)
        delta = item.get("delta", 0)
        status = str(item.get("status", "")).upper()
        score = float(item.get("odd_score", 0.0))
        lines.append(f"{cat:<24} {set_count:<8} {detected:<10} {delta:<8} {status:<8} {score:<10.3f}")
    return "\n".join(lines)


__all__ = [
    "BASE_VIDEO_FILTERS",
    "QA_SPEC_ATTRIBUTE_ALIASES",
    "AttributeReport",
    "AttributeSpec",
    "CategorySpec",
    "ODDCoverageConfig",
    "ODDCoverageEngine",
    "SceneRecord",
    "build_category_summary",
    "calculate_needed_variations",
    "filter_spec_by_qa_template",
    "format_category_summary",
    "generate_variations_for_gaps",
    "load_scenes_from_annotations",
    "modify_annotation_for_attribute",
    "normalize_attribute_string",
    "select_base_videos_for_attribute",
]
