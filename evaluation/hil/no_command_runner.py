"""Deterministic UR10E-shaped HiL dry run with no command-capable transport."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_JOINT_ORDER = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)
_REJECTION_CODE = "NO_COMMAND_TRANSPORT"


class NoCommandTransportError(RuntimeError):
    """Raised whenever an action reaches the independent no-command boundary."""


@dataclass(frozen=True, slots=True)
class Ur10eObservation:
    """One six-axis observation sampled from a deterministic fixture."""

    sequence: int
    timestamp_ns: int
    joint_positions_rad: tuple[float, float, float, float, float, float]
    joint_velocities_rad_s: tuple[float, float, float, float, float, float]


class NoCommandUr10eAdapter:
    """UR10E-shaped adapter that intentionally contains no robot transport."""

    adapter_id = "ur10e-no-command/v1"
    command_transport = "none"

    def __init__(self, observations: list[Ur10eObservation]) -> None:
        self._observations = observations

    def observations(self) -> list[Ur10eObservation]:
        """Return deterministic fixture observations."""
        return self._observations.copy()

    def apply_action(self, _action: tuple[float, float, float, float, float, float]) -> None:
        """Reject every action because no command transport exists."""
        raise NoCommandTransportError(_REJECTION_CODE)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1 or config.get("kind") != "ur10e-no-command-dry-run":
        raise ValueError("unsupported HiL configuration schema")
    robot = config.get("robot", {})
    if robot.get("model") != "UR10E" or robot.get("joint_order") != list(_JOINT_ORDER):
        raise ValueError("configuration must use the canonical UR10E joint order")
    forbidden_values = (
        robot.get("command_transport"),
        robot.get("command_endpoint"),
        robot.get("device_paths"),
        robot.get("robot_network_cidrs"),
    )
    if forbidden_values != ("none", None, [], []):
        raise ValueError("dry run must not declare a command transport, endpoint, device, or robot network")
    safety = config.get("safety", {})
    execution = config.get("execution", {})
    if (
        safety.get("allow_motion") is not False
        or safety.get("allow_command_transport") is not False
        or safety.get("require_negative_command_probe") is not True
        or execution.get("mode") != "dry-run"
    ):
        raise ValueError("dry run requires motion and command transport disabled plus the negative command probe")
    if int(execution.get("max_steps", 0)) <= 0 or int(execution.get("period_ms", 0)) <= 0:
        raise ValueError("max_steps and period_ms must be positive")
    return config


def _load_observations(path: Path, max_steps: int) -> list[Ur10eObservation]:
    observations = []
    previous_sequence = -1
    previous_timestamp = -1
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        value = json.loads(raw_line)
        positions = tuple(float(item) for item in value["joint_positions_rad"])
        velocities = tuple(float(item) for item in value["joint_velocities_rad_s"])
        if len(positions) != len(_JOINT_ORDER) or len(velocities) != len(_JOINT_ORDER):
            raise ValueError("each observation must contain six positions and velocities")
        if not all(math.isfinite(item) for item in positions + velocities):
            raise ValueError("observation values must be finite")
        sequence = int(value["sequence"])
        timestamp_ns = int(value["timestamp_ns"])
        if sequence <= previous_sequence or timestamp_ns <= previous_timestamp:
            raise ValueError("observation sequence and timestamp values must increase monotonically")
        observations.append(
            Ur10eObservation(
                sequence=sequence,
                timestamp_ns=timestamp_ns,
                joint_positions_rad=positions,  # type: ignore[arg-type]
                joint_velocities_rad_s=velocities,  # type: ignore[arg-type]
            )
        )
        previous_sequence = sequence
        previous_timestamp = timestamp_ns
        if len(observations) == max_steps:
            break
    if len(observations) != max_steps:
        raise ValueError(f"observation fixture must provide exactly {max_steps} steps")
    return observations


def _propose_action(observation: Ur10eObservation) -> tuple[float, float, float, float, float, float]:
    proposed = tuple(round(-0.01 * position, 8) for position in observation.joint_positions_rad)
    return proposed  # type: ignore[return-value]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, sort_keys=True) + "\n")


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    """Run the deterministic evaluation and return its summary."""
    config = _load_config(config_path)
    fixture_path = (config_path.parent / config["observations"]["fixture"]).resolve()
    max_steps = int(config["execution"]["max_steps"])
    period_ms = int(config["execution"]["period_ms"])
    observations = _load_observations(fixture_path, max_steps)
    adapter = NoCommandUr10eAdapter(observations)

    output_dir.mkdir(parents=True, exist_ok=True)
    observed_records = []
    action_records = []
    safety_events = []
    latency_ms = []
    deadline_misses = 0

    started_at = time.time_ns()
    for observation in adapter.observations():
        step_started = time.perf_counter_ns()
        action = _propose_action(observation)
        try:
            adapter.apply_action(action)
        except NoCommandTransportError as error:
            if str(error) != _REJECTION_CODE:
                raise
            rejected = True
        else:
            rejected = False

        elapsed_ms = (time.perf_counter_ns() - step_started) / 1_000_000
        latency_ms.append(elapsed_ms)
        if elapsed_ms > period_ms:
            deadline_misses += 1

        observed_records.append(
            {
                "sequence": observation.sequence,
                "timestamp_ns": observation.timestamp_ns,
                "joint_positions_rad": observation.joint_positions_rad,
                "joint_velocities_rad_s": observation.joint_velocities_rad_s,
            }
        )
        action_records.append(
            {
                "sequence": observation.sequence,
                "proposed_action_rad": action,
                "applied": False,
                "command_transport": adapter.command_transport,
                "command_probe": "rejected" if rejected else "unexpectedly-accepted",
                "rejection_code": _REJECTION_CODE if rejected else None,
                "latency_ms": elapsed_ms,
            }
        )
        safety_events.append(
            {
                "sequence": observation.sequence,
                "event": "command-rejected",
                "code": _REJECTION_CODE,
                "expected": True,
            }
        )
        if not rejected:
            raise RuntimeError("negative command probe unexpectedly crossed the no-command boundary")

    finished_at = time.time_ns()
    if deadline_misses:
        raise RuntimeError(f"dry run missed {deadline_misses} execution deadlines")
    observation_path = output_dir / "observations.jsonl"
    action_path = output_dir / "proposed-actions.jsonl"
    safety_path = output_dir / "safety-events.jsonl"
    _write_jsonl(observation_path, observed_records)
    _write_jsonl(action_path, action_records)
    _write_jsonl(safety_path, safety_events)

    result = {
        "schema_version": 1,
        "kind": "ur10e-no-command-result",
        "status": "passed",
        "adapter": adapter.adapter_id,
        "robot_model": "UR10E",
        "joint_order": _JOINT_ORDER,
        "command_transport": adapter.command_transport,
        "started_at_ns": started_at,
        "finished_at_ns": finished_at,
        "steps": len(observations),
        "proposed_actions": len(action_records),
        "applied_actions": 0,
        "negative_command_probe": "passed",
        "rejection_code": _REJECTION_CODE,
        "period_ms": period_ms,
        "deadline_misses": deadline_misses,
        "latency_ms": {
            "minimum": min(latency_ms),
            "mean": statistics.fmean(latency_ms),
            "maximum": max(latency_ms),
        },
        "config_sha256": _sha256(config_path),
        "fixture_sha256": _sha256(fixture_path),
        "policy_identity": config["policy"],
    }
    summary_path = output_dir / "summary.json"
    _write_json(summary_path, result)

    manifest = []
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest.append({"path": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    _write_json(output_dir / "manifest.json", {"schema_version": 1, "files": manifest})
    return result


def main() -> int:
    """Parse CLI arguments and execute the no-command evaluation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.config.resolve(), args.output_dir.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
