"""Closed-loop chunk execution with immutable evidence and distinct task/process outcomes."""

from __future__ import annotations

import hashlib
import re
import shutil
import time
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from .artifacts import inventory, write_json
from .config import EvaluationConfig, canonical, finite_array, require
from .interfaces import Frame, Simulation


class EpisodeVideo:
    """Stream per-camera MP4s and verify their decoded frame count, cadence, and dimensions."""

    def __init__(self, root: Path, config: EvaluationConfig) -> None:
        import av

        self.config = config
        self.root = root
        self.count = 0
        self.outputs = {}
        try:
            for camera, (height, width, _) in config.image_shapes.items():
                path = root / f"{camera}.mp4"
                require(not path.exists(), "Refusing to overwrite camera evidence")
                container = av.open(str(path), "w", format="mp4")
                stream = container.add_stream("libx264", rate=config.control_hz)
                self.outputs[camera] = (container, stream, hashlib.sha256())
                stream.width, stream.height = width, height
                stream.pix_fmt = "yuv420p"
                stream.time_base = Fraction(1, config.control_hz)
                stream.options = {"crf": "18", "preset": "medium"}
        except Exception:
            for container, _, _ in self.outputs.values():
                container.close()
            raise

    def append(self, images: dict[str, np.ndarray]) -> None:
        """Write the synchronized post-action camera frames."""
        import av

        for camera, (container, stream, hasher) in self.outputs.items():
            image = images[camera]
            hasher.update(image.tobytes(order="C"))
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts, frame.time_base = self.count, Fraction(1, self.config.control_hz)
            for packet in stream.encode(frame):
                container.mux(packet)
        self.count += 1

    def close(self) -> list[dict[str, Any]]:
        """Finalize every stream; decoding proves serialization, not visual task quality."""
        import av

        failures = []
        for camera, (container, stream, _) in self.outputs.items():
            try:
                for packet in stream.encode():
                    container.mux(packet)
            except Exception as error:
                failures.append(f"{camera}: {error}")
            finally:
                container.close()
        require(not failures, f"Video finalization failed: {failures}")
        if self.count == 0:
            return []
        result = []
        for camera, (_, _, hasher) in self.outputs.items():
            with av.open(str(self.root / f"{camera}.mp4")) as container:
                require(len(container.streams.video) == 1, "Expected one camera stream")
                stream = container.streams.video[0]
                require(stream.average_rate == Fraction(self.config.control_hz), "Encoded camera FPS differs")
                count = 0
                for frame in container.decode(stream):
                    require(
                        not frame.is_corrupt and frame.pts is not None and frame.time_base is not None,
                        "Corrupt camera frame or missing timestamp",
                    )
                    require(
                        abs(float(frame.pts * frame.time_base) - count / self.config.control_hz) < 1e-6,
                        "Camera timestamps differ from control grid",
                    )
                    require(
                        frame.to_ndarray(format="rgb24").shape == self.config.image_shapes[camera],
                        "Camera dimensions differ from configuration",
                    )
                    count += 1
                require(count == self.count, "Encoded camera count differs from stepped frames")
            result.append(
                {
                    "camera": camera,
                    "path": f"{camera}.mp4",
                    "frame_count": count,
                    "source_rgb_sha256": hasher.hexdigest(),
                    "decoded_validation": "pass",
                    "visual_review": "not_checked",
                    "pixel_fidelity": "not_checked",
                }
            )
        return result


def _frame(config: EvaluationConfig, frame: Frame) -> Frame:
    state, images = config.validate_observation(frame.state, frame.images)
    evidence = {}
    require(isinstance(frame.evidence, dict), "Task evidence must be a mapping")
    for key, value in frame.evidence.items():
        require(isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_]*", key) is not None, "Invalid evidence key")
        array = np.asarray(value)
        require(
            array.ndim <= 2 and array.size <= 4096 and array.dtype.kind in "fiu" and bool(np.isfinite(array).all()),
            f"Invalid task evidence: {key}",
        )
        evidence[key] = np.array(array, dtype=np.float64, copy=True)
    return Frame(state, images, evidence)


def run_episode(
    config: EvaluationConfig, simulation: Simulation, policy: Any, root: Path, split: str, seed: int
) -> dict[str, Any]:
    """Run a bounded episode; preserve completed steps on any failure and never reuse stale chunks."""
    root.mkdir(exist_ok=False)
    requests = root / "requests"
    requests.mkdir()
    rows, evidence_rows = [], []
    timings = []
    videos = None
    initial = None
    errors = []
    started = time.perf_counter()
    try:
        initial = current = _frame(config, simulation.reset(seed))
        limits = finite_array(simulation.action_limits, (len(config.channels), 2), "Joint limits")
        require(bool(np.all(limits[:, 0] < limits[:, 1])), "Invalid joint limits")
        initial_command = finite_array(simulation.initial_command, (len(config.channels),), "Initial target")
        write_json(
            root / "initial.json",
            {
                "seed": seed,
                "split": split,
                "state": current.state.tolist(),
                "initial_command": initial_command.tolist(),
                "action_limits": limits.tolist(),
                "evidence": {key: value.tolist() for key, value in current.evidence.items()},
            },
        )
        videos = EpisodeVideo(root, config)
        chunk = None
        cursor = 0
        for step in range(config.max_steps):
            if chunk is None or cursor == config.execution_horizon:
                request_index = len(timings)
                payload = {"state": current.state, **{f"image.{key}": value for key, value in current.images.items()}}
                with (requests / f"{request_index:06d}.input.npz").open("xb") as stream:
                    np.savez_compressed(stream, **payload)
                infer_started = time.perf_counter()
                response = policy.infer(
                    current.state.copy(),
                    {key: value.copy() for key, value in current.images.items()},
                    seed=seed,
                    reset=step == 0,
                )
                elapsed = time.perf_counter() - infer_started
                require(elapsed <= config.timeout_seconds, "Inference exceeded the evaluation deadline")
                chunk = finite_array(
                    response["actions"], (config.policy.action_horizon, len(config.channels)), "Action chunk"
                )
                model_actions = np.asarray(response["model_actions"])
                require(
                    np.array_equal(chunk, config.decode_actions(model_actions, current.state)),
                    "Model output and decoded action chunk differ",
                )
                canonical({key: value for key, value in response.items() if key not in {"actions", "model_actions"}})
                cursor = 0
                timings.append(elapsed * 1000)
                with (requests / f"{request_index:06d}.output.npz").open("xb") as stream:
                    np.savez_compressed(stream, actions=chunk, model_actions=model_actions)
                write_json(
                    requests / f"{request_index:06d}.json",
                    {key: value for key, value in response.items() if key not in {"actions", "model_actions"}},
                )
            raw = chunk[cursor].copy()
            cursor += 1
            action = np.clip(raw, limits[:, 0], limits[:, 1])
            if config.joint_limit_behavior == "reject":
                require(np.array_equal(raw, action), "Policy target exceeds joint limits; action not applied")
            next_frame = _frame(config, simulation.step(action.copy()))
            require(
                set(next_frame.evidence) == set(initial.evidence)
                and all(next_frame.evidence[key].shape == initial.evidence[key].shape for key in initial.evidence),
                "Task evidence schema changed during the episode",
            )
            rows.append((current.state, next_frame.state, raw, action))
            evidence_rows.append(next_frame.evidence)
            videos.append(next_frame.images)
            current = next_frame
            if (step + 1) % config.control_hz == 0:
                write_json(
                    root / "progress.json",
                    {"step": step + 1, "max_steps": config.max_steps, "seed": seed},
                    replace=True,
                )
    except (Exception, KeyboardInterrupt) as error:
        errors.append(f"{type(error).__name__}: {error}")
        try:
            simulation.hold()
        except Exception as hold_error:
            errors.append(f"hold failed: {hold_error}")
    finally:
        media = []
        if videos is not None:
            try:
                media = videos.close()
            except Exception as video_error:
                errors.append(f"video failed: {video_error}")
    count = len(rows)
    data = {
        name: np.asarray([row[index] for row in rows], dtype=np.float64).reshape(count, len(config.channels))
        for index, name in enumerate(("states", "next_states", "raw_actions", "actions"))
    }
    data.update(
        {
            f"evidence.{key}": np.asarray([frame[key] for frame in evidence_rows], dtype=np.float64)
            for key in (initial.evidence if initial is not None else {})
        }
    )
    data.update(
        frame_index=np.arange(count), timestamp=np.arange(count) / config.control_hz, inference_ms=np.asarray(timings)
    )
    with (root / "trajectory.npz").open("xb") as stream:
        np.savez_compressed(stream, **data)
    score = {"success": False}
    if count and initial is not None:
        try:
            score = simulation.score(data, initial)
            require(type(score.get("success")) is bool, "Task scorer must return an explicit success boolean")
            require("status" not in score, "Task scores must not replace process status")
            canonical(score)
        except Exception as score_error:
            errors.append(f"scoring failed: {score_error}")
            score = {"success": False}
    result = {
        "seed": seed,
        "split": split,
        "split_provenance": "caller_declared_not_independently_verified",
        "status": "failed" if errors else "complete",
        "errors": errors,
        "steps": count,
        "simulated_seconds": count / config.control_hz,
        "wall_seconds": time.perf_counter() - started,
        "task": score,
        "success": bool(not errors and count == config.max_steps and score["success"]),
        "inference_calls": len(timings),
        "mean_inference_ms": float(np.mean(timings)) if timings else None,
        "clipped_actions": sum(not np.array_equal(row[2], row[3]) for row in rows),
        "media": media,
        "visual_review": "not_checked",
        "files": inventory(root),
    }
    write_json(root / "result.json", result)
    return result


def run_evaluation(
    config: EvaluationConfig,
    simulation: Simulation,
    policy: Any,
    output: Path,
    *,
    validate_inputs: Callable[[], None] | None = None,
    publish_complete: bool = True,
) -> dict[str, Any]:
    """Run every declared episode once; task failures count, operational failures stop the batch."""
    require(output.is_dir() and not any(output.iterdir()), "Evaluation output must be a fresh empty directory")
    require(shutil.disk_usage(output).free >= config.minimum_free_gib * 1024**3, "Insufficient evaluation disk space")
    write_json(output / "config.json", config.source)
    write_json(
        output / "provenance.json",
        {
            "config_sha256": config.sha256,
            "policy": policy.metadata,
            "simulation": simulation.provenance,
            "policy_inputs": ["measured_joint_state", "declared_rgb_cameras", "instruction"],
            "state_alignment": "observation before action; task evidence and video after action",
            "execution_mode": "synchronous_non_realtime_chunked",
            "oracle_policy_inputs": False,
            "demonstration_replay": False,
            "connected_capture_eligible": False,
            "physical_validation": "not_performed",
        },
    )
    reports = []
    for index, (split, seed) in enumerate(config.episodes):
        require(
            shutil.disk_usage(output).free >= config.minimum_free_gib * 1024**3, "Evaluation disk reserve exhausted"
        )
        episode_root = output / f"episode_{index:06d}_seed_{seed}"
        report = run_episode(config, simulation, policy, episode_root, split, seed)
        reports.append({**report, "path": episode_root.name})
        write_json(
            output / "progress.json",
            {"completed_episodes": len(reports), "planned_episodes": len(config.episodes)},
            replace=True,
        )
        if report["status"] != "complete":
            break
    complete = len(reports) == len(config.episodes) and all(report["status"] == "complete" for report in reports)
    result = {
        "schema_version": 1,
        "status": "complete" if complete else "failed",
        "config_sha256": config.sha256,
        "planned_episode_count": len(config.episodes),
        "episode_count": len(reports),
        "success_count": sum(report["success"] for report in reports),
        "episodes": reports,
        "not_attempted": [{"split": split, "seed": seed} for split, seed in config.episodes[len(reports) :]],
        "unattempted_seeds": [seed for _, seed in config.episodes[len(reports) :]],
        "by_split": {
            split: {
                "episodes": sum(report["split"] == split for report in reports),
                "successes": sum(report["split"] == split and report["success"] for report in reports),
            }
            for split in sorted({split for split, _ in config.episodes})
        },
        "split_provenance": "caller_declared_not_independently_verified",
        "visual_review": "not_checked",
        "physical_validation": "not_performed",
        "files": inventory(output),
    }
    if validate_inputs is not None:
        validate_inputs()
    if not complete or publish_complete:
        write_json(output / ("evaluation.json" if complete else "failure.json"), result)
    return result
