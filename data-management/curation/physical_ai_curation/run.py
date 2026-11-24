"""CLI entry point for VLM-as-judge dataset evaluation.

Each output has an adjacent ``<output>.config.json`` declaration written
before service creation. It records explicit instruction overrides and
non-secret settings; credentials and endpoint URLs are excluded.

Examples:

    # Local Qwen3-VL-4B against a v3.0 LeRobot dataset, first 5 episodes
    python -m physical_ai_curation.run \
        --dataset datasets/cnc_lerobot \
        --backend qwen3-vl \
        --model-id Qwen/Qwen3-VL-4B-Instruct \
        --limit 5 \
        --output outputs/vlm-judge/cnc_lerobot.jsonl

    # Smoke test without a model (works on CPU, no network)
    python -m physical_ai_curation.run \
        --dataset datasets/leisaac-pick-orange \
        --backend echo --limit 2 --output /tmp/echo.jsonl

    # OpenAI-compatible endpoint (vLLM, NIM, Azure OpenAI)
    python -m physical_ai_curation.run \
        --dataset datasets/leisaac-pick-orange \
        --backend openai-compat \
        --model-id Qwen/Qwen3-VL-30B-A3B-Instruct \
        --base-url http://localhost:8000/v1 \
        --output outputs/vlm-judge/leisaac.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from physical_ai_dataset_io.lerobot import EpisodeRecord, iter_episodes

from physical_ai_curation.adapters.job_storage import LocalJobStore
from physical_ai_curation.adapters.local_dataset import LocalDatasetResolver
from physical_ai_curation.adapters.local_storage import LocalCurationStorage
from physical_ai_curation.application import apply_judge_result
from physical_ai_curation.jobs import JudgeJobs
from physical_ai_curation.saved_input import SavedInputSnapshot

from .job_cli import add_job_arguments, execute_job_command, read_sample_references

if TYPE_CHECKING:
    from evaluation.vlm_judge.service import JudgeService

_LOGGER = logging.getLogger("evaluation.vlm_judge")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.log_level)

    if args.config_preview:
        _print_config(args)
        return 0

    if not args.dry_run:
        try:
            return asyncio.run(_durable_main(args))
        except KeyboardInterrupt:
            _LOGGER.info("Client interrupted; accepted jobs remain available through status")
            return 130
        except (ValueError, OSError, KeyError, ImportError) as error:
            _LOGGER.error("Judge command failed category=%s", type(error).__name__)
            return 2

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

    declared_config = {
        "schema_version": 1,
        "instruction_origin": "cli-override" if instruction_override else "saved-input",
        "instruction_override": instruction_override,
        "backend": args.backend,
        "model_id": args.model_id,
        "model_revision": args.model_revision,
        "views": views,
        "episode_indices": [episode.episode_index for episode in episodes],
        "n_frames": args.n_frames,
        "frame_size": args.frame_size,
        "n_outcome_samples": args.n_outcome_samples,
        "milestone_threshold": args.milestone_threshold,
        "dry_run": args.dry_run,
    }
    reader = LocalDatasetResolver(
        {args.dataset_id or Path(args.dataset).resolve().name: Path(args.dataset)},
        instruction_override=instruction_override,
    )
    snapshots = [asyncio.run(_resolve_episode_input(reader, episode, args)) for episode in episodes]
    declared_config["saved_inputs"] = [snapshot.model_dump(mode="json") for snapshot in snapshots]
    episodes = [
        replace(episode, instruction=snapshot.instruction, snapshot_id=snapshot.snapshot_id)
        for episode, snapshot in zip(episodes, snapshots, strict=True)
    ]
    output_path.with_suffix(output_path.suffix + ".config.json").write_text(
        json.dumps(declared_config, indent=2) + "\n", encoding="utf-8"
    )
    service = None

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
                asyncio.run(_resolve_episode_input(reader, ep, args, ep.snapshot_id))
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
    return 0 if n_ok > 0 and n_fail == 0 else 1


# -------------------------------------------------------------------------
# Argument parsing
# -------------------------------------------------------------------------


async def _durable_main(args: argparse.Namespace) -> int:
    root = Path(args.dataset).resolve()
    dataset_id = args.dataset_id or root.name
    output = Path(args.output)
    override = args.instruction.strip() or None
    if override and not args.single and args.operation == "submit":
        raise ValueError("Declared instruction overrides require the explicit single-episode action")
    resolver = LocalDatasetResolver({dataset_id: root}, instruction_override=override)
    indices = []
    if args.operation == "submit":
        records = list(
            iter_episodes(
                root,
                views=tuple(args.views) if args.views else None,
                indices=_parse_indices(args.indices),
                limit=args.limit,
            )
        )
        indices = [record.episode_index for record in records]
        samples = read_sample_references(args.sample_references)
        snapshots = []
        for index in indices:
            reference = samples.get(index, {}) if args.mode == "sample" else {}
            _, snapshot = await resolver.resolve(
                dataset_id,
                index,
                principal_scope_id=args.principal_scope_id,
                views=tuple(args.views or ()),
                annotation_author_id=reference.get("annotation_author_id", args.annotation_author_id),
                expected_snapshot_id=reference.get("snapshot_id"),
            )
            snapshots.append(snapshot.model_dump(mode="json"))
        output.parent.mkdir(parents=True, exist_ok=True)
        declaration = {key: value for key, value in vars(args).items() if key not in {"api_key", "base_url"}}
        declaration.update(
            instruction_origin="cli-override" if override else "saved-input",
            instruction_override=override,
            saved_inputs=snapshots,
        )
        output.with_suffix(output.suffix + ".config.json").write_text(
            json.dumps(declaration, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    executor = None
    config = {}
    if args.operation in {"submit", "worker"} or (args.operation == "retry" and not args.detach):
        from evaluation.vlm_judge.run import build_service
        from physical_ai_curation.adapters.evaluator import ServiceExecutor

        service = build_service(args)
        executor = ServiceExecutor(service)
        config = executor.configuration(
            {
                "views": args.views,
                "annotation_author_id": args.annotation_author_id,
                "force": args.force,
                "instruction_override": override,
            }
        )
    storage = LocalCurationStorage({dataset_id: root})

    async def apply_result(job: dict[str, object], target: dict[str, object]) -> bool:
        return await apply_judge_result(storage, job, target)

    jobs = JudgeJobs(
        LocalJobStore(args.job_dir or root.parent / ".curation" / "judge"),
        resolver,
        executor,
        capacity=args.capacity,
        capacity_scope=args.capacity_scope,
        apply_result=apply_result,
        curation_storage=storage,
    )
    code, result = await execute_job_command(
        jobs,
        args,
        dataset_id=dataset_id,
        actor=args.principal_scope_id,
        config=config,
        indices=indices,
    )
    if args.operation in {"submit", "retry"} and not args.detach and "targets" in result:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as stream:
            for target in result["targets"]:
                if target["result"] is not None:
                    stream.write(
                        json.dumps({**target["result"], "run_id": result["id"], "result_id": target["result_id"]})
                        + "\n"
                    )
    print(json.dumps(result))
    return code


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="physical_ai_curation.run",
        description="VLM-as-judge evaluation over a LeRobot dataset",
    )
    parser.add_argument("--dataset", required=True, help="LeRobot dataset directory")
    parser.add_argument("--output", default="outputs/vlm-judge/results.jsonl", help="Saved result JSONL export")
    add_job_arguments(parser)
    parser.add_argument("--dataset-id", default=None, help="Canonical dataset ID; defaults to the root directory name")
    parser.add_argument(
        "--principal-scope-id", default="local", help="Saved edit author scope for explicit local operation"
    )
    parser.add_argument("--annotation-author-id", default=None, help="Select one saved instruction author")
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
        "--model-revision",
        default=None,
        help="Immutable HF commit SHA to pin the qwen3-vl model/processor download",
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
    cfg = {k: v for k, v in vars(args).items() if k not in {"config_preview", "api_key", "base_url"}}
    print(json.dumps(cfg, indent=2, default=str))


# -------------------------------------------------------------------------
# Service + episode processing
# -------------------------------------------------------------------------


async def _resolve_episode_input(
    reader: LocalDatasetResolver,
    episode: EpisodeRecord,
    args: argparse.Namespace,
    expected_snapshot_id: str | None = None,
) -> SavedInputSnapshot:
    dataset_id = args.dataset_id or Path(args.dataset).resolve().name
    _, snapshot = await reader.resolve(
        dataset_id,
        episode.episode_index,
        principal_scope_id=args.principal_scope_id,
        views=tuple(args.views or ()),
        annotation_author_id=args.annotation_author_id,
        expected_snapshot_id=expected_snapshot_id,
    )
    return snapshot


def _process_episode(
    *,
    episode: EpisodeRecord,
    service: JudgeService | None,
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

    if service is None:
        raise ValueError("Judge unavailable")
    result = service.judge_episode(
        episode_id=episode.episode_id,
        instruction=instruction,
        video_paths=episode.video_paths,
        from_s=episode.from_timestamp,
        to_s=episode.to_timestamp,
        video_windows=episode.video_windows,
        media_identity=episode.media_identity,
        snapshot_id=episode.snapshot_id,
        force=force,
    )
    payload = result.to_dict()
    payload["duration_s"] = episode.duration_s
    payload["video_paths"] = {k: str(v) for k, v in episode.video_paths.items()}
    return payload


if __name__ == "__main__":
    sys.exit(main())
