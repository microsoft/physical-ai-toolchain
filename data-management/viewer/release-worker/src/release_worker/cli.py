"""File-based command interface for isolated LeRobot release processing."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np

from .lerobot import LeRobotReleaseWorker, WorkerEpisode, WorkerEpisodeReference


def create_parser() -> argparse.ArgumentParser:
    """Create the worker command parser."""
    parser = argparse.ArgumentParser(description="Process a dataviewer release request")
    parser.add_argument("request_path", type=Path)
    return parser


def _decode_episodes(request: dict[str, Any], request_root: Path) -> tuple[WorkerEpisode, ...]:
    episodes = []
    for encoded_episode in request["episodes"]:
        frames = []
        for encoded_frame in encoded_episode["frames"]:
            frame = {}
            for encoded_value in encoded_frame["values"]:
                relative_path = PurePosixPath(encoded_value["path"])
                if relative_path.is_absolute() or ".." in relative_path.parts:
                    raise ValueError("Frame value path must be relative to the request directory")
                frame[encoded_value["feature"]] = np.load(request_root / Path(relative_path), allow_pickle=False)
            frames.append(frame)
        episodes.append(
            WorkerEpisode(
                source_episode_index=int(encoded_episode["source_episode_index"]),
                decision_id=encoded_episode["decision_id"],
                task=encoded_episode["task"],
                frames=tuple(frames),
            )
        )
    return tuple(episodes)


def _decode_episode_references(request: dict[str, Any]) -> tuple[WorkerEpisodeReference, ...]:
    return tuple(
        WorkerEpisodeReference(
            source_episode_index=int(episode["source_episode_index"]),
            decision_id=episode["decision_id"],
            task=episode["task"],
        )
        for episode in request["episodes"]
    )


def _process(request_path: Path) -> dict[str, Any]:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    worker = LeRobotReleaseWorker()
    if request["operation"] == "write-v3":
        result = worker.write_v3(
            episodes=_decode_episodes(request, request_path.parent),
            target_root=Path(request["target_root"]),
            repo_id=request["repo_id"],
            fps=int(request["fps"]),
            features=request["features"],
        )
        return asdict(result)
    if request["operation"] == "copy-v3":
        result = worker.copy_v3(
            source_root=Path(request["source_root"]),
            source_repo_id=request["source_repo_id"],
            episodes=_decode_episode_references(request),
            target_root=Path(request["target_root"]),
            repo_id=request["repo_id"],
            fps=int(request["fps"]),
            features=request["features"],
        )
        return asdict(result)
    if request["operation"] == "convert-v21":
        converted_root = worker.convert_v21(
            source_root=Path(request["source_root"]),
            workspace_root=Path(request["workspace_root"]),
            repo_id=request["repo_id"],
        )
        return {"converted_root": str(converted_root)}
    raise ValueError(f"Unsupported release operation: {request['operation']}")


def main() -> int:
    """Process one request and persist a structured response."""
    arguments = create_parser().parse_args()
    response_path = arguments.request_path.with_name("response.json")
    try:
        response = _process(arguments.request_path)
    except Exception as error:
        response = {"error": {"type": type(error).__name__, "message": str(error)}}
        response_path.write_text(json.dumps(response, separators=(",", ":")), encoding="utf-8")
        return 1
    response_path.write_text(json.dumps(response, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
