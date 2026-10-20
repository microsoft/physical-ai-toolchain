"""Direct VLM scoring of LeRobot media, without curation or durable jobs."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from physical_ai_dataset_io.lerobot import iter_episodes

from .agent import AgentConfig
from .service import BackendConfig, FrameConfig, JudgeService, ServiceConfig

_LOGGER = logging.getLogger("evaluation.vlm_judge")


def build_service(args: argparse.Namespace) -> JudgeService:
    """Construct the declared inference runtime without loading model weights."""
    return JudgeService(
        ServiceConfig(
            backend=BackendConfig(
                kind=args.backend,
                model_id=args.model_id,
                revision=args.model_revision,
                base_url=args.base_url,
                api_key=args.api_key,
                device_map=args.device_map,
                dtype=args.dtype,
            ),
            frames=FrameConfig(n_frames=args.n_frames, target_size=(args.frame_size, args.frame_size)),
            agent=AgentConfig(
                n_outcome_samples=args.n_outcome_samples,
                milestone_threshold=args.milestone_threshold,
            ),
            cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        )
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="evaluation.vlm_judge.run", description=__doc__)
    parser.add_argument("--dataset", required=True, help="LeRobot dataset directory")
    parser.add_argument("--output", default="outputs/vlm-judge/results.jsonl")
    parser.add_argument("--backend", choices=("qwen3-vl", "openai-compat", "echo"), default="qwen3-vl")
    parser.add_argument("--model-id", default="Qwen/Qwen3-VL-4B-Instruct")
    parser.add_argument("--model-revision", default=None, help="Immutable model download revision")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--n-frames", type=int, default=12)
    parser.add_argument("--n-outcome-samples", type=int, default=3)
    parser.add_argument("--frame-size", type=int, default=448)
    parser.add_argument("--views", nargs="*", default=None)
    parser.add_argument("--instruction", default="", help="Override dataset metadata instruction")
    parser.add_argument("--indices", default=None, help="Comma-separated episode indices or ranges")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--cache-dir", default="outputs/vlm-judge/cache")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--milestone-threshold", type=float, default=0.85)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--config-preview", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser.parse_args(argv)


def _parse_indices(specification: str | None) -> list[int] | None:
    if specification is None:
        return None
    indices = []
    for token in specification.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lower, upper = token.split("-", 1)
            indices.extend(range(int(lower), int(upper) + 1))
        else:
            indices.append(int(token))
    return sorted(set(indices))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    declaration = {
        key: value for key, value in vars(args).items() if key not in {"config_preview", "api_key", "base_url"}
    }
    if args.config_preview:
        print(json.dumps(declaration, indent=2))
        return 0
    try:
        episodes = list(
            iter_episodes(
                Path(args.dataset),
                views=tuple(args.views) if args.views else None,
                indices=_parse_indices(args.indices),
                limit=args.limit,
            )
        )
        if not episodes:
            _LOGGER.error("No episodes selected")
            return 1
        instruction_override = args.instruction.strip() or None
        declaration.update(
            schema_version=1,
            instruction_origin="cli-override" if instruction_override else "dataset-metadata",
            instruction_override=instruction_override,
            episode_indices=[episode.episode_index for episode in episodes],
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix(output.suffix + ".config.json").write_text(
            json.dumps(declaration, indent=2) + "\n", encoding="utf-8"
        )
        service = None if args.dry_run else build_service(args)
        failures = 0
        with output.open("w", encoding="utf-8") as stream:
            for episode in episodes:
                instruction = instruction_override or episode.instruction
                payload = {
                    "episode_id": episode.episode_id,
                    "instruction": instruction,
                    "duration_s": episode.duration_s,
                    "video_paths": {name: str(path) for name, path in episode.video_paths.items()},
                }
                if service is None:
                    payload["dry_run"] = True
                else:
                    try:
                        result = service.judge_episode(
                            episode_id=episode.episode_id,
                            instruction=instruction,
                            video_paths=episode.video_paths,
                            from_s=episode.from_timestamp,
                            to_s=episode.to_timestamp,
                            video_windows=episode.video_windows,
                            media_identity=episode.media_identity,
                            force=args.force,
                        )
                        payload.update(result.to_dict())
                    except Exception as error:
                        failures += 1
                        _LOGGER.error("Episode scoring failed category=%s", type(error).__name__)
                        continue
                stream.write(json.dumps(payload) + "\n")
                stream.flush()
        return 1 if failures else 0
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError) as error:
        _LOGGER.error("Inference command failed category=%s", type(error).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
