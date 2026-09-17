"""CPU-only VLA execution contracts with synthetic adapters and real MP4 evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import av
import numpy as np
import pytest
from sil.vla import runner
from sil.vla.artifacts import file_identity, unchanged
from sil.vla.backends import Backend
from sil.vla.config import EvaluationConfig, load_config
from sil.vla.interfaces import Frame, Simulation
from sil.vla.runner import EpisodeVideo, run_episode, run_evaluation
from sil.vla.transport import PolicySession

_IMAGE_SHAPE = (32, 48, 3)
_EPISODES = (("test", 29), ("test", 3), ("validation", 17))
_ACTION_INDICES = (6, 4, 2, 0, 1, 3, 5)
_ACTION_SCALE = (1.0, -1.0, 0.5, 2.0, 1.0, 0.25, 1.0)
_ACTION_OFFSET = (0.1, -0.1, 0.2, 0.0, -0.2, 0.3, -0.3)


def _make_config(tmp_path: Path, *, behavior: str = "reject") -> EvaluationConfig:
    example = Path(__file__).resolve().parents[3] / "examples/vla/rho-ur10e.example.json"
    source = json.loads(example.read_text(encoding="utf-8"))
    source["image_shapes"] = {camera: list(_IMAGE_SHAPE) for camera in ("d435", "d405")}
    source["policy"].update(
        checkpoint="checkpoint",
        checkpoint_sha256="1" * 64,
        action_horizon=8,
        action_mapping={
            "indices": list(_ACTION_INDICES),
            "scale": list(_ACTION_SCALE),
            "offset": list(_ACTION_OFFSET),
        },
    )
    source["task"].update(
        producer_root="synthetic-owner",
        expert_config="synthetic-task.json",
        stage="synthetic-assets/stage.usda",
        reference_root="synthetic-assets",
        runtime_assets=[],
    )
    source["evaluation"] = {
        "control_hz": 30,
        "execution_horizon": 3,
        "seeds": {"test": [29, 3], "validation": [17]},
        "max_steps": 5,
        "timeout_seconds": 10,
        "minimum_free_gib": 0,
        "joint_limit_behavior": behavior,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(source, allow_nan=False), encoding="utf-8")
    return load_config(path)


def _rgb(seed: int, step: int, camera: int) -> np.ndarray:
    y, x = np.indices(_IMAGE_SHAPE[:2])
    base = np.array([135, 45, 20] if camera == 0 else [20, 45, 135])
    return (base + ((x + 2 * y + seed) % 16)[..., None] + step * 9).astype(np.uint8)


def _measured_frame(seed: int, step: int) -> Frame:
    return Frame(
        state=np.arange(7, dtype=np.float64) / 8 + seed / 1000 + step / 50,
        images={camera: _rgb(seed, step, index) for index, camera in enumerate(("d435", "d405"))},
        evidence={
            "gear_pose": np.array([10 + seed, step / 10, 0.2, 1, 0, 0, 0], dtype=np.float64),
            "contact_force": np.array([step + 0.5, step + 0.75]),
            "oracle_phase": np.array([step], dtype=np.float64),
        },
    )


def _mapped(native: np.ndarray) -> np.ndarray:
    return native[:, list(_ACTION_INDICES)] * np.asarray(_ACTION_SCALE) + np.asarray(_ACTION_OFFSET)


def _read_npz(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as archive:
        return {key: archive[key].copy() for key in archive.files}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    json.dumps(value, allow_nan=False)
    return value


def _assert_inventory(root: Path, records: dict[str, Any], *, excluded: set[str]) -> None:
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    assert set(records) == actual - excluded
    for relative, record in records.items():
        payload = (root / relative).read_bytes()
        assert record == {"sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}


def _assert_media(root: Path, result: dict[str, Any], config: EvaluationConfig, *, seed: int) -> None:
    assert {record["camera"] for record in result["media"]} == set(config.image_shapes)
    hashes = set()
    for record in result["media"]:
        camera = record["camera"]
        source = [_measured_frame(seed, step).images[camera] for step in range(1, result["steps"] + 1)]
        source_hash = hashlib.sha256(b"".join(image.tobytes(order="C") for image in source)).hexdigest()
        assert record["source_rgb_sha256"] == source_hash
        assert record["frame_count"] == result["steps"]
        assert record["decoded_validation"] == "pass"
        assert record["visual_review"] == record["pixel_fidelity"] == "not_checked"
        hashes.add(source_hash)
        with av.open(str(root / record["path"])) as container:
            assert len(container.streams) == len(container.streams.video) == 1
            stream = container.streams.video[0]
            assert stream.average_rate == Fraction(config.control_hz)
            decoded = list(container.decode(stream))
            assert len(decoded) == len(source)
            for index, (frame, expected) in enumerate(zip(decoded, source, strict=True)):
                assert not frame.is_corrupt
                assert frame.pts is not None and frame.time_base is not None
                assert frame.pts * frame.time_base == Fraction(index, config.control_hz)
                actual = frame.to_ndarray(format="rgb24")
                assert actual.dtype == np.uint8 and actual.shape == _IMAGE_SHAPE
                assert float(np.abs(actual.astype(np.int16) - expected.astype(np.int16)).mean()) < 5
    assert len(hashes) == len(config.image_shapes)


class _SyntheticSimulation(Simulation):
    """Return measured states independent of commands and keep oracle data task-private."""

    frame_transform: Callable[[Frame, int], Frame] | None
    reset_error: BaseException | None
    step_error: BaseException | None
    hold_error: Exception | None
    score_error: Exception | None

    def __init__(self) -> None:
        self.provenance = {"producer": "synthetic_cpu", "native_validation": "not_performed"}
        self.action_limits = np.tile([-2.0, 2.0], (7, 1))
        self.initial_command = np.linspace(-0.1, 0.1, 7)
        self.reset_seeds = []
        self.applied_actions = []
        self.score_calls = []
        self.step_calls = 0
        self.hold_calls = 0
        self.close_calls = 0
        self.seed = 0
        self.step_index = 0
        self.fail_step = 2
        self.frame_transform = None
        self.reset_error = self.step_error = self.hold_error = self.score_error = None
        self.score_result = {"success": True, "producer": "synthetic_cpu"}

    def _observe(self) -> Frame:
        frame = _measured_frame(self.seed, self.step_index)
        return frame if self.frame_transform is None else self.frame_transform(frame, self.step_index)

    def reset(self, seed: int) -> Frame:
        self.reset_seeds.append(seed)
        if self.reset_error is not None:
            raise self.reset_error
        self.seed, self.step_index = seed, 0
        return self._observe()

    def step(self, action: np.ndarray) -> Frame:
        self.step_calls += 1
        if self.step_error is not None and self.step_index + 1 == self.fail_step:
            raise self.step_error
        self.applied_actions.append(action.copy())
        self.step_index += 1
        return self._observe()

    def score(self, data: dict[str, np.ndarray], initial: Frame) -> dict[str, Any]:
        self.score_calls.append((data, initial))
        if self.score_error is not None:
            raise self.score_error
        return dict(self.score_result)

    def hold(self) -> None:
        self.hold_calls += 1
        if self.hold_error is not None:
            raise self.hold_error

    def close(self) -> None:
        self.close_calls += 1


class _SyntheticBackend(Backend):
    """Produce request-specific chunks without loading weights or maintaining an action queue."""

    on_infer: Callable[[int], None] | None

    def __init__(self) -> None:
        self.provenance = {"producer": "synthetic_cpu", "device": "cpu", "action_queue": "none"}
        self.calls = []
        self.outputs = []
        self.replies = {}
        self.on_infer = None

    def infer(self, state: np.ndarray, images: dict[str, np.ndarray], *, seed: int, reset: bool) -> np.ndarray:
        request = len(self.calls)
        self.calls.append(
            {
                "state": state.copy(),
                "images": {key: value.copy() for key, value in images.items()},
                "seed": seed,
                "reset": reset,
            }
        )
        if self.on_infer is not None:
            self.on_infer(request)
        native = seed / 1000 + (request + 1) / 20 + np.arange(8)[:, None] / 100 + np.arange(7)[None, :] / 1000
        value = self.replies.get(request, native)
        if isinstance(value, BaseException):
            raise value
        self.outputs.append(np.array(value, copy=True))
        return value


class _InProcessPolicy:
    """Use the real session contract with no socket, model runtime, or native simulator."""

    response_transform: Callable[[dict[str, Any]], dict[str, Any]] | None

    def __init__(self, config: EvaluationConfig, backend: Backend) -> None:
        self.session = PolicySession(config, backend)
        self.metadata = self.session.hello()
        self.requests = []
        self.mutate_inputs = False
        self.response_transform = None

    def infer(self, state: np.ndarray, images: dict[str, np.ndarray], *, seed: int, reset: bool) -> dict[str, Any]:
        request = {
            "session_id": self.session.session_id,
            "request_id": len(self.requests),
            "config_sha256": self.session.config.sha256,
            "seed": seed,
            "reset": reset,
            "state": state.copy(),
            "images": {key: value.copy() for key, value in images.items()},
        }
        self.requests.append(request)
        response = self.session.infer(request)
        if self.mutate_inputs:
            state.fill(-999)
            for image in images.values():
                image.fill(0)
        return response if self.response_transform is None else self.response_transform(response)


@pytest.fixture
def config(tmp_path: Path, request: pytest.FixtureRequest) -> EvaluationConfig:
    return _make_config(tmp_path, behavior=getattr(request, "param", "reject"))


@pytest.fixture
def simulation() -> _SyntheticSimulation:
    return _SyntheticSimulation()


@pytest.fixture
def backend() -> _SyntheticBackend:
    return _SyntheticBackend()


@pytest.fixture
def policy(config: EvaluationConfig, backend: _SyntheticBackend) -> _InProcessPolicy:
    return _InProcessPolicy(config, backend)


@pytest.fixture
def episode(
    tmp_path: Path, config: EvaluationConfig, simulation: _SyntheticSimulation, policy: _InProcessPolicy
) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "episode"
    split, seed = config.episodes[0]
    return root, run_episode(config, simulation, policy, root, split, seed)


class TestEpisodeExecution:
    """Chunk boundaries preserve measurement alignment and exact action provenance."""

    def test_given_two_chunks_when_executed_then_only_declared_prefixes_are_applied(
        self,
        episode: tuple[Path, dict[str, Any]],
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        backend: _SyntheticBackend,
        policy: _InProcessPolicy,
    ) -> None:
        root, result = episode
        data = _read_npz(root / "trajectory.npz")
        expected = np.concatenate((_mapped(backend.outputs[0])[:3], _mapped(backend.outputs[1])[:2]))
        canonical = json.dumps(config.source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

        assert config.sha256 == hashlib.sha256(canonical).hexdigest()
        assert config.policy.action_horizon == 8 and config.execution_horizon == 3
        assert len(config.channels) == 7 and config.max_steps == 5 and config.episodes == _EPISODES
        assert result["status"] == "complete" and result["success"] is True
        assert result["steps"] == 5 and result["inference_calls"] == 2 and result["clipped_actions"] == 0
        assert simulation.hold_calls == simulation.close_calls == 0
        np.testing.assert_array_equal(simulation.applied_actions, expected)
        np.testing.assert_array_equal(data["actions"], expected)
        np.testing.assert_array_equal(data["raw_actions"], expected)
        np.testing.assert_array_equal(data["frame_index"], np.arange(5))
        np.testing.assert_array_equal(data["timestamp"], np.arange(5) / config.control_hz)
        assert data["inference_ms"].shape == (2,) and np.isfinite(data["inference_ms"]).all()
        assert np.all(data["inference_ms"] >= 0)
        for request_index, step in enumerate((0, 3)):
            initial = _measured_frame(29, step)
            call = backend.calls[request_index]
            assert call["seed"] == 29 and call["reset"] is (step == 0)
            np.testing.assert_array_equal(call["state"], initial.state)
            assert set(call["images"]) == {"d435", "d405"}
            inputs = _read_npz(root / "requests" / f"{request_index:06d}.input.npz")
            assert set(inputs) == {"state", "image.d435", "image.d405"}
            np.testing.assert_array_equal(inputs["state"], initial.state)
            for camera in config.image_shapes:
                np.testing.assert_array_equal(inputs[f"image.{camera}"], initial.images[camera])
                np.testing.assert_array_equal(call["images"][camera], initial.images[camera])
            outputs = _read_npz(root / "requests" / f"{request_index:06d}.output.npz")
            assert set(outputs) == {"actions", "model_actions"}
            np.testing.assert_array_equal(outputs["model_actions"], backend.outputs[request_index])
            np.testing.assert_array_equal(outputs["actions"], _mapped(backend.outputs[request_index]))
            metadata = _read_json(root / "requests" / f"{request_index:06d}.json")
            assert metadata["request_id"] == request_index and metadata["seed"] == 29
            assert metadata["config_sha256"] == config.sha256
            assert metadata["session_id"] == policy.session.session_id
        assert all(
            set(request) == {"session_id", "request_id", "config_sha256", "seed", "reset", "state", "images"}
            for request in policy.requests
        )

    def test_given_measured_frames_when_logged_then_states_precede_actions_and_evidence_follows(
        self,
        episode: tuple[Path, dict[str, Any]],
        simulation: _SyntheticSimulation,
    ) -> None:
        root, _ = episode
        data = _read_npz(root / "trajectory.npz")
        initial = _read_json(root / "initial.json")

        np.testing.assert_array_equal(initial["state"], _measured_frame(29, 0).state)
        np.testing.assert_array_equal(initial["initial_command"], simulation.initial_command)
        np.testing.assert_array_equal(initial["action_limits"], simulation.action_limits)
        for step in range(5):
            np.testing.assert_array_equal(data["states"][step], _measured_frame(29, step).state)
            after = _measured_frame(29, step + 1)
            np.testing.assert_array_equal(data["next_states"][step], after.state)
            for key, value in after.evidence.items():
                np.testing.assert_array_equal(data[f"evidence.{key}"][step], value)
        scored, first = simulation.score_calls[0]
        np.testing.assert_array_equal(first.state, _measured_frame(29, 0).state)
        for key in data:
            np.testing.assert_array_equal(scored[key], data[key])
        assert not np.array_equal(data["states"], data["actions"])

    def test_given_two_cameras_when_encoded_then_every_post_action_frame_decodes_on_its_own_stream(
        self,
        episode: tuple[Path, dict[str, Any]],
        config: EvaluationConfig,
    ) -> None:
        root, result = episode

        _assert_media(root, result, config, seed=29)
        _assert_inventory(root, result["files"], excluded={"result.json"})
        assert _read_json(root / "result.json") == result

    def test_given_mutating_policy_when_called_then_original_measurements_remain_unchanged(
        self,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        policy: _InProcessPolicy,
    ) -> None:
        policy.mutate_inputs = True
        root = tmp_path / "mutating"

        result = run_episode(config, simulation, policy, root, "test", 29)

        assert result["status"] == "complete"
        data = _read_npz(root / "trajectory.npz")
        np.testing.assert_array_equal(data["states"], [_measured_frame(29, step).state for step in range(5)])
        _assert_media(root, result, config, seed=29)

    def test_given_out_of_limit_target_when_rejected_then_it_never_reaches_step(
        self,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        backend: _SyntheticBackend,
        policy: _InProcessPolicy,
    ) -> None:
        native = np.zeros((8, 7))
        native[1, 0] = 2
        backend.replies[0] = native
        root = tmp_path / "rejected"

        result = run_episode(config, simulation, policy, root, "test", 29)

        assert result["status"] == "failed" and result["success"] is False
        assert result["steps"] == simulation.step_calls == 1 and simulation.hold_calls == 1
        assert any("action not applied" in error for error in result["errors"])
        np.testing.assert_array_equal(simulation.applied_actions, _mapped(native)[:1])
        np.testing.assert_array_equal(_read_npz(root / "requests/000000.output.npz")["model_actions"], native)
        _assert_media(root, result, config, seed=29)

    @pytest.mark.parametrize("config", ["clip"], indirect=True)
    def test_given_explicit_clipping_when_executed_then_counts_steps_not_channels_or_discarded_tail(
        self,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        backend: _SyntheticBackend,
        policy: _InProcessPolicy,
    ) -> None:
        first, second = np.zeros((8, 7)), np.zeros((8, 7))
        first[1:3, :2], first[7, :], second[0, :2], second[7, :] = 9, 9, -9, 9
        backend.replies.update({0: first, 1: second})
        root = tmp_path / "clipped"

        result = run_episode(config, simulation, policy, root, "test", 29)

        raw = np.concatenate((_mapped(first)[:3], _mapped(second)[:2]))
        executed = np.clip(raw, -2, 2)
        data = _read_npz(root / "trajectory.npz")
        assert result["status"] == "complete" and result["clipped_actions"] == 3
        np.testing.assert_array_equal(data["raw_actions"], raw)
        np.testing.assert_array_equal(data["actions"], executed)
        np.testing.assert_array_equal(simulation.applied_actions, executed)
        for index, native in enumerate((first, second)):
            logged = _read_npz(root / "requests" / f"{index:06d}.output.npz")
            np.testing.assert_array_equal(logged["model_actions"], native)
            np.testing.assert_array_equal(logged["actions"], _mapped(native))


class TestOperationalFailures:
    """Failed requests stop execution and retain only validated completed steps."""

    @pytest.mark.parametrize(
        "invalid",
        [
            pytest.param(np.empty((0, 7)), id="blank-chunk"),
            pytest.param(np.empty((8, 0)), id="blank-channels"),
            pytest.param(np.zeros((7, 7)), id="short-horizon"),
            pytest.param(np.zeros((9, 7)), id="long-horizon"),
            pytest.param(np.zeros((8, 6)), id="missing-joint"),
            pytest.param(np.zeros(7), id="vector"),
            pytest.param(np.zeros((1, 8, 7)), id="batched"),
            pytest.param(np.ones((8, 7), dtype=bool), id="boolean"),
            pytest.param(np.full((8, 7), "0"), id="text"),
            pytest.param(np.zeros((8, 7), dtype=object), id="object"),
            pytest.param(np.full((8, 7), np.nan), id="nan"),
            pytest.param(np.full((8, 7), np.inf), id="infinity"),
            pytest.param(np.column_stack((np.zeros((8, 7)), np.full(8, np.nan))), id="unused-channel-nan"),
        ],
    )
    def test_given_invalid_refill_when_requested_then_no_tail_actions_survive(
        self,
        invalid: np.ndarray,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        backend: _SyntheticBackend,
        policy: _InProcessPolicy,
    ) -> None:
        backend.replies[1] = invalid
        root = tmp_path / "bad-refill"

        result = run_episode(config, simulation, policy, root, "test", 29)

        assert result["status"] == "failed" and result["success"] is False and result["errors"]
        assert result["steps"] == simulation.step_calls == 3 and simulation.hold_calls == 1
        assert len(backend.calls) == 2 and policy.session.closed
        assert (root / "requests/000001.input.npz").is_file()
        assert not (root / "requests/000001.output.npz").exists()
        data = _read_npz(root / "trajectory.npz")
        np.testing.assert_array_equal(data["actions"], _mapped(backend.outputs[0])[:3])
        _assert_inventory(root, result["files"], excluded={"result.json"})
        _assert_media(root, result, config, seed=29)

        retry = run_episode(config, simulation, policy, tmp_path / "closed-session", "test", 3)

        assert retry["status"] == "failed" and retry["steps"] == 0
        assert simulation.step_calls == 3 and len(backend.calls) == 2 and simulation.hold_calls == 2

    @pytest.mark.parametrize(
        "error",
        [
            RuntimeError("backend unavailable"),
            TimeoutError("policy timed out"),
            KeyboardInterrupt("operator interrupted"),
        ],
    )
    def test_given_inference_exception_when_refilling_then_partial_files_and_hold_are_preserved(
        self,
        error: BaseException,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        backend: _SyntheticBackend,
        policy: _InProcessPolicy,
    ) -> None:
        backend.replies[1] = error
        simulation.hold_error = RuntimeError("hold unavailable")
        root = tmp_path / "interrupted"

        result = run_episode(config, simulation, policy, root, "test", 29)

        assert result["status"] == "failed" and result["success"] is False
        assert result["steps"] == simulation.step_calls == 3 and simulation.hold_calls == 1
        assert any(str(error) in item for item in result["errors"])
        assert any("hold unavailable" in item for item in result["errors"])
        assert result["task"]["success"] is True
        assert _read_json(root / "result.json") == result
        _assert_inventory(root, result["files"], excluded={"result.json"})
        _assert_media(root, result, config, seed=29)

    @pytest.mark.parametrize("failure_request", [0, 1])
    def test_given_late_response_when_deadline_expires_then_no_new_action_is_applied(
        self,
        failure_request: int,
        tmp_path: Path,
        config: EvaluationConfig,
        monkeypatch: pytest.MonkeyPatch,
        simulation: _SyntheticSimulation,
        backend: _SyntheticBackend,
        policy: _InProcessPolicy,
    ) -> None:
        clock = {"now": 0.0}

        def now() -> float:
            return clock["now"]

        def delay(request: int) -> None:
            if request == failure_request:
                clock["now"] += config.timeout_seconds + 0.5

        monkeypatch.setattr(runner, "time", SimpleNamespace(perf_counter=now))
        backend.on_infer = delay
        root = tmp_path / "deadline"

        result = run_episode(config, simulation, policy, root, "test", 29)

        assert result["status"] == "failed" and result["success"] is False
        assert result["steps"] == simulation.step_calls == failure_request * 3
        assert simulation.hold_calls == 1 and len(backend.calls) == failure_request + 1
        assert any("deadline" in error for error in result["errors"])
        assert not (root / "requests" / f"{failure_request:06d}.output.npz").exists()

    @pytest.mark.parametrize(
        "kind",
        [
            "blank",
            "missing-actions",
            "nan-action",
            "unused-tail-nan-action",
            "short-action",
            "missing-model-actions",
            "nan-model-actions",
            "infinite-model-actions",
            "object-model-actions",
            "short-model-actions",
            "mismatched-model-actions",
            "nan-timing",
        ],
    )
    def test_given_malformed_policy_response_when_returned_then_episode_fails_without_stepping(
        self,
        kind: str,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        policy: _InProcessPolicy,
    ) -> None:
        def corrupt(response: dict[str, Any]) -> dict[str, Any]:
            if kind == "blank":
                return {}
            if kind == "missing-actions":
                response.pop("actions")
            elif kind == "nan-action":
                response["actions"][0, 0] = np.nan
            elif kind == "unused-tail-nan-action":
                response["actions"][-1, -1] = np.nan
            elif kind == "short-action":
                response["actions"] = response["actions"][:-1]
            elif kind == "missing-model-actions":
                response.pop("model_actions")
            elif kind == "nan-model-actions":
                response["model_actions"][0, 0] = np.nan
            elif kind == "infinite-model-actions":
                response["model_actions"][-1, -1] = np.inf
            elif kind == "object-model-actions":
                response["model_actions"] = response["model_actions"].astype(object)
            elif kind == "short-model-actions":
                response["model_actions"] = response["model_actions"][:-1]
            elif kind == "mismatched-model-actions":
                response["model_actions"] += 0.1
            elif kind == "nan-timing":
                response["inference_ms"] = np.nan
            return response

        policy.response_transform = corrupt

        result = run_episode(config, simulation, policy, tmp_path / "bad-response", "test", 29)

        assert result["status"] == "failed" and result["success"] is False
        assert result["steps"] == simulation.step_calls == 0 and simulation.hold_calls == 1

    @pytest.mark.parametrize("phase", [0, 2], ids=["reset", "post-action"])
    @pytest.mark.parametrize(
        "kind",
        [
            "nan-state",
            "infinite-state",
            "short-state",
            "missing-camera",
            "extra-camera",
            "blank-camera",
            "float-camera",
            "rgba-camera",
            "nan-evidence",
            "infinite-evidence",
            "object-evidence",
            "rank-three-evidence",
            "oversized-evidence",
            "unsafe-evidence-key",
        ],
    )
    def test_given_invalid_frame_when_observed_then_no_invalid_arrays_are_logged(
        self,
        phase: int,
        kind: str,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        policy: _InProcessPolicy,
    ) -> None:
        def corrupt(frame: Frame, step: int) -> Frame:
            if step != phase:
                return frame
            state, images, evidence = frame.state.copy(), dict(frame.images), dict(frame.evidence)
            if kind == "nan-state":
                state[0] = np.nan
            elif kind == "infinite-state":
                state[6] = np.inf
            elif kind == "short-state":
                state = state[:-1]
            elif kind == "missing-camera":
                images.pop("d405")
            elif kind == "extra-camera":
                images["oracle_camera"] = images["d435"]
            elif kind == "blank-camera":
                images["d405"] = np.empty((0, 48, 3), dtype=np.uint8)
            elif kind == "float-camera":
                images["d405"] = images["d405"].astype(np.float32)
            elif kind == "rgba-camera":
                images["d405"] = np.zeros((32, 48, 4), dtype=np.uint8)
            elif kind == "nan-evidence":
                evidence["contact_force"] = np.array([1, np.nan])
            elif kind == "infinite-evidence":
                evidence["gear_pose"][2] = np.inf
            elif kind == "object-evidence":
                evidence["contact_force"] = np.array([1, 2], dtype=object)
            elif kind == "rank-three-evidence":
                evidence["contact_force"] = np.ones((1, 1, 2))
            elif kind == "oversized-evidence":
                evidence["contact_force"] = np.ones(4097)
            elif kind == "unsafe-evidence-key":
                evidence["../oracle"] = np.ones(1)
            return Frame(state, images, evidence)

        simulation.frame_transform = corrupt
        root = tmp_path / "bad-frame"

        result = run_episode(config, simulation, policy, root, "test", 29)

        assert result["status"] == "failed" and result["success"] is False and result["errors"]
        assert simulation.hold_calls == 1
        assert result["steps"] == (0 if phase == 0 else 1)
        assert simulation.step_calls == phase
        for value in _read_npz(root / "trajectory.npz").values():
            assert value.dtype.kind in "fiu" and np.isfinite(value).all()

    @pytest.mark.parametrize("change", ["missing", "added", "shape"])
    def test_given_changed_evidence_schema_when_stepped_then_partial_trajectory_is_retained(
        self,
        change: str,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        policy: _InProcessPolicy,
    ) -> None:
        def change_schema(frame: Frame, step: int) -> Frame:
            if step == 2:
                if change == "missing":
                    frame.evidence.pop("contact_force")
                elif change == "added":
                    frame.evidence["additional"] = np.zeros(1)
                else:
                    frame.evidence["contact_force"] = np.zeros(3)
            return frame

        simulation.frame_transform = change_schema
        root = tmp_path / "schema-change"

        result = run_episode(config, simulation, policy, root, "test", 29)

        assert result["status"] == "failed" and result["steps"] == 1 and simulation.hold_calls == 1
        assert simulation.step_calls == 2
        assert any("schema changed" in error for error in result["errors"])
        np.testing.assert_array_equal(_read_npz(root / "trajectory.npz")["evidence.contact_force"], [[1.5, 1.75]])
        _assert_media(root, result, config, seed=29)

    @pytest.mark.parametrize(
        "kind",
        [
            "nan-limits",
            "infinite-limits",
            "reversed-limits",
            "missing-limits",
            "nan-initial-target",
            "infinite-initial-target",
            "missing-initial-target",
        ],
    )
    def test_given_invalid_initial_contract_when_reset_then_policy_and_step_are_not_called(
        self,
        kind: str,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        backend: _SyntheticBackend,
        policy: _InProcessPolicy,
    ) -> None:
        if kind == "nan-limits":
            simulation.action_limits[6, 0] = np.nan
        elif kind == "infinite-limits":
            simulation.action_limits[0, 1] = np.inf
        elif kind == "reversed-limits":
            simulation.action_limits[0] = [2, -2]
        elif kind == "missing-limits":
            simulation.action_limits = simulation.action_limits[:-1]
        elif kind == "nan-initial-target":
            simulation.initial_command[0] = np.nan
        elif kind == "infinite-initial-target":
            simulation.initial_command[6] = np.inf
        else:
            simulation.initial_command = simulation.initial_command[:-1]

        result = run_episode(config, simulation, policy, tmp_path / "bad-initial-contract", "test", 29)

        assert result["status"] == "failed" and result["steps"] == 0 and simulation.hold_calls == 1
        assert not backend.calls and simulation.step_calls == 0 and not simulation.score_calls

    @pytest.mark.parametrize("phase", ["reset", "step", "score"])
    def test_given_simulator_error_when_running_then_result_cannot_claim_success(
        self,
        phase: str,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        policy: _InProcessPolicy,
    ) -> None:
        setattr(simulation, f"{phase}_error", RuntimeError(f"synthetic {phase} failure"))
        root = tmp_path / "simulator-error"

        result = run_episode(config, simulation, policy, root, "test", 29)

        assert result["status"] == "failed" and result["success"] is False
        assert result["steps"] == {"reset": 0, "step": 1, "score": 5}[phase]
        assert any(f"synthetic {phase} failure" in error for error in result["errors"])
        assert _read_json(root / "result.json") == result
        if phase != "score":
            assert simulation.hold_calls == 1

    @pytest.mark.parametrize(
        "score",
        [{}, {"success": 1}, {"success": "true"}, {"success": np.bool_(True)}, {"success": True, "status": "complete"}],
    )
    def test_given_invalid_task_result_when_scored_then_process_status_cannot_be_overridden(
        self,
        score: dict[str, Any],
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        policy: _InProcessPolicy,
    ) -> None:
        simulation.score_result = score

        result = run_episode(config, simulation, policy, tmp_path / "bad-score", "test", 29)

        assert result["status"] == "failed" and result["success"] is False
        assert result["task"] == {"success": False} and result["steps"] == 5
        assert any("scoring failed" in error for error in result["errors"])

    def test_given_nonfinite_task_metric_when_scored_then_failure_manifest_preserves_completed_steps(
        self,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        policy: _InProcessPolicy,
    ) -> None:
        simulation.score_result = {"success": True, "distance": float("nan")}
        root = tmp_path / "nonfinite-score"

        result = run_episode(config, simulation, policy, root, "test", 29)

        assert result["status"] == "failed" and result["success"] is False and result["steps"] == 5
        assert result["errors"] and _read_json(root / "result.json") == result


class TestEvaluationArtifacts:
    """Batch completion, task outcome, provenance, and final publication remain distinct."""

    @pytest.mark.parametrize("task_success", [True, False])
    def test_given_seed_groups_when_evaluated_then_every_episode_resets_once_in_declared_order(
        self,
        task_success: bool,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        backend: _SyntheticBackend,
        policy: _InProcessPolicy,
    ) -> None:
        simulation.score_result["success"] = task_success
        output = tmp_path / "evaluation"
        output.mkdir()

        result = run_evaluation(config, simulation, policy, output)

        assert result["status"] == "complete" and result["episode_count"] == result["planned_episode_count"] == 3
        assert result["success_count"] == (3 if task_success else 0)
        assert result["not_attempted"] == result["unattempted_seeds"] == []
        assert simulation.reset_seeds == [29, 3, 17]
        assert [(call["seed"], call["reset"]) for call in backend.calls] == [
            (29, True),
            (29, False),
            (3, True),
            (3, False),
            (17, True),
            (17, False),
        ]
        assert result["by_split"] == {
            "test": {"episodes": 2, "successes": 2 if task_success else 0},
            "validation": {"episodes": 1, "successes": 1 if task_success else 0},
        }
        assert simulation.hold_calls == 0 and simulation.step_calls == 15
        for index, report in enumerate(result["episodes"]):
            split, seed = _EPISODES[index]
            assert (report["split"], report["seed"]) == (split, seed)
            assert report["status"] == "complete" and report["success"] is task_success
            root = output / report["path"]
            data = _read_npz(root / "trajectory.npz")
            expected = np.concatenate(
                (_mapped(backend.outputs[index * 2])[:3], _mapped(backend.outputs[index * 2 + 1])[:2])
            )
            np.testing.assert_array_equal(data["actions"], expected)
            np.testing.assert_array_equal(data["states"][0], _measured_frame(seed, 0).state)
            _assert_media(root, report, config, seed=seed)
        provenance = _read_json(output / "provenance.json")
        assert provenance["config_sha256"] == config.sha256
        assert provenance["oracle_policy_inputs"] is False and provenance["demonstration_replay"] is False
        assert provenance["physical_validation"] == "not_performed"
        assert provenance["policy_inputs"] == ["measured_joint_state", "declared_rgb_cameras", "instruction"]
        assert _read_json(output / "config.json") == config.source
        assert _read_json(output / "evaluation.json") == result
        assert not (output / "failure.json").exists()
        _assert_inventory(output, result["files"], excluded={"evaluation.json"})

    def test_given_operational_failure_when_evaluated_then_remaining_seeds_are_explicitly_not_attempted(
        self,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        backend: _SyntheticBackend,
        policy: _InProcessPolicy,
    ) -> None:
        backend.replies[1] = RuntimeError("refill failed")
        output = tmp_path / "fail-fast"
        output.mkdir()

        result = run_evaluation(config, simulation, policy, output)

        assert result["status"] == "failed" and result["episode_count"] == 1 and result["success_count"] == 0
        assert result["planned_episode_count"] == 3
        assert result["not_attempted"] == [{"split": "test", "seed": 3}, {"split": "validation", "seed": 17}]
        assert result["unattempted_seeds"] == [3, 17] and simulation.reset_seeds == [29]
        assert result["by_split"] == {
            "test": {"episodes": 1, "successes": 0},
            "validation": {"episodes": 0, "successes": 0},
        }
        assert len(list(output.glob("episode_*"))) == 1
        assert _read_json(output / "failure.json") == result
        assert not (output / "evaluation.json").exists()
        _assert_inventory(output, result["files"], excluded={"failure.json"})

    def test_given_changed_input_when_revalidated_then_final_manifest_is_not_published(
        self,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        backend: _SyntheticBackend,
        policy: _InProcessPolicy,
    ) -> None:
        identities = [file_identity(config.path)]
        validations = []
        output = tmp_path / "changed-input"
        output.mkdir()

        def change_input(request: int) -> None:
            if request == 1:
                config.path.write_text(config.path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        def validate_inputs() -> None:
            validations.append(True)
            unchanged(identities)

        backend.on_infer = change_input

        with pytest.raises(ValueError, match="Input changed"):
            run_evaluation(config, simulation, policy, output, validate_inputs=validate_inputs)

        assert validations == [True] and simulation.reset_seeds == [29, 3, 17]
        assert not (output / "evaluation.json").exists() and not (output / "failure.json").exists()
        assert len(list(output.glob("episode_*/result.json"))) == 3
        assert len(list(output.glob("episode_*/trajectory.npz"))) == 3
        assert _read_json(output / "progress.json")["completed_episodes"] == 3

    def test_given_existing_output_when_requested_then_existing_bytes_are_unchanged(
        self,
        tmp_path: Path,
        config: EvaluationConfig,
        simulation: _SyntheticSimulation,
        policy: _InProcessPolicy,
    ) -> None:
        output = tmp_path / "existing"
        output.mkdir()
        sentinel = output / "preserve.bin"
        sentinel.write_bytes(b"existing evidence")

        with pytest.raises(ValueError, match="fresh empty directory"):
            run_evaluation(config, simulation, policy, output)
        with pytest.raises(FileExistsError):
            run_episode(config, simulation, policy, output, "test", 29)

        assert sentinel.read_bytes() == b"existing evidence"
        assert list(output.iterdir()) == [sentinel]
        assert not simulation.reset_seeds and not policy.requests

    def test_given_existing_camera_when_writer_opens_then_camera_bytes_are_not_replaced(
        self,
        tmp_path: Path,
        config: EvaluationConfig,
    ) -> None:
        root = tmp_path / "existing-video"
        root.mkdir()
        camera = root / "d435.mp4"
        camera.write_bytes(b"preserved camera evidence")

        with pytest.raises(ValueError, match="overwrite camera evidence"):
            EpisodeVideo(root, config)

        assert camera.read_bytes() == b"preserved camera evidence"
