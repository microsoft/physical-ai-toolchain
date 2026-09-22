# cspell:ignore autohome draccus gethostname pyautogui teleop teleoperator
"""Control-loop entrypoint for the offloaded ur10e-single Pi0.5 deployment.

Two modes:

``self-check``
    Load the checkpoint on the GPU stage and run synthetic observations through
    it. Proves the offload path end to end without a UR10e attached.

``headless``
    Drive the UR10e from a terminal with no display, no dataset, and no
    teleoperator. This is the mode a container runs: it homes the arm, then
    streams observations to the GPU stage and applies the returned actions.

``record``
    Run the stock ``lerobot-record`` loop from the ur10e-single deployment with
    inference redirected to the GPU stage. Robot and camera I/O stay in this
    container; only the observation tensors and the returned action cross the
    pod network.

The record mode redirects lerobot at three seams instead of forking it:
``make_policy`` and ``make_pre_post_processors`` return local stand-ins, and
``predict_action`` converts the observation to CPU tensors and calls the remote
:class:`~ur10e_offload.PolicyRunner`. The stand-ins exist because the record loop
reads ``policy.config.device`` and ``policy.config.use_amp`` on every cycle; a
remote proxy would turn each read into a round trip.

The headless mode skips those seams entirely and runs the same sequence the
record loop performs per step -- observation, dataset frame, inference, action --
which keeps the desktop dependencies of the ur10e-single plugin out of the path.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import sys
import time
import types
from copy import copy
from pathlib import Path
from typing import Any

import numpy as np
import torch
from ur10e_offload import PolicyRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ur10e-offload")

DEFAULT_RENAME_MAP = {
    "observation.images.scene": "observation.images.base_0_rgb",
    "observation.images.wrist": "observation.images.left_wrist_0_rgb",
}

_MODES = ("self-check", "headless", "record")


def emit(event: str, **fields: Any) -> None:
    """Print a single-line JSON event the verification scripts assert against."""
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


class _RemotePolicyConfig:
    """The subset of the policy config the record loop reads locally."""

    def __init__(self, device: str, use_amp: bool) -> None:
        self.device = device
        self.use_amp = use_amp


class OffloadSession:
    """Holds the remote policy handles for the lifetime of the process."""

    def __init__(self, checkpoint_path: str, device: str, rename_map: dict[str, str]) -> None:
        self._runner = PolicyRunner(checkpoint_path=checkpoint_path, device=device)
        logger.info("Requesting checkpoint load on the server stage: %s", checkpoint_path)
        started = time.perf_counter()
        # The policy and both pipelines are remoted classes, so these are proxies
        # bound to the server stage rather than deserialized objects.
        self.policy, self.preprocessor, self.postprocessor = self._runner.load(rename_map)
        self.info = self._runner.describe(self.policy)
        emit(
            "loaded",
            executed_by=self.info["hostname"],
            client_host=socket.gethostname(),
            checkpoint=checkpoint_path,
            device=self.info["device"],
            policy_class=self.info["policy_class"],
            cuda_available=self.info["cuda_available"],
            cuda_device_name=self.info["cuda_device_name"],
            load_seconds=round(time.perf_counter() - started, 2),
        )

    @property
    def use_amp(self) -> bool:
        return bool(self.info["use_amp"])

    @property
    def device(self) -> str:
        return str(self.info["device"])

    def reset(self) -> None:
        self._runner.reset(self.policy, self.preprocessor, self.postprocessor)

    def get_action(self, observation: dict[str, Any]) -> torch.Tensor:
        return self._runner.get_action(
            self.policy,
            self.preprocessor,
            self.postprocessor,
            observation,
            self.use_amp,
        )


class _OffloadedPolicy:
    """Local stand-in for the policy object the record loop passes around."""

    def __init__(self, session: OffloadSession) -> None:
        self._session = session
        self.config = _RemotePolicyConfig(device=session.device, use_amp=session.use_amp)

    def reset(self) -> None:
        self._session.reset()


class _RemoteProcessor:
    """Local stand-in for a processor pipeline that lives on the server stage."""

    def __init__(self, session: OffloadSession) -> None:
        self._session = session

    def reset(self) -> None:
        # The record loop resets the policy and both pipelines together, and
        # OffloadSession.reset already covers all three in one remote call.
        return None


def _install_offload(session: OffloadSession, rename_map: dict[str, str], log_every: int) -> None:
    """Redirect the lerobot record loop at the remote policy."""
    import lerobot.scripts.lerobot_record as lerobot_record
    from lerobot.policies.utils import prepare_observation_for_inference

    state = {"step": 0}

    def make_policy(*_args: Any, **_kwargs: Any) -> _OffloadedPolicy:
        return _OffloadedPolicy(session)

    def make_pre_post_processors(*_args: Any, **_kwargs: Any) -> tuple[_RemoteProcessor, _RemoteProcessor]:
        return _RemoteProcessor(session), _RemoteProcessor(session)

    def predict_action(
        observation: dict[str, np.ndarray],
        policy: Any,
        device: torch.device,
        preprocessor: Any,
        postprocessor: Any,
        use_amp: bool,
        task: str | None = None,
        robot_type: str | None = None,
    ) -> torch.Tensor:
        # Convert on the CPU: the codec has no NumPy adapter, and this container
        # holds no GPU to convert onto.
        started = time.perf_counter()
        prepared = prepare_observation_for_inference(copy(observation), torch.device("cpu"), task, robot_type)
        action = session.get_action(prepared)
        state["step"] += 1
        if state["step"] % log_every == 0:
            emit(
                "action",
                step=state["step"],
                client_host=socket.gethostname(),
                executed_by=session.info["hostname"],
                action_shape=list(action.shape),
                action=[round(float(value), 4) for value in action.flatten().tolist()[:8]],
                cycle_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        return action

    lerobot_record.make_policy = make_policy
    lerobot_record.make_pre_post_processors = make_pre_post_processors
    lerobot_record.predict_action = predict_action
    logger.info("Redirected make_policy, make_pre_post_processors, and predict_action to the GPU stage")


def _synthetic_observation(state_dim: int, cameras: dict[str, tuple[int, int]]) -> dict[str, np.ndarray]:
    """Build a raw observation shaped like the UR10e recording frames."""
    observation: dict[str, np.ndarray] = {
        "observation.state": np.zeros(state_dim, dtype=np.float32),
    }
    for name, (width, height) in cameras.items():
        observation[f"observation.images.{name}"] = np.zeros((height, width, 3), dtype=np.uint8)
    return observation


def _run_self_check(session: OffloadSession, args: argparse.Namespace) -> int:
    from lerobot.policies.utils import prepare_observation_for_inference

    cameras = {"scene": (320, 240), "wrist": (424, 240)}
    logger.info("Running %d synthetic inference steps", args.steps)
    session.reset()

    for step in range(args.steps):
        observation = _synthetic_observation(args.state_dim, cameras)
        prepared = prepare_observation_for_inference(copy(observation), torch.device("cpu"), args.task, "ur10e")
        started = time.perf_counter()
        action = session.get_action(prepared)
        cycle_ms = round((time.perf_counter() - started) * 1000, 1)
        emit(
            "action",
            step=step,
            client_host=socket.gethostname(),
            executed_by=session.info["hostname"],
            action_shape=list(action.shape),
            action=[round(float(value), 4) for value in action.flatten().tolist()[:8]],
            cycle_ms=cycle_ms,
        )

    emit("self_check_passed", steps=args.steps, executed_by=session.info["hostname"])

    # Hold the loop open at the control frequency. The client Deployment must stay
    # Running: the controller reconciles the GPU stage against a live client, and a
    # container that exits would tear the stage down and reload 7 GB on restart.
    period = 1.0 / max(args.fps, 1)
    step = args.steps
    while True:
        started = time.perf_counter()
        observation = _synthetic_observation(args.state_dim, cameras)
        prepared = prepare_observation_for_inference(copy(observation), torch.device("cpu"), args.task, "ur10e")
        action = session.get_action(prepared)
        cycle_ms = round((time.perf_counter() - started) * 1000, 1)
        if step % args.log_every == 0:
            emit(
                "action",
                step=step,
                client_host=socket.gethostname(),
                executed_by=session.info["hostname"],
                action_shape=list(action.shape),
                action=[round(float(value), 4) for value in action.flatten().tolist()[:8]],
                cycle_ms=cycle_ms,
            )
        step += 1
        time.sleep(max(0.0, period - (time.perf_counter() - started)))


class _NullDashboard:
    """Stands in for the Gradio dashboard the ur10e-single plugin builds.

    The plugin renders camera feeds to a browser and gates episode start on a
    button click. Neither is reachable from a container, and the plugin's
    fallback for an unavailable dashboard is a blocking :func:`input` call, so
    the dashboard is replaced rather than disabled.
    """

    available = False
    url = None

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def render(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def start_waiting(self) -> None:
        return None

    def stop_waiting(self) -> None:
        return None

    def is_clicked(self) -> bool:
        return True

    def close(self) -> None:
        return None


def _install_headless_stubs() -> None:
    """Remove the display dependencies from the ur10e-single plugin import path.

    ``lerobot_robot_ur10e/__init__.py`` imports its teleoperator, which imports
    ``pyautogui`` at module scope; ``pyautogui`` resolves ``DISPLAY`` while
    importing and raises ``KeyError`` when there is no X server. The headless
    loop is policy-driven and never calls a teleoperator, so a stub is enough.
    """
    if "pyautogui" not in sys.modules:
        stub = types.ModuleType("pyautogui")
        stub.press = lambda *_args, **_kwargs: None
        stub.hotkey = lambda *_args, **_kwargs: None
        sys.modules["pyautogui"] = stub


def _require_realsense() -> None:
    """Fail early when the RealSense backend cannot load.

    lerobot imports ``pyrealsense2`` lazily and swallows the failure, so a missing
    shared library surfaces much later as ``NameError: name 'rs' is not defined``
    inside camera connect.
    """
    try:
        import pyrealsense2  # noqa: F401
    except ImportError as error:
        raise RuntimeError(f"The RealSense backend failed to load: {error}") from error


def _build_robot(robot_config_path: str) -> Any:
    """Construct the UR10e driver from the ur10e-single robot config file."""
    _install_headless_stubs()

    import draccus

    # Camera config subclasses register themselves on import, and the ur10e-single
    # config names them by their registered type rather than their class.
    from lerobot.cameras import opencv, realsense  # noqa: F401
    from lerobot_robot_ur10e import UR10E, UR10EConfig
    from lerobot_robot_ur10e import ur10e as ur10e_module

    # The plugin constructs the dashboard in __init__ and calls it from
    # send_action, so both the class and the two call sites are neutralized.
    ur10e_module.DemoDashboard = _NullDashboard
    UR10E._wait_for_dashboard_button = lambda self: None
    UR10E._render_dashboard = lambda self: None

    payload = json.loads(Path(robot_config_path).read_text(encoding="utf-8"))
    robot_section = payload.get("robot", payload)
    robot_section.pop("type", None)
    config = draccus.decode(UR10EConfig, robot_section)
    if any(type(camera).__name__ == "RealSenseCameraConfig" for camera in config.cameras.values()):
        _require_realsense()
    return UR10E(config)


def _home(robot: Any, speed: float) -> None:
    """Move the arm straight to the home pose and enable servoJ streaming.

    The driver's ``reset`` action reaches home by way of the submissive pose at
    full speed and gates the transition on the dashboard button. Driving the
    same steps directly removes the detour and keeps the one unplanned move
    slow. ``active`` is what gates servoJ, and only this sequence sets it.
    """
    robot.reset()
    robot.home(gripper_position=0.0, speed=speed)
    robot.active = True


def _run_headless(session: OffloadSession, args: argparse.Namespace) -> int:
    """Drive the arm from the offloaded policy with no display or dataset."""
    from lerobot.datasets.utils import build_dataset_frame, hw_to_dataset_features
    from lerobot.policies.utils import make_robot_action, prepare_observation_for_inference

    robot = _build_robot(args.robot_config)
    observation_features = hw_to_dataset_features(robot.observation_features, "observation")
    action_features = hw_to_dataset_features(robot.action_features, "action")

    logger.info("Connecting to the UR10e and cameras")
    robot.connect()
    emit("robot_connected", robot_type=robot.robot_type, cameras=sorted(robot.cameras))

    stopping = {"requested": False}

    def request_stop(*_args: Any) -> None:
        stopping["requested"] = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        session.reset()
        logger.warning("Moving the arm to the home pose; keep the workspace clear")
        _home(robot, args.home_speed)
        emit("homed")

        period = 1.0 / max(args.fps, 1)
        step = 0
        while not stopping["requested"] and (args.max_steps <= 0 or step < args.max_steps):
            started = time.perf_counter()
            frame = build_dataset_frame(observation_features, robot.get_observation(), prefix="observation")
            # Convert on the CPU: the codec has no NumPy adapter, and this
            # container holds no GPU to convert onto.
            prepared = prepare_observation_for_inference(frame, torch.device("cpu"), args.task, robot.robot_type)
            action_values = session.get_action(prepared)
            action = make_robot_action(action_values, action_features)
            robot.send_action(action)

            cycle_ms = round((time.perf_counter() - started) * 1000, 1)
            if step % args.log_every == 0:
                emit(
                    "action",
                    step=step,
                    client_host=socket.gethostname(),
                    executed_by=session.info["hostname"],
                    action={name: round(value, 4) for name, value in action.items()},
                    cycle_ms=cycle_ms,
                )
            step += 1
            time.sleep(max(0.0, period - (time.perf_counter() - started)))

        emit("headless_finished", steps=step)
    finally:
        logger.info("Returning the arm to the home pose and disconnecting")
        robot.active = False
        _home(robot, args.home_speed)
        emit("homed_on_exit")
        robot.disconnect()

    return 0


def _run_record(session: OffloadSession, args: argparse.Namespace, rename_map: dict[str, str]) -> int:
    import lerobot.scripts.lerobot_record as lerobot_record

    _install_offload(session, rename_map, args.log_every)

    sys.argv = [
        "lerobot-record",
        f"--config_path={args.robot_config}",
        f"--dataset.repo_id={args.dataset_repo_id}",
        f"--dataset.num_episodes={args.num_episodes}",
        f"--dataset.episode_time_s={args.episode_time_s}",
        f"--dataset.fps={args.fps}",
        f"--dataset.reset_time_s={args.reset_time_s}",
        f"--dataset.single_task={args.task}",
        f"--dataset.rename_map={json.dumps(rename_map)}",
        f"--policy.path={args.checkpoint_path}",
        f"--policy.n_action_steps={args.n_action_steps}",
        "--policy.compile_model=false",
        "--policy.push_to_hub=false",
        "--dataset.push_to_hub=false",
        "--teleop.type=ur10e-autohome",
        "--display_data=false",
    ]
    logger.info("Starting lerobot-record with offloaded inference")
    return int(lerobot_record.main() or 0)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=_MODES,
        default=os.environ.get("UR10E_MODE", "self-check"),
        help="self-check drives synthetic observations; headless and record drive the UR10e",
    )
    parser.add_argument("--checkpoint-path", default=os.environ.get("UR10E_CHECKPOINT_PATH", "/models/pi05-ur10e"))
    parser.add_argument("--device", default=os.environ.get("UR10E_DEVICE", "cuda"))
    parser.add_argument(
        "--task",
        default=os.environ.get("UR10E_TASK", "Pick up the large white gear and place it in the blue bin."),
    )
    parser.add_argument(
        "--robot-config", default=os.environ.get("UR10E_ROBOT_CONFIG", "/workspace/script/ur10e_config_demo.json")
    )
    parser.add_argument("--dataset-repo-id", default=os.environ.get("UR10E_DATASET_REPO_ID", "gpu-offload/eval_demo"))
    parser.add_argument("--fps", type=int, default=int(os.environ.get("UR10E_FPS", "10")))
    parser.add_argument("--num-episodes", type=int, default=int(os.environ.get("UR10E_NUM_EPISODES", "1000")))
    parser.add_argument("--episode-time-s", type=int, default=int(os.environ.get("UR10E_EPISODE_TIME_S", "10")))
    parser.add_argument("--reset-time-s", type=int, default=int(os.environ.get("UR10E_RESET_TIME_S", "6000000")))
    parser.add_argument("--n-action-steps", type=int, default=int(os.environ.get("UR10E_N_ACTION_STEPS", "50")))
    parser.add_argument("--state-dim", type=int, default=int(os.environ.get("UR10E_STATE_DIM", "7")))
    parser.add_argument("--steps", type=int, default=int(os.environ.get("UR10E_SELF_CHECK_STEPS", "3")))
    parser.add_argument(
        "--max-steps",
        type=int,
        default=int(os.environ.get("UR10E_MAX_STEPS", "0")),
        help="headless mode: stop after this many control cycles, or 0 to run until interrupted",
    )
    parser.add_argument(
        "--home-speed",
        type=float,
        default=float(os.environ.get("UR10E_HOME_SPEED", "0.2")),
        help="headless mode: joint speed for the moves to the home pose",
    )
    parser.add_argument("--log-every", type=int, default=int(os.environ.get("UR10E_LOG_EVERY", "50")))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    # argparse only validates choices for values passed on the command line, so a
    # mode supplied through UR10E_MODE reaches here unchecked.
    if args.mode not in _MODES:
        logger.error("Unknown mode %r; expected one of %s", args.mode, ", ".join(_MODES))
        return 1

    rename_map = DEFAULT_RENAME_MAP
    raw_rename_map = os.environ.get("UR10E_RENAME_MAP", "").strip()
    if raw_rename_map:
        rename_map = json.loads(raw_rename_map)

    checkpoint = Path(args.checkpoint_path)
    if not (checkpoint / "config.json").is_file():
        logger.error("No checkpoint at %s; the model volume is missing or empty", checkpoint)
        return 1

    session = OffloadSession(
        checkpoint_path=str(checkpoint),
        device=args.device,
        rename_map=rename_map,
    )

    if args.mode == "self-check":
        return _run_self_check(session, args)
    if args.mode == "headless":
        return _run_headless(session, args)
    return _run_record(session, args, rename_map)


if __name__ == "__main__":
    sys.exit(main())
