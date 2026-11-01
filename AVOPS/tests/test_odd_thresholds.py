# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Tests for ODD gap-generation threshold resolution."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

AVOPS = Path(__file__).resolve().parents[1]
ODD_DIR = AVOPS / "ODD"
sys.path.insert(0, str(ODD_DIR))

from odd_core import engine as engine_module  # noqa: E402
from odd_core.engine import (  # noqa: E402
    AttributeSpec,
    CategorySpec,
    ODDCoverageConfig,
    _compute_odd_score,
    _resolve_attribute_threshold,
    calculate_needed_variations,
)


def test_attribute_threshold_takes_precedence_over_category_threshold():
    attribute = AttributeSpec(name="weatherType", threshold=0.9)
    category = CategorySpec(name="sceneDetails", threshold=0.8)

    assert _resolve_attribute_threshold(attribute, category) == 0.9


def test_category_threshold_is_used_when_attribute_threshold_is_missing():
    attribute = AttributeSpec(name="weatherType")
    category = CategorySpec(name="sceneDetails", threshold=0.8)

    assert _resolve_attribute_threshold(attribute, category) == 0.8


def test_zero_attribute_threshold_is_preserved():
    attribute = AttributeSpec(name="weatherType", threshold=0.0)
    category = CategorySpec(name="sceneDetails", threshold=0.8)

    assert _resolve_attribute_threshold(attribute, category) == 0.0


def test_missing_attribute_and_category_thresholds_raise_error():
    attribute = AttributeSpec(name="weatherType")
    category = CategorySpec(name="sceneDetails")

    with pytest.raises(ValueError, match=r"weatherType.*sceneDetails"):
        _resolve_attribute_threshold(attribute, category)


def test_gap_generation_uses_configured_thresholds_in_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "spec.json"
    config_path.write_text(
        json.dumps(
            {
                "categories": {
                    "sceneDetails": {
                        "threshold": 0.8,
                        "weatherType": {
                            "expected_values": ["rainy"],
                            "threshold": 0.9,
                        },
                        "timeOfDay": {"expected_values": ["night"]},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    records = [
        {
            "scene_id": "scene",
            "video_path": None,
            "text_annotation": {},
        }
    ]
    monkeypatch.setattr(engine_module, "load_scenes_from_annotations", lambda *args, **kwargs: records)

    class FakeEngine:
        def __init__(self, config: ODDCoverageConfig) -> None:
            self.config = config

        def load_dataset(self, loaded_records: list[dict[str, Any]]) -> None:
            assert loaded_records == records

        def analyze(self) -> dict[str, Any]:
            return {
                "attributes": {
                    name: {"odd_score": 0.7, "distribution": {}, "total": 1} for name in ("weatherType", "timeOfDay")
                },
                "overall_odd_score": 0.7,
                "category_scores": {},
            }

        def _extract_scene_attributes(self, scene: Any) -> dict[str, Any]:
            return {}

    thresholds_used = []

    def calculate_needed_variations(
        distribution: dict[str, int],
        expected_values: list[str],
        threshold: float,
        **kwargs: Any,
    ) -> dict[str, int]:
        thresholds_used.append(threshold)
        return {expected_values[0]: 1}

    monkeypatch.setattr(engine_module, "ODDCoverageEngine", FakeEngine)
    monkeypatch.setattr(engine_module, "calculate_needed_variations", calculate_needed_variations)
    monkeypatch.setattr(engine_module, "select_base_videos_for_attribute", lambda *args, **kwargs: [])

    result = engine_module.generate_variations_for_gaps(
        annotation_dir=tmp_path,
        output_dir=tmp_path / "output",
        spec_path=config_path,
    )

    expected_thresholds = {"sceneDetails": {"weatherType": 0.9, "timeOfDay": 0.8}}
    assert result["summary_data"]["thresholds"] == expected_thresholds
    assert result["wfm_input_data"]["thresholds"] == expected_thresholds
    assert [item["threshold"] for item in result["variation_sets"]["sceneDetails"]] == [0.9, 0.8]
    assert thresholds_used == [0.9, 0.8]


def test_variation_solver_uses_composite_score_and_dataset_size() -> None:
    expected_values = ["sunny", "rainy", "snowy", "foggy"]
    distribution = {value: 15 for value in expected_values}
    initial_score = _compute_odd_score(Counter(distribution), expected_values, 100, 0.3, 0.35, 0.35)

    needed = calculate_needed_variations(
        distribution,
        expected_values,
        target_score=0.9,
        total_count=100,
    )

    assert initial_score == pytest.approx(0.88)
    assert needed == {value: 5 for value in expected_values}
    final_distribution = Counter({value: 20 for value in expected_values})
    assert _compute_odd_score(final_distribution, expected_values, 120, 0.3, 0.35, 0.35) == pytest.approx(0.9)


def test_variation_solver_returns_empty_when_target_is_unreachable() -> None:
    assert (
        calculate_needed_variations(
            {"sunny": 3, "rainy": 0},
            ["sunny", "rainy"],
            target_score=1.0,
            total_count=5,
        )
        == {}
    )


def test_gap_generation_fails_when_detected_gap_cannot_be_sized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "spec.json"
    config_path.write_text(
        json.dumps(
            {
                "categories": {
                    "sceneDetails": {
                        "weatherType": {
                            "expected_values": ["rainy"],
                            "threshold": 0.9,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    records = [{"scene_id": "scene", "video_path": None, "text_annotation": {}}]
    monkeypatch.setattr(engine_module, "load_scenes_from_annotations", lambda *args, **kwargs: records)

    class FakeEngine:
        def __init__(self, config: ODDCoverageConfig) -> None:
            self.config = config

        def load_dataset(self, loaded_records: list[dict[str, Any]]) -> None:
            assert loaded_records == records

        def analyze(self) -> dict[str, Any]:
            return {
                "attributes": {
                    "weatherType": {
                        "odd_score": 0.7,
                        "distribution": {"rainy": 1},
                        "total": 1,
                    }
                },
                "overall_odd_score": 0.7,
                "category_scores": {},
            }

    monkeypatch.setattr(engine_module, "ODDCoverageEngine", FakeEngine)
    monkeypatch.setattr(engine_module, "calculate_needed_variations", lambda *args, **kwargs: {})

    with pytest.raises(RuntimeError, match=r"weatherType.*dataset_size=1"):
        engine_module.generate_variations_for_gaps(
            annotation_dir=tmp_path,
            output_dir=tmp_path / "output",
            spec_path=config_path,
        )
