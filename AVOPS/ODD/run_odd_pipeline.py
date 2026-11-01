#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""
ODD Coverage Analysis & Scene Variation Gap Detection Component
======================================================================
Standalone Docker container entrypoint for ODD gap analysis.

Inputs:
  - Directory containing driving videos (and optional pre-existing annotations)
  - VLM endpoint (Cosmos-Reason2-2B BYOM adapter or OpenAI-compatible server)

Outputs:
  - variation_summary.json: ODD completeness, entropy, and shortfall
  - variations_video_input.json: Base video mapping for detected gaps
  - wfm_generation_input.json: Master scene synthesis specification
  - final_gap_output.json: Executive summary of all coverage gaps & recommendations
  - Per-scene variation specification JSONs (<scene_id>/<scene_id>_var_...json)
  - Text_Annotations/: Extracted/validated base scene annotations
"""

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

# Add component root to sys.path
script_dir = Path(__file__).resolve().parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from odd_core import (  # noqa: E402
    ODDCoverageConfig,
    ODDCoverageEngine,
    VLMAnnotator,
    build_category_summary,
    filter_spec_by_qa_template,
    format_category_summary,
    generate_variations_for_gaps,
    load_scenes_from_annotations,
)
from odd_core.prompt_writer import PROMPTS_FILENAME, PromptWriter, write_generation_prompts  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ODD-Pipeline")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Physical AI Data Factory - ODD Gap Analysis & Scene Variation Component",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input-dir",
        "-i",
        default=os.environ.get("INPUT_DIR", "/input"),
        help="Input directory containing video clips (or mounted volume)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=os.environ.get("OUTPUT_DIR", "/output"),
        help="Output directory for ODD test_output and final gap outputs",
    )
    parser.add_argument(
        "--annotations-dir",
        "-a",
        default=os.environ.get("ANNOTATIONS_DIR", None),
        help="Directory with pre-existing annotations (optional, auto-detected if in input-dir)",
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=float,
        default=float(os.environ.get("ODD_SCORE_THRESHOLD", "0.8")),
        help="Overall dataset ODD score threshold for status reporting (e.g. 0.8)",
    )
    parser.add_argument(
        "--vlm-url",
        default=os.environ.get("VLM_URL", "http://host.docker.internal:8010/v1"),
        help="OpenAI-compatible VLM endpoint URL (e.g. http://127.0.0.1:8010/v1)",
    )
    parser.add_argument(
        "--vlm-model",
        default=os.environ.get("VLM_MODEL", "nvidia/Cosmos-Reason2-2B"),
        help="Model name for VLM perception endpoint",
    )
    parser.add_argument(
        "--vlm-timeout",
        type=float,
        default=float(os.environ.get("VLM_TIMEOUT", "120.0")),
        help="Timeout in seconds for VLM inference calls",
    )
    parser.add_argument(
        "--vlm-input",
        choices=("video", "frame"),
        default=os.environ.get("VLM_INPUT", "video"),
        help="What the VLM sees: the whole clip (video) or only its first frame (frame)",
    )
    parser.add_argument(
        "--vlm-max-video-mb",
        type=float,
        default=float(os.environ.get("VLM_MAX_VIDEO_MB", "32")),
        help="Largest clip sent to the VLM as it is; larger clips are re-encoded smaller",
    )
    parser.add_argument(
        "--qa-template",
        default=str(script_dir / "configs" / "qa_template.json"),
        help="Path to QA template specification JSON",
    )
    parser.add_argument(
        "--spec-file",
        default=str(script_dir / "configs" / "odd_coverage_spec.json"),
        help="Path to ODD coverage specification JSON",
    )
    parser.add_argument(
        "--host-video-dir",
        default=os.environ.get("HOST_VIDEO_DIR", None),
        help="Host path prefix to replace container input-dir in generated outputs",
    )
    parser.add_argument(
        "--mock-vlm",
        action="store_true",
        help="Force mock perception annotator (for offline simulation / tests)",
    )
    parser.add_argument(
        "--no-generation-prompts",
        dest="generation_prompts",
        action="store_false",
        help="Skip writing one text prompt paragraph per scene variation",
    )

    return parser.parse_args()


def discover_videos(input_dir: Path) -> list[Path]:
    """Find all supported video files in input directory or immediate subdirectories."""
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    videos: list[Path] = []

    if not input_dir.exists():
        return videos

    # 1. Search root input directory
    for f in sorted(input_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in video_exts:
            videos.append(f)

    # 2. Check standard subdirectories if none found in root
    if not videos:
        subdirs = ["physical_ai_videos", "videos", "trimmed_93f"]
        for sd in subdirs:
            candidate = input_dir / sd
            if candidate.is_dir():
                for f in sorted(candidate.iterdir()):
                    if f.is_file() and f.suffix.lower() in video_exts:
                        videos.append(f)
                if videos:
                    break

    # 3. Recursive fallback
    if not videos:
        for f in sorted(input_dir.rglob("*")):
            if f.is_file() and f.suffix.lower() in video_exts and not f.name.startswith("."):
                videos.append(f)

    return videos


def find_existing_annotations(input_dir: Path, custom_annot_dir: str | None) -> Path | None:
    """Look for pre-existing annotations directory."""
    if custom_annot_dir:
        p = Path(custom_annot_dir)
        if p.exists() and any(p.glob("*.json")):
            return p

    candidates = [
        input_dir / "Text_Annotations",
        input_dir / "annotations",
        input_dir / "Annotations",
        input_dir,
    ]
    for c in candidates:
        if c.exists() and c.is_dir():
            jsons = [f for f in c.glob("*.json") if "_var_" not in f.name and "summary" not in f.name]
            if len(jsons) > 0:
                return c
    return None


def rewrite_video_paths(file_path: Path, container_dir: str, host_dir: str) -> None:
    """Rewrite container input path with host path in JSON file."""
    if not file_path.exists():
        return
    try:
        content = file_path.read_text(encoding="utf-8")
        clean_container = str(Path(container_dir).resolve()).rstrip("/")
        clean_host = str(Path(host_dir)).rstrip("/")

        replaced = content.replace(clean_container, clean_host)
        file_path.write_text(replaced, encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to rewrite host video paths in %s: %s", file_path.name, e)


def print_banner():
    print("=" * 72)
    print("   PHYSICAL AI DATA FACTORY: ODD COVERAGE & GAP ANALYSIS COMPONENT   ")
    print("=" * 72)


def main():
    args = parse_args()
    print_banner()

    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    annotations_out_dir = output_dir / "Text_Annotations"
    annotations_out_dir.mkdir(parents=True, exist_ok=True)

    qa_template_path = Path(args.qa_template)
    spec_path = Path(args.spec_file)

    if not qa_template_path.exists():
        logger.error("QA Template not found at: %s", qa_template_path)
        sys.exit(1)
    if not spec_path.exists():
        logger.error("ODD Specification not found at: %s", spec_path)
        sys.exit(1)

    with open(qa_template_path, encoding="utf-8") as f:
        qa_template = json.load(f)

    with open(spec_path, encoding="utf-8") as f:
        spec_dict = json.load(f)

    logger.info("Configuration:")
    logger.info("  Input Directory:      %s", input_dir)
    logger.info("  Output Directory:     %s", output_dir)
    logger.info("  ODD Score Threshold:  %s", args.threshold)
    logger.info("  QA Template:          %s", qa_template_path.name)
    logger.info("  ODD Specification:    %s", spec_path.name)
    logger.info("  VLM Endpoint URL:     %s", args.vlm_url)
    logger.info("  VLM Model:            %s", args.vlm_model)

    # -------------------------------------------------------------------------
    # 1. Discover Videos
    # -------------------------------------------------------------------------
    videos = discover_videos(input_dir)
    logger.info("Discovered %d video file(s) in input directory.", len(videos))

    # -------------------------------------------------------------------------
    # 2. Annotation Resolution (Existing vs. VLM Perception)
    # -------------------------------------------------------------------------
    existing_annot_dir = find_existing_annotations(input_dir, args.annotations_dir)
    annotator = VLMAnnotator(
        endpoint_url=args.vlm_url,
        model_name=args.vlm_model,
        timeout=args.vlm_timeout,
        media=args.vlm_input,
        max_video_bytes=int(args.vlm_max_video_mb * 1024 * 1024),
    )

    vlm_healthy, health_msg = annotator.check_health()
    logger.info("VLM Endpoint Status: %s (%s)", "HEALTHY" if vlm_healthy else "OFFLINE / UNREACHABLE", health_msg)

    if existing_annot_dir:
        logger.info("Found existing annotations in: %s", existing_annot_dir)
        # Copy existing annotations to output Text_Annotations
        for f in existing_annot_dir.glob("*.json"):
            if "_var_" not in f.name and not f.name.endswith("_input.json") and "summary" not in f.name:
                shutil.copy2(f, annotations_out_dir / f.name)

    # Check for missing annotations for any discovered videos
    missing_videos = []
    for v in videos:
        annot_file = annotations_out_dir / f"{v.stem}.json"
        if not annot_file.exists():
            missing_videos.append(v)

    if missing_videos:
        logger.info("Need to generate annotations for %d video(s)...", len(missing_videos))
        use_mock = args.mock_vlm or (not vlm_healthy and args.auto_fallback)

        if not vlm_healthy and not use_mock:
            logger.error("VLM endpoint at %s is unreachable and --mock-vlm is not set.", args.vlm_url)
            logger.error("Resolution options:")
            logger.error("  1. Start VLM Adapter: uv run odd_core/cosmos_reason_vlm_adapter.py --port 8010")
            logger.error("  2. If using Docker on Linux, supply '--network host' to reach host port 8010.")
            logger.error("  3. Mount pre-existing annotations to /input/Text_Annotations.")
            logger.error("  4. Pass '--mock-vlm' for testing/simulation.")
            sys.exit(1)

        if use_mock:
            logger.warning("Generating heuristic/mock annotations (VLM is offline or --mock-vlm specified)...")

        for vid in missing_videos:
            out_file = annotations_out_dir / f"{vid.stem}.json"
            try:
                res = annotator.annotate_video(vid, qa_template, fallback_mock=use_mock)
                out_file.write_text(json.dumps(res, indent=4), encoding="utf-8")
                logger.info("Annotated %s -> %s", vid.name, out_file.name)
            except Exception as e:
                logger.error("Failed to annotate %s: %s", vid.name, e)

    # -------------------------------------------------------------------------
    # 3. ODD Coverage Engine & Gap Analysis
    # -------------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("STEP 2: RUNNING ODD COVERAGE ENGINE & ENTROPY GAP-FILLING SOLVER")
    print("=" * 72)

    # Align spec with QA template
    filtered_spec = filter_spec_by_qa_template(spec_dict, qa_template)
    config = ODDCoverageConfig.load(filtered_spec)

    records = load_scenes_from_annotations(annotations_out_dir, video_dir=input_dir)
    if not records:
        logger.error("No valid scene records found in %s", annotations_out_dir)
        sys.exit(1)

    logger.info("Loaded %d scene record(s) for ODD coverage analysis.", len(records))

    engine = ODDCoverageEngine(config)
    engine.load_dataset(records)
    report = engine.analyze()

    overall_score = report.get("overall_odd_score", 0.0)
    category_scores = report.get("category_scores", {})
    logger.info("Overall ODD Score: %.4f (Threshold: %.2f)", overall_score, args.threshold)

    cat_summaries = build_category_summary(report, scene_count=len(records), config_path=config)
    print("\n" + format_category_summary(cat_summaries))

    # -------------------------------------------------------------------------
    # 4. Synthesize Gap Variations & Output Files
    # -------------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("STEP 3: GENERATING SCENE VARIATIONS & ARTIFACTS")
    print("=" * 72)

    result = generate_variations_for_gaps(
        annotation_dir=str(annotations_out_dir),
        output_dir=str(output_dir),
        spec_path=str(spec_path),
        video_dir=str(input_dir),
        qa_template_path=str(qa_template_path),
    )

    if result.get("status") == "error":
        logger.error("Variation generation failed: %s", result.get("message"))
        sys.exit(1)

    total_variations = result.get("total_variations_generated", 0)
    variation_sets = result.get("variation_sets", {})

    # Rewrite video paths if host_video_dir was specified
    if args.host_video_dir:
        logger.info("Rewriting container paths to host directory: %s", args.host_video_dir)
        rewrite_video_paths(output_dir / "variations_video_input.json", str(input_dir), args.host_video_dir)
        rewrite_video_paths(output_dir / "wfm_generation_input.json", str(input_dir), args.host_video_dir)

    # Summarize each variation into one paragraph for text-prompted Cosmos models.
    prompt_count = 0
    if args.generation_prompts:
        use_llm = vlm_healthy and not args.mock_vlm
        if use_llm:
            writer = PromptWriter.from_endpoint(args.vlm_url, args.vlm_model, annotator.api_key, args.vlm_timeout)
        else:
            logger.warning("Writing template prompts (VLM is offline or --mock-vlm specified)")
            writer = PromptWriter()
        prompt_index = write_generation_prompts(output_dir, config, writer, args.vlm_model if use_llm else "")
        prompt_count = len(prompt_index["prompts"])
        logger.info("Wrote %d generation prompt(s) to %s", prompt_count, PROMPTS_FILENAME)

    # -------------------------------------------------------------------------
    # 5. Generate final_gap_output.json
    # -------------------------------------------------------------------------
    detected_gaps_detail = []
    for cat_name, attr_list in variation_sets.items():
        for item in attr_list:
            attr_name = item["attribute"]
            score = item["odd_score"]
            needed = item["needed_variations"]
            total_needed = sum(needed.values())
            detected_gaps_detail.append(
                {
                    "category": cat_name,
                    "attribute": attr_name,
                    "odd_score": round(score, 4),
                    "threshold": item["threshold"],
                    "status": "GAP",
                    "needed_variations": needed,
                    "total_needed": total_needed,
                    "recommendation": (
                        f"Augment dataset with {total_needed} variation(s) across: {', '.join(needed.keys())}"
                    ),
                }
            )

    final_gap_output = {
        "status": "COMPLETED",
        "dataset_summary": {
            "total_scenes": len(records),
            "overall_odd_score": round(overall_score, 4),
            "odd_score_threshold": args.threshold,
            "overall_status": (
                "PASSED" if overall_score >= args.threshold and not detected_gaps_detail else "GAPS_DETECTED"
            ),
            "total_gaps_detected": len(detected_gaps_detail),
            "total_variations_generated": total_variations,
        },
        "category_scores": {k: round(v, 4) for k, v in category_scores.items()},
        "category_summaries": cat_summaries,
        "detected_gaps": detected_gaps_detail,
        "output_artifacts": {
            "variation_summary": str(output_dir / "variation_summary.json"),
            "variations_video_input": str(output_dir / "variations_video_input.json"),
            "wfm_generation_input": str(output_dir / "wfm_generation_input.json"),
            "final_gap_output": str(output_dir / "final_gap_output.json"),
            "generation_prompts": str(output_dir / PROMPTS_FILENAME) if prompt_count else None,
            "text_annotations_dir": str(annotations_out_dir),
        },
    }

    final_gap_path = output_dir / "final_gap_output.json"
    final_gap_path.write_text(json.dumps(final_gap_output, indent=4), encoding="utf-8")
    logger.info("Saved final gap outputs to: %s", final_gap_path.name)

    # -------------------------------------------------------------------------
    # 6. Final Summary Display
    # -------------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("FINAL ODD GAP ANALYSIS & SCENE VARIATION REPORT")
    print("=" * 72)
    print(f"Dataset Scenes Evaluated:    {len(records)}")
    print(f"Overall ODD Score:           {overall_score:.4f} (Target Threshold: {args.threshold:.2f})")
    print(f"Status:                      {final_gap_output['dataset_summary']['overall_status']}")
    print(f"Total Coverage Gaps:         {len(detected_gaps_detail)}")
    print(f"Total Variations Generated:  {total_variations}")
    print("-" * 72)

    if detected_gaps_detail:
        print("DETECTED COVERAGE GAPS & SHORTFALL:")
        for idx, gap in enumerate(detected_gaps_detail, 1):
            print(f"  {idx}. [{gap['category']}] {gap['attribute']}:")
            print(f"     Current Score: {gap['odd_score']:.4f} < Threshold: {gap['threshold']:.2f}")
            print(f"     Needed Variations: {gap['needed_variations']}")
            print(f"     Action: {gap['recommendation']}")
    else:
        print("No gaps detected! Dataset satisfies all ODD threshold criteria.")

    print("\n" + "-" * 72)
    print("OUTPUT FILES & DIRECTORIES VERIFICATION:")
    expected_files = [
        "variation_summary.json",
        "variations_video_input.json",
        "wfm_generation_input.json",
        "final_gap_output.json",
    ]
    for fn in expected_files:
        fp = output_dir / fn
        if fp.exists():
            print(f"  [OK] {fn:<28} ({fp.stat().st_size:,} bytes)")
        else:
            print(f"  [MISSING] {fn}")

    # Count scene variation subfolders
    scene_folders = [d for d in output_dir.iterdir() if d.is_dir() and d.name != "Text_Annotations"]
    total_var_files = sum(len(list(d.glob("*.json"))) for d in scene_folders)
    print(f"  [OK] Scene Variation Folders:    {len(scene_folders)} folders ({total_var_files} variation configs)")
    print(f"  [OK] Text Annotations:           {len(list(annotations_out_dir.glob('*.json')))} files")
    print("=" * 72)
    print("ODD COMPONENT EXECUTION COMPLETED SUCCESSFULLY!\n")


if __name__ == "__main__":
    main()
