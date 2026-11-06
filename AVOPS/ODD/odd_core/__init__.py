# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""ODD Scene Variation & Coverage Analysis Package."""

from .annotator import VLMAnnotator, clean_reasoning_tags, encode_video_b64, extract_first_frame_b64
from .engine import (
    BASE_VIDEO_FILTERS,
    QA_SPEC_ATTRIBUTE_ALIASES,
    AttributeReport,
    AttributeSpec,
    CategorySpec,
    ODDCoverageConfig,
    ODDCoverageEngine,
    SceneRecord,
    build_category_summary,
    calculate_needed_variations,
    filter_spec_by_qa_template,
    format_category_summary,
    generate_variations_for_gaps,
    load_scenes_from_annotations,
    modify_annotation_for_attribute,
    normalize_attribute_string,
    select_base_videos_for_attribute,
)

__all__ = [
    "BASE_VIDEO_FILTERS",
    "QA_SPEC_ATTRIBUTE_ALIASES",
    "AttributeReport",
    "AttributeSpec",
    "CategorySpec",
    "ODDCoverageConfig",
    "ODDCoverageEngine",
    "SceneRecord",
    "VLMAnnotator",
    "build_category_summary",
    "calculate_needed_variations",
    "clean_reasoning_tags",
    "encode_video_b64",
    "extract_first_frame_b64",
    "filter_spec_by_qa_template",
    "format_category_summary",
    "generate_variations_for_gaps",
    "load_scenes_from_annotations",
    "modify_annotation_for_attribute",
    "normalize_attribute_string",
    "select_base_videos_for_attribute",
]
