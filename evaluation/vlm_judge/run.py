"""CLI entry point for VLM-as-judge dataset evaluation.

Examples:

    # Local Qwen3-VL-4B against a v3.0 LeRobot dataset, first 5 episodes
    python -m evaluation.vlm_judge.run \
        --dataset datasets/cnc_lerobot \
        --backend qwen3-vl \
        --model-id Qwen/Qwen3-VL-4B-Instruct \
        --limit 5 \
        --output outputs/vlm-judge/cnc_lerobot.jsonl

    # Smoke test without a model (works on CPU, no network)
    python -m evaluation.vlm_judge.run \
        --dataset datasets/leisaac-pick-orange \
        --backend echo --limit 2 --output /tmp/echo.jsonl

    # OpenAI-compatible endpoint (vLLM, NIM, Azure OpenAI)
    python -m evaluation.vlm_judge.run \
        --dataset datasets/leisaac-pick-orange \
        --backend openai-compat \
        --model-id Qwen/Qwen3-VL-30B-A3B-Instruct \
        --base-url http://localhost:8000/v1 \
        --output outputs/vlm-judge/leisaac.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from .agent import AgentConfig
from .dataset import EpisodeRecord, iter_episodes
from .service import (
    BackendConfig,
    FrameConfig,
    JudgeService,
    ServiceConfig,
)

_LOGGER = logging.getLogger("evaluation.vlm_judge")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.log_level)

    if args.config_preview:
        _print_config(args)
        return 0

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    instruction_override = args.instruction.strip() or None
    views = tuple(args.views) if args.views else None
    indices = _parse_indices(args.indices)

    episodes = list(
        iter_episodes(
            Path(args.dataset),
            views=views,
            limit=args.limit,
            indices=indices,
        ),
    )
    _LOGGER.info("Resolved %d episodes from %s", len(episodes), args.dataset)
    if not episodes:
        _LOGGER.error("No episodes selected; nothing to do")
        return 1

    service = _build_service(args)
    if not args.dry_run:
        _LOGGER.info("Backend: %s (%s)", service.config.backend.kind, service.model_id)

    n_ok = 0
    n_fail = 0
    started = time.time()
    with output_path.open("w") as fh:
        for ep in episodes:
            try:
                record = _process_episode(
                    episode=ep,
                    service=service,
                    instruction_override=instruction_override,
                    dry_run=args.dry_run,
                    force=args.force,
                )
            except Exception:
                _LOGGER.exception("Episode %s failed; skipping", ep.episode_id)
                n_fail += 1
                continue
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            n_ok += 1

    elapsed = time.time() - started
    _LOGGER.info(
        "Done: %d ok, %d failed in %.1fs (%.1fs/ep avg). Output -> %s",
        n_ok,
        n_fail,
        elapsed,
        elapsed / max(1, n_ok),
        output_path,
    )
    return 0 if n_ok > 0 else 1


# -------------------------------------------------------------------------
# Argument parsing
# -------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vlm_judge.run",
        description="VLM-as-judge evaluation over a LeRobot dataset",
    )
    parser.add_argument("--dataset", required=True, help="LeRobot dataset directory")
    parser.add_argument("--output", required=True, help="Output JSONL file")
    parser.add_argument(
        "--backend",
        choices=("qwen3-vl", "openai-compat", "echo"),
        default="qwen3-vl",
        help="Judge backend implementation",
    )
    parser.add_argument(
        "--model-id",
        default="Qwen/Qwen3-VL-4B-Instruct",
        help="Hugging Face model id (qwen3-vl) or remote model name (openai-compat)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible chat completions base URL (openai-compat backend)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (or set OPENAI_API_KEY env var). Defaults to 'EMPTY' for vLLM/NIM.",
    )
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--n-frames", type=int, default=12, help="Frames sampled per episode")
    parser.add_argument(
        "--n-outcome-samples",
        type=int,
        default=3,
        help="N for outcome MCQ self-consistency voting (use 1 for greedy single pass)",
    )
    parser.add_argument(
        "--frame-size",
        type=int,
        default=448,
        help="Square letterbox size before sending frames to the VLM",
    )
    parser.add_argument(
        "--views",
        nargs="*",
        default=None,
        help="Subset of video views to use; default is all video features",
    )
    parser.add_argument(
        "--instruction",
        default="",
        help="Override the dataset instruction (otherwise read from meta/tasks)",
    )
    parser.add_argument(
        "--indices",
        default=None,
        help="Comma-separated episode indices (e.g. '0,3,5' or '0-9')",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap number of episodes")
    parser.add_argument(
        "--cache-dir",
        default="outputs/vlm-judge/cache",
        help="Cache directory for idempotent judgments (empty string to disable)",
    )
    parser.add_argument("--force", action="store_true", help="Ignore the cache")
    parser.add_argument(
        "--milestone-threshold",
        type=float,
        default=0.85,
        help="Run milestone decomposition when outcome confidence < threshold",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve episodes but skip frame extraction and model inference",
    )
    parser.add_argument(
        "--config-preview",
        action="store_true",
        help="Print resolved configuration and exit",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args(argv)


def _parse_indices(spec: str | None) -> list[int] | None:
    if spec is None:
        return None
    out: list[int] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo, hi = token.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(token))
    return sorted(set(out))


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _print_config(args: argparse.Namespace) -> None:
    cfg = {k: v for k, v in vars(args).items() if k != "config_preview"}
    print(json.dumps(cfg, indent=2, default=str))


# -------------------------------------------------------------------------
# Service + episode processing
# -------------------------------------------------------------------------


def _build_service(args: argparse.Namespace) -> JudgeService:
    backend = BackendConfig(
        kind=args.backend,
        model_id=args.model_id,
        base_url=args.base_url,
        api_key=args.api_key,
        device_map=args.device_map,
        dtype=args.dtype,
    )
    frames = FrameConfig(
        n_frames=args.n_frames,
        target_size=(args.frame_size, args.frame_size),
    )
    agent = AgentConfig(
        n_outcome_samples=args.n_outcome_samples,
        milestone_threshold=args.milestone_threshold,
    )
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    return JudgeService(
        ServiceConfig(backend=backend, frames=frames, agent=agent, cache_dir=cache_dir),
    )


def _process_episode(
    *,
    episode: EpisodeRecord,
    service: JudgeService,
    instruction_override: str | None,
    dry_run: bool,
    force: bool,
) -> dict[str, object]:
    instruction = instruction_override or episode.instruction
    if not instruction:
        _LOGGER.warning("No instruction available for %s", episode.episode_id)

    if dry_run:
        return {
            "episode_id": episode.episode_id,
            "instruction": instruction,
            "duration_s": episode.duration_s,
            "video_paths": {k: str(v) for k, v in episode.video_paths.items()},
            "dry_run": True,
        }

    result = service.judge_episode(
        episode_id=episode.episode_id,
        instruction=instruction,
        video_paths=episode.video_paths,
        from_s=episode.from_timestamp,
        to_s=episode.to_timestamp,
        force=force,
    )
    payload = result.to_dict()
    payload["duration_s"] = episode.duration_s
    payload["video_paths"] = {k: str(v) for k, v in episode.video_paths.items()}
    return payload


if __name__ == "__main__":
    sys.exit(main())
