"""Finite task adapters reusing reviewed simulation owners without loading their CLI."""

from __future__ import annotations

import importlib.util
import itertools
import sys
from dataclasses import asdict
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from .artifacts import file_identity, unchanged
from .config import EvaluationConfig, require
from .interfaces import Frame
from .scoring import placement_margins, rotation_matrix, score_gear

_OWNERS = ("privileged_expert", "collect_privileged_demos", "producer_shutdown", "generate_dataset")


def _load_owner(root: Path, name: str) -> ModuleType:
    require(name in _OWNERS, "Unregistered task owner")
    path = root / f"{name}.py"
    require(path.resolve(strict=True) == path, "Task source must not be redirected")
    existing = sys.modules.get(name)
    if existing is not None:
        require(getattr(existing, "__file__", None) == str(path), f"Conflicting task owner module: {name}")
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"Cannot import task owner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def inspect_task(
    config: EvaluationConfig, *, inspect_dependencies: bool = True
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Inspect the selected finite owner and USD dependencies on CPU; never launch Isaac."""
    require(config.task.adapter == "ur10e_gear", "No registered simulator adapter for selected task")
    root = config.task.producer_root
    require(root.is_dir(), "Select the packaged UR10e scripts/capture directory")
    require(
        config.task.reference_root.is_dir() and config.task.stage.is_relative_to(config.task.reference_root),
        "Stage must remain inside its asset reference root",
    )
    records = [file_identity(root / f"{name}.py") for name in _OWNERS]
    records += [file_identity(config.path), file_identity(config.task.expert_config), file_identity(config.task.stage)]
    expert = _load_owner(root, "privileged_expert")
    cfg = expert.load_config(config.task.expert_config)
    require(tuple(config.channels) == expert.STATE_NAMES, "UR10e state/action channel order differs from native owner")
    require(config.control_hz == cfg.control_hz, "Evaluation cadence differs from the expert runtime configuration")
    require(
        set(config.image_shapes) == set(expert.CAMERA_NAMES)
        and all(shape == (cfg.camera_height, cfg.camera_width, 3) for shape in config.image_shapes.values()),
        "Evaluation camera contract differs from native owner",
    )
    require(config.instruction == cfg.task, "Evaluation task differs from the selected expert configuration")
    runtime_assets = list(config.task.runtime_assets)
    if inspect_dependencies:
        planner = _load_owner(root, "generate_dataset")
        runtime_assets = planner._runtime_assets({"runtime_assets": runtime_assets})
        dependencies = planner._dependencies(config.task.stage, config.task.reference_root, runtime_assets)
        records.extend(file_identity(Path(item["input_path"])) for item in dependencies)
    unchanged(records)
    return {
        "adapter": config.task.adapter,
        "expert_config": asdict(cfg),
        "expert_config_sha256": cfg.acceptance_contract_hash,
        "local_dependencies": records,
        "unpinned_runtime_assets": runtime_assets,
        "native_validation": "not_performed",
        "usd_inspection": "host_dependency_inventory_only"
        if inspect_dependencies
        else "deferred_until_application_start",
    }, records


def validate_native_runtime() -> dict[str, str]:
    """Check installed runtime metadata without importing Isaac or constructing a GPU context."""
    require(sys.version_info[:2] == (3, 11), "UR10e native runtime requires Python 3.11")
    versions = {name: metadata.version(name) for name in ("isaacsim", "isaaclab", "torch", "numpy")}
    require(
        versions["isaacsim"] == "5.1.0.0" and versions["isaaclab"] == "0.54.4",
        "UR10e adapter requires Isaac Sim 5.1.0.0 and Isaac Lab 0.54.4",
    )
    return versions


class Ur10eGearSimulation:
    """Direct joint-target UR10e adapter; no demonstration replay or trajectory expert acts during evaluation."""

    def __init__(self, config: EvaluationConfig, inspection: dict[str, Any], *, device: str) -> None:
        versions = validate_native_runtime()
        self.config = config
        self.expert = _load_owner(config.task.producer_root, "privileged_expert")
        self.collector = _load_owner(config.task.producer_root, "collect_privileged_demos")
        self.expert_config = self.expert.load_config(config.task.expert_config)
        self.runtime = self.collector._build_runtime(config.task.stage, self.expert_config, device)
        self.geometry = self._geometry()
        self.runtime["render_unrecorded"] = False
        self.runtime["sim"].reset()
        self.runtime["gripper_materials"] = self.collector._configure_gripper_friction(self.runtime, self.expert_config)
        self.runtime["robot"].update(1 / self.expert_config.physics_hz)
        self.runtime["gear"].update(1 / self.expert_config.physics_hz)
        for index in range(self.expert_config.control_hz):
            self.collector._step_runtime(
                self.runtime, self.expert_config, render=index == self.expert_config.control_hz - 1
            )
        self.runtime["gear_initial_pose_w"] = self.runtime["gear"].data.root_pose_w.clone()
        self.bin_view = self._bin_view()
        self.bin_reset = self.bin_view.get_transforms().clone()
        self.control = self.collector._resolve_motion_control(
            self.runtime, self.expert_config, self.expert.ARM_JOINT_NAMES, self.expert.GRIPPER_JOINT_NAMES
        )
        arm_limits = (
            self.runtime["robot"].data.soft_joint_pos_limits[0, self.control["arm_joint_ids"]].detach().cpu().numpy()
        )
        self.action_limits = np.concatenate(
            (arm_limits, [[self.expert_config.gripper_open_rad, self.expert_config.transport_gripper_max_rad]])
        )
        self.initial_command = np.zeros(7)
        self.provenance = {
            **inspection,
            "native_runtime": versions,
            "device": device,
            "geometry": self.geometry,
            "native_validation": "constructed_pending_rollout",
            "control": "absolute_joint_targets",
            "contact_measurement": "maximum filtered force magnitude per gripper side",
            "initial_conditions": "seeded square XY offset; explicit robot and bin reset",
            "deterministic_physics": False,
        }

    def _geometry(self) -> dict[str, Any]:
        from pxr import Gf, Usd, UsdGeom, UsdPhysics

        stage = self.runtime["stage"]
        bins = self.collector._find_api_paths(stage, self.collector._BIN_ROOT, UsdPhysics.RigidBodyAPI)
        require(len(bins) == 1, "UR10e scorer requires one live bin rigid body")
        self.bin_path = bins[0]

        def pose(path: str) -> np.ndarray:
            transform = Gf.Transform(
                UsdGeom.XformCache(Usd.TimeCode.Default()).GetLocalToWorldTransform(stage.GetPrimAtPath(path))
            )
            quat = transform.GetRotation().GetQuat()
            return np.array([*transform.GetTranslation(), quat.GetReal(), *quat.GetImaginary()])

        gear_pose = pose(self.runtime["gear_body_path"])
        low, high = self.collector._world_bounds(stage, self.collector._GEAR_ROOT)
        corners = np.array(list(itertools.product(*zip(low, high, strict=True))))
        local = (corners - gear_pose[:3]) @ rotation_matrix(gear_pose[3:])
        return {
            "object_local_corners": local.tolist(),
            "target_reference_pose": pose(self.bin_path).tolist(),
            "target_reference_bounds": self.collector._world_bounds(stage, self.collector._BIN_ROOT),
            "wall_margin_m": self.expert_config.bin_wall_margin_m,
            "floor_tolerance_m": 0.005,
            "geometry_source": "authored USD before first physics step",
            "bin_rigid_body": self.bin_path,
        }

    def _bin_view(self) -> Any:
        view = self.runtime["sim"].physics_sim_view.create_rigid_body_view(self.bin_path)
        require(view.count == 1, "Bin physics view must contain one actor")
        return view

    def reset(self, seed: int) -> Frame:
        """Reset the complete scene, then restore the same bin baseline and sampled gear position."""
        import torch

        self.collector._seed_everything(seed)
        self.collector._reset_simulation_for_episode(self.runtime, self.expert_config)
        self.bin_view = self._bin_view()
        indices = torch.tensor([0], dtype=torch.int32, device=self.bin_reset.device)
        self.bin_view.set_transforms(self.bin_reset.clone(), indices)
        self.bin_view.set_velocities(torch.zeros_like(self.bin_view.get_velocities()), indices)
        self.collector._reset_gear_for_episode(self.runtime, self.expert_config, seed)
        self.collector._reset_robot_for_episode(self.runtime, self.control, self.expert_config)
        for _ in range(round(self.expert_config.settle_seconds * self.expert_config.control_hz)):
            self.collector._step_runtime(self.runtime, self.expert_config, render=True)
        self.initial_command = np.r_[self.runtime["arm_command_rad"], self.runtime["gripper_scalar_target"]]
        return self._frame()

    def _frame(self) -> Frame:
        robot = self.runtime["robot"]
        state = np.r_[
            robot.data.joint_pos[0, self.control["arm_joint_ids"]].detach().cpu().numpy(),
            float(robot.data.joint_pos[0, self.control["gripper_joint_ids"][0]].item()),
        ]
        images = {
            key: camera.data.output["rgb"][0, :, :, :3].detach().cpu().numpy().copy()
            for key, camera in self.runtime["cameras"].items()
        }
        gear = self.runtime["gear"].data.root_pose_w[0].detach().cpu().numpy().copy()
        bin_pose = self.bin_view.get_transforms()[0].detach().cpu().numpy()[[0, 1, 2, 6, 3, 4, 5]]
        forces = []
        for side in ("left", "right"):
            magnitudes = []
            for path in self.runtime[f"{side}_contact_paths"]:
                matrix = self.runtime["contact_sensors"][path].data.force_matrix_w
                require(matrix is not None, "Missing filtered contact matrix")
                value = matrix.detach().cpu().numpy()
                require(bool(np.isfinite(value).all()), "Non-finite contact measurement")
                magnitudes.append(float(np.linalg.norm(value, axis=-1).max()))
            forces.append(max(magnitudes))
        evidence = {
            "gear_pose": gear,
            "bin_pose": bin_pose,
            "ee_pose": robot.data.body_pose_w[0, self.control["end_effector_body_id"]].detach().cpu().numpy().copy(),
            "contact_force": np.array(forces),
            "gear_velocity": self.runtime["gear"].data.root_vel_w[0].detach().cpu().numpy().copy(),
            "bin_velocity": self.bin_view.get_velocities()[0].detach().cpu().numpy().copy(),
            "placement_margins": placement_margins(gear, bin_pose, self.geometry),
        }
        return Frame(state, images, evidence)

    def step(self, action: np.ndarray) -> Frame:
        """Execute policy targets directly; no IK solving, adaptive grasp, or expert fallback."""
        import torch

        robot = self.runtime["robot"]
        robot.set_joint_position_target(
            torch.as_tensor(action[:6], device=robot.device, dtype=robot.data.joint_pos.dtype)[None],
            joint_ids=self.control["arm_joint_ids"],
        )
        gripper = self.expert.gripper_joint_targets(float(action[6]))
        robot.set_joint_position_target(
            torch.as_tensor(gripper, device=robot.device, dtype=robot.data.joint_pos.dtype)[None],
            joint_ids=self.control["gripper_joint_ids"],
        )
        self.runtime["arm_command_rad"] = action[:6].copy()
        self.runtime["gripper_scalar_target"] = float(action[6])
        self.collector._step_runtime(self.runtime, self.expert_config, render=True)
        return self._frame()

    def score(self, data: dict[str, np.ndarray], initial: Frame) -> dict[str, Any]:
        """Evaluate a complete or retained partial trajectory under the declared task limits."""
        return score_gear(data, initial.evidence, self.initial_command, asdict(self.expert_config))

    def hold(self) -> None:
        """Hold achieved articulation positions without an additional simulation step."""
        robot = self.runtime["robot"]
        robot.set_joint_position_target(robot.data.joint_pos.clone())
        robot.write_data_to_sim()

    def close(self) -> None:
        """Stop stepping; application ownership and shutdown are handled by the CLI."""
        self.runtime["sim"].stop()


def load_simulation(config: EvaluationConfig, inspection: dict[str, Any], *, device: str) -> Ur10eGearSimulation:
    """Dispatch only reviewed registered task adapters, never arbitrary Python paths."""
    require(config.task.adapter == "ur10e_gear", "No registered simulator adapter for selected task")
    return Ur10eGearSimulation(config, inspection, device=device)
