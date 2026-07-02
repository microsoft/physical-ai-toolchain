"""Policy-rollout adapter for VLM-as-judge evaluation.

LeRobot-format datasets are scored via :mod:`evaluation.vlm_judge.run`. This
module adds the symmetric path for **policy-rollout artifacts** — directories
of MP4 files produced by inference runs (e.g. ``leisaac-tests/pickup-orange/``).

A rollout directory typically contains:

::

    rollout-dir/
    ├── leisaac-videos/
    │   ├── pick_orange.mp4
    │   ├── pick_orange_run-001.mp4
    │   └── ...
    └── leisaac-sweeps/
        └── ...

This module discovers MP4 files under one or more roots, pairs them with a
language instruction (CLI arg, sidecar JSON, or filename heuristic) and
emits a JSONL stream of ``JudgeResult`` records via :class:`JudgeService`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from .service import (
    BackendConfig,
    FrameConfig,
    JudgeService,
    ServiceConfig,
)

_LOGGER = logging.getLogger("evaluation.vlm_judge")


@dataclass(frozen=True, slots=True)
class RolloutVideo:
    """One rollout MP4 ready for VLM judgment."""

    episode_id: str
    instruction: str
    video_path: Path


def discover_rollouts(
    roots: Sequence[Path],
    *,
    instruction: str,
    instructions_file: Path | None = None,
) -> Iterator[RolloutVideo]:
    """Yield rollout MP4s under ``roots`` paired with their instruction.

    Resolution order for the instruction string:
      1. Per-file lookup in ``instructions_file`` (JSON: ``{episode_id: instruction}``)
      2. CLI ``instruction`` argument.
    """
    overrides = _load_instruction_overrides(instructions_file)
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            _LOGGER.warning("Rollout root not found: %s", root)
            continue
        for path in sorted(root.rglob("*.mp4")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            episode_id = _episode_id_for(path, root)
            yield RolloutVideo(
                episode_id=episode_id,
                instruction=overrides.get(episode_id, instruction),
                video_path=path,
            )


def _load_instruction_overrides(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    if not path.exists():
        _LOGGER.warning("Instructions file not found: %s", path)
        return {}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Instructions file {path} must be a JSON object")
    return {str(k): str(v) for k, v in payload.items()}


def _episode_id_for(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path.name
    return str(rel.with_suffix(""))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.log_level)

    if args.config_preview:
        _print_config(args)
        return 0
    if not args.instruction.strip() and not args.instructions_file:
        _LOGGER.error("Either --instruction or --instructions-file must be provided")
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    service = _build_service(args)
    rollouts = list(
        discover_rollouts(
            roots=[Path(p) for p in args.rollout_root],
            instruction=args.instruction,
            instructions_file=Path(args.instructions_file) if args.instructions_file else None,
        ),
    )
    if args.limit is not None:
        rollouts = rollouts[: args.limit]
    if not rollouts:
        _LOGGER.error("No rollout MP4s discovered under: %s", args.rollout_root)
        return 1
    _LOGGER.info("Discovered %d rollout videos", len(rollouts))

    n_ok = 0
    n_fail = 0
    started = time.time()
    with output_path.open("w") as fh:
        for rollout in rollouts:
            try:
                if args.dry_run:
                    payload = {
                        "episode_id": rollout.episode_id,
                        "instruction": rollout.instruction,
                        "video_path": str(rollout.video_path),
                        "dry_run": True,
                    }
                else:
                    result = service.judge_episode(
                        episode_id=rollout.episode_id,
                        instruction=rollout.instruction,
                        video_paths={"primary": rollout.video_path},
                        force=args.force,
                    )
                    payload = result.to_dict()
                    payload["video_path"] = str(rollout.video_path)
            except Exception:
                _LOGGER.exception("Rollout %s failed; skipping", rollout.episode_id)
                n_fail += 1
                continue
            fh.write(json.dumps(payload) + "\n")
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
        prog="vlm_judge.policy_eval",
        description="VLM-as-judge over policy-rollout MP4 directories",
    )
    parser.add_argument(
        "--rollout-root",
        nargs="+",
        required=True,
        help="One or more directories containing rollout MP4s (recursive)",
    )
    parser.add_argument(
        "--instruction",
        default="",
        help="Default task instruction applied to each rollout",
    )
    parser.add_argument(
        "--instructions-file",
        default=None,
        help="Optional JSON object mapping episode_id -> instruction",
    )
    parser.add_argument("--output", required=True, help="Output JSONL file")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of rollouts")
    parser.add_argument(
        "--backend",
        choices=("qwen3-vl", "openai-compat", "echo"),
        default="qwen3-vl",
    )
    parser.add_argument("--model-id", default="Qwen/Qwen3-VL-4B-Instruct")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--n-frames", type=int, default=12)
    parser.add_argument("--cache-dir", default="outputs/vlm-judge/cache")
    parser.add_argument("--force", action="store_true", help="Ignore the cache")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and pair rollouts but skip inference",
    )
    parser.add_argument("--config-preview", action="store_true")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args(argv)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _print_config(args: argparse.Namespace) -> None:
    cfg = {k: v for k, v in vars(args).items() if k != "config_preview"}
    print(json.dumps(cfg, indent=2, default=str))


def _build_service(args: argparse.Namespace) -> JudgeService:
    backend = BackendConfig(
        kind=args.backend,
        model_id=args.model_id,
        base_url=args.base_url,
        api_key=args.api_key,
    )
    frames = FrameConfig(n_frames=args.n_frames)
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    return JudgeService(ServiceConfig(backend=backend, frames=frames, cache_dir=cache_dir))


if __name__ == "__main__":
    sys.exit(main())
