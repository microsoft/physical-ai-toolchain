"""CPU-only task and CLI ownership tests with explicit synthetic external boundaries.

OpenUSD tests inspect a tiny in-memory authored stage only; no Isaac app, model,
or production owner runs.
Complete observation validation and no-oracle routing belong to the runner tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
import pytest
from sil.vla import __main__ as cli
from sil.vla import backends, tasks, transport
from sil.vla.artifacts import file_identity, fingerprint, unchanged, write_json
from sil.vla.config import EvaluationConfig, digest, load_config, read_json
from sil.vla.interfaces import Frame
from sil.vla.scoring import placement_margins

_OWNERS = ("privileged_expert", "collect_privileged_demos", "producer_shutdown", "generate_dataset")
_CHANNELS = ("shoulder_pan", "shoulder_lift", "elbow", "wrist_1", "wrist_2", "wrist_3", "gripper")
_CONSENT_ENV = ("ACCEPT_EULA", "PRIVACY_CONSENT")


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make accidental framework or production-owner imports fail closed."""
    forbidden = {"rho", "openpi", "jax", "torch", "isaaclab", "isaacsim", "omni"}
    names = forbidden | {name for name in sys.modules if name.split(".")[0] in forbidden}
    for name in names | set(_OWNERS):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)


@dataclass(frozen=True)
class _ExpertConfig:
    control_hz: int = 30
    camera_height: int = 2
    camera_width: int = 4
    task: str = "Place the observed part in the bin."
    bin_wall_margin_m: float = 0.04

    @property
    def acceptance_contract_hash(self) -> str:
        return digest(asdict(self))


@pytest.fixture
def task_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    inputs = tmp_path / "inputs"
    root, assets, checkpoint = inputs / "owners", inputs / "assets", inputs / "checkpoint"
    for path in (root, assets, checkpoint):
        path.mkdir(parents=True)
    modules = {}
    for name in _OWNERS:
        path = root / f"{name}.py"
        path.write_text("raise AssertionError('Synthetic owner was unexpectedly executed')\n", encoding="utf-8")
        module = ModuleType(name)
        module.__file__ = str(path)
        monkeypatch.setitem(sys.modules, name, module)
        modules[name] = module
    expert = _ExpertConfig()
    expert_path = inputs / "expert.json"
    expert_path.write_text(json.dumps(asdict(expert)), encoding="utf-8")
    stage = assets / "stage.usda"
    stage.write_text('#usda 1.0\ndef Xform "World" {}\n', encoding="utf-8")
    dependency = assets / "dependency.usda"
    dependency.write_text('#usda 1.0\ndef Xform "Fixture" {}\n', encoding="utf-8")
    (checkpoint / "metadata.json").write_bytes(b'{"synthetic":true}\n')
    source = {
        "schema_version": 1,
        "instruction": expert.task,
        "channels": list(_CHANNELS),
        "image_shapes": {key: [2, 4, 3] for key in ("d435", "d405")},
        "policy": {
            "backend": "openpi",
            "checkpoint": str(checkpoint),
            "checkpoint_sha256": fingerprint(checkpoint)["sha256"],
            "training_config": "synthetic_registered_config",
            "state_key": "state",
            "prompt_key": "prompt",
            "image_keys": {"d435": "images/front", "d405": "images/wrist"},
            "state_mapping": {"indices": list(range(7)), "scale": [1] * 7, "offset": [0] * 7},
            "action_mapping": {"indices": list(range(7)), "scale": [1] * 7, "offset": [0] * 7},
            "delta_indices": [],
            "action_horizon": 3,
        },
        "task": {
            "adapter": "ur10e_gear",
            "producer_root": str(root),
            "expert_config": str(expert_path),
            "stage": str(stage),
            "reference_root": str(assets),
            "runtime_assets": ["synthetic-material.mdl"],
        },
        "evaluation": {
            "control_hz": 30,
            "execution_horizon": 2,
            "seeds": {"test": [11]},
            "max_steps": 4,
            "timeout_seconds": 5,
            "minimum_free_gib": 0,
            "joint_limit_behavior": "reject",
        },
    }
    config_path = inputs / "config.json"
    config_path.write_text(json.dumps(source), encoding="utf-8")
    bundle = SimpleNamespace(
        config=load_config(config_path), expert=expert, modules=modules, dependency=dependency, calls=[]
    )

    def load_expert(path: Path) -> _ExpertConfig:
        bundle.calls.append(("load_config", path))
        return _ExpertConfig(**read_json(path))

    def runtime_assets(value: dict[str, Any]) -> list[str]:
        bundle.calls.append(("runtime_assets", value))
        return list(value["runtime_assets"])

    def dependencies(stage_path: Path, reference_root: Path, materials: list[str]) -> list[dict[str, str]]:
        bundle.calls.append(("dependencies", stage_path, reference_root, materials))
        return [{"input_path": str(dependency)}]

    modules["privileged_expert"].STATE_NAMES = _CHANNELS
    modules["privileged_expert"].CAMERA_NAMES = ("d435", "d405")
    modules["privileged_expert"].load_config = load_expert
    modules["generate_dataset"]._runtime_assets = runtime_assets
    modules["generate_dataset"]._dependencies = dependencies
    return bundle


class TestOwnerLoading:
    def test_registered_owner_set_is_finite_and_explicit(self) -> None:
        assert tasks._OWNERS == _OWNERS

    @pytest.mark.parametrize("name", _OWNERS)
    def test_only_explicit_synthetic_fixture_modules_are_executed_and_cached(self, tmp_path: Path, name: str) -> None:
        source = tmp_path / f"{name}.py"
        source.write_text("TOKEN = object()\n", encoding="utf-8")

        first = tasks._load_owner(tmp_path, name)
        token = first.TOKEN
        second = tasks._load_owner(tmp_path, name)

        assert first is second and second.TOKEN is token
        assert first.__file__ == str(source) and sys.modules[name] is first

    @pytest.mark.parametrize("name", ["custom", "../../escape", "arbitrary:factory"])
    def test_unknown_owner_is_rejected_before_any_file_execution(self, tmp_path: Path, name: str) -> None:
        with pytest.raises(ValueError, match="Unregistered task owner"):
            tasks._load_owner(tmp_path, name)

    def test_missing_registered_owner_reports_its_exact_source_path(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError) as error:
            tasks._load_owner(tmp_path, "privileged_expert")

        assert str(tmp_path / "privileged_expert.py") in str(error.value)

    @pytest.mark.parametrize("has_file", [False, True])
    def test_conflicting_import_is_not_reused_or_replaced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, has_file: bool
    ) -> None:
        path = tmp_path / "privileged_expert.py"
        path.write_text("raise AssertionError('must not execute')\n", encoding="utf-8")
        foreign = ModuleType("privileged_expert")
        if has_file:
            foreign.__file__ = str(tmp_path / "another.py")
        monkeypatch.setitem(sys.modules, "privileged_expert", foreign)

        with pytest.raises(ValueError, match="Conflicting task owner module: privileged_expert"):
            tasks._load_owner(tmp_path, "privileged_expert")

        assert sys.modules["privileged_expert"] is foreign

    def test_redirected_owner_source_is_rejected_even_with_a_matching_cached_module(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target.py"
        target.write_text("raise AssertionError('must not execute')\n", encoding="utf-8")
        source = tmp_path / "privileged_expert.py"
        source.symlink_to(target)
        cached = ModuleType("privileged_expert")
        cached.__file__ = str(source)
        monkeypatch.setitem(sys.modules, "privileged_expert", cached)

        with pytest.raises(ValueError, match="Task source must not be redirected"):
            tasks._load_owner(tmp_path, "privileged_expert")

        assert sys.modules["privileged_expert"] is cached

    def test_failed_synthetic_import_is_removed_from_the_module_cache(self, tmp_path: Path) -> None:
        source = tmp_path / "privileged_expert.py"
        source.write_text("raise RuntimeError('synthetic fixture import failed')\n", encoding="utf-8")

        with pytest.raises(RuntimeError, match="synthetic fixture import failed"):
            tasks._load_owner(tmp_path, "privileged_expert")

        assert "privileged_expert" not in sys.modules


class TestTaskInspection:
    def test_inspection_uses_real_identities_and_exact_owner_callbacks_without_native_claims(
        self, task_bundle: SimpleNamespace
    ) -> None:
        config = task_bundle.config

        inspection, records = tasks.inspect_task(config)

        expected = [config.task.producer_root / f"{name}.py" for name in _OWNERS]
        expected += [config.path, config.task.expert_config, config.task.stage, task_bundle.dependency]
        assert records == [file_identity(path) for path in expected]
        for path, record in zip(expected, records, strict=True):
            assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        unchanged(records)
        assert inspection == {
            "adapter": "ur10e_gear",
            "expert_config": asdict(task_bundle.expert),
            "expert_config_sha256": task_bundle.expert.acceptance_contract_hash,
            "local_dependencies": records,
            "unpinned_runtime_assets": ["synthetic-material.mdl"],
            "native_validation": "not_performed",
            "usd_inspection": "host_dependency_inventory_only",
        }
        assert task_bundle.calls == [
            ("load_config", config.task.expert_config),
            ("runtime_assets", {"runtime_assets": ["synthetic-material.mdl"]}),
            ("dependencies", config.task.stage, config.task.reference_root, ["synthetic-material.mdl"]),
        ]
        assert sys.modules["torch"] is None and sys.modules["isaaclab"] is None

    @pytest.mark.parametrize(
        ("case", "message"),
        [
            ("channels", "channel order differs"),
            ("cadence", "cadence differs"),
            ("instruction", "task differs"),
            ("missing-camera", "camera contract differs"),
            ("extra-camera", "camera contract differs"),
            ("camera-shape", "camera contract differs"),
        ],
    )
    def test_task_contract_guards_fail_before_dependency_callbacks(
        self, task_bundle: SimpleNamespace, case: str, message: str
    ) -> None:
        config = task_bundle.config
        changes = {
            "channels": {"channels": tuple(reversed(config.channels))},
            "cadence": {"control_hz": 31},
            "instruction": {"instruction": "A different task."},
            "missing-camera": {"image_shapes": {"d435": (2, 4, 3)}},
            "extra-camera": {"image_shapes": {**config.image_shapes, "oracle": (2, 4, 3)}},
            "camera-shape": {"image_shapes": {"d435": (2, 4, 3), "d405": (4, 4, 3)}},
        }

        with pytest.raises(ValueError, match=message):
            tasks.inspect_task(replace(config, **changes[case]))

        assert task_bundle.calls == [("load_config", config.task.expert_config)]

    @pytest.mark.parametrize(
        "case", ["unknown-adapter", "missing-owner-root", "missing-reference-root", "outside-stage"]
    )
    def test_task_paths_and_adapter_are_validated_before_owner_callbacks(
        self, task_bundle: SimpleNamespace, tmp_path: Path, case: str
    ) -> None:
        config = task_bundle.config
        changes = {
            "unknown-adapter": {"adapter": "custom:entrypoint"},
            "missing-owner-root": {"producer_root": tmp_path / "missing"},
            "missing-reference-root": {"reference_root": tmp_path / "missing"},
            "outside-stage": {"stage": tmp_path / "outside.usda"},
        }

        with pytest.raises(ValueError):
            tasks.inspect_task(replace(config, task=replace(config.task, **changes[case])))

        assert not task_bundle.calls

    def test_missing_finite_owner_file_is_not_silently_ignored(self, task_bundle: SimpleNamespace) -> None:
        missing = task_bundle.config.task.producer_root / "producer_shutdown.py"
        missing.unlink()

        with pytest.raises(FileNotFoundError) as error:
            tasks.inspect_task(task_bundle.config)

        assert str(missing) in str(error.value) and not task_bundle.calls

    def test_dependency_callback_cannot_publish_identities_after_an_input_changes(
        self, task_bundle: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        planner = task_bundle.modules["generate_dataset"]
        original = planner._dependencies

        def mutate(stage: Path, root: Path, materials: list[str]) -> list[dict[str, str]]:
            task_bundle.config.path.write_text("changed during synthetic dependency inspection", encoding="utf-8")
            return original(stage, root, materials)

        monkeypatch.setattr(planner, "_dependencies", mutate)

        with pytest.raises(ValueError, match="Input changed"):
            tasks.inspect_task(task_bundle.config)

    @pytest.mark.parametrize("registered", [False, True])
    def test_simulation_dispatch_does_not_execute_arbitrary_adapters(
        self, task_bundle: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, registered: bool
    ) -> None:
        config = task_bundle.config
        if not registered:
            config = replace(config, task=replace(config.task, adapter="custom:entrypoint"))
        inspection = {"native_validation": "not_performed"}
        expected = object()
        calls = []

        def create(selected: EvaluationConfig, record: dict[str, Any], *, device: str) -> object:
            calls.append((selected, record, device))
            return expected

        monkeypatch.setattr(tasks, "Ur10eGearSimulation", create)

        if registered:
            assert tasks.load_simulation(config, inspection, device="cpu") is expected
            assert calls == [(config, inspection, "cpu")]
        else:
            with pytest.raises(ValueError, match="No registered simulator adapter"):
                tasks.load_simulation(config, inspection, device="cpu")
            assert not calls


@pytest.fixture
def authored_geometry() -> SimpleNamespace:
    pytest.importorskip("pxr", reason="Tiny OpenUSD geometry fixture requires the existing audit dependency")
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.CreateInMemory()
    gear = UsdGeom.Xform.Define(stage, "/World/Gear")
    gear.AddTranslateOp().Set(Gf.Vec3d(2, 3, 4))
    gear.AddRotateZOp().Set(90.0)
    target = UsdGeom.Xform.Define(stage, "/World/Bin")
    target.AddTranslateOp().Set(Gf.Vec3d(-1, 2, 0.5))
    target.AddRotateZOp().Set(-90.0)
    UsdPhysics.RigidBodyAPI.Apply(target.GetPrim())
    fixture = SimpleNamespace(stage=stage, bins=["/World/Bin"], calls=[])
    bounds = {"/World/Gear": [[1, 1, 3.5], [3, 5, 4.5]], "/World/Bin": [[-2, 1, 0], [0, 3, 1]]}

    def find_api_paths(selected: Any, root: str, api: Any) -> list[str]:
        assert selected is stage and root == "/World/Bin" and api is UsdPhysics.RigidBodyAPI
        fixture.calls.append(("find", root))
        return fixture.bins

    def world_bounds(selected: Any, root: str) -> list[list[float]]:
        assert selected is stage
        fixture.calls.append(("bounds", root))
        return bounds[root]

    adapter = tasks.Ur10eGearSimulation.__new__(tasks.Ur10eGearSimulation)
    adapter.runtime = {"stage": stage, "gear_body_path": "/World/Gear"}
    adapter.expert_config = _ExpertConfig()
    adapter.collector = SimpleNamespace(
        _BIN_ROOT="/World/Bin", _GEAR_ROOT="/World/Gear", _find_api_paths=find_api_paths, _world_bounds=world_bounds
    )
    fixture.adapter = adapter
    return fixture


class TestUr10eGeometry:
    def test_authored_world_bounds_are_transformed_to_object_local_corners_without_isaac(
        self, authored_geometry: SimpleNamespace
    ) -> None:
        adapter = authored_geometry.adapter

        geometry = adapter._geometry()

        np.testing.assert_allclose(
            geometry["object_local_corners"],
            [
                [-2, 1, -0.5],
                [-2, 1, 0.5],
                [2, 1, -0.5],
                [2, 1, 0.5],
                [-2, -1, -0.5],
                [-2, -1, 0.5],
                [2, -1, -0.5],
                [2, -1, 0.5],
            ],
            atol=1e-12,
        )
        np.testing.assert_allclose(geometry["target_reference_pose"], [-1, 2, 0.5, 2**-0.5, 0, 0, -(2**-0.5)])
        assert geometry["target_reference_bounds"] == [[-2, 1, 0], [0, 3, 1]]
        assert geometry["wall_margin_m"] == 0.04 and geometry["floor_tolerance_m"] == 0.005
        assert geometry["geometry_source"] == "authored USD before first physics step"
        assert geometry["bin_rigid_body"] == adapter.bin_path == "/World/Bin"
        assert authored_geometry.calls == [("find", "/World/Bin"), ("bounds", "/World/Gear"), ("bounds", "/World/Bin")]
        assert sys.modules["isaaclab"] is None and "sim" not in adapter.runtime

    @pytest.mark.parametrize("bins", [[], ["/World/Bin", "/World/SecondBin"]])
    def test_geometry_requires_one_unambiguous_bin_body(
        self, authored_geometry: SimpleNamespace, bins: list[str]
    ) -> None:
        authored_geometry.bins = bins

        with pytest.raises(ValueError, match="requires one live bin rigid body"):
            authored_geometry.adapter._geometry()

        assert authored_geometry.calls == [("find", "/World/Bin")]


class _ArrayView:
    """NumPy stand-in for read-only tensor extraction; no native tensor runtime is loaded."""

    def __init__(self, values: Any) -> None:
        self.values = np.asarray(values)

    def __getitem__(self, key: Any) -> _ArrayView:
        return _ArrayView(self.values[key])

    def detach(self) -> _ArrayView:
        return self

    def cpu(self) -> _ArrayView:
        return self

    def numpy(self) -> np.ndarray:
        return self.values

    def item(self) -> float:
        return float(self.values.item())

    def clone(self) -> _ArrayView:
        return _ArrayView(self.values.copy())


@pytest.fixture
def measured_adapter() -> tasks.Ur10eGearSimulation:
    adapter = tasks.Ur10eGearSimulation.__new__(tasks.Ur10eGearSimulation)
    robot = SimpleNamespace(
        data=SimpleNamespace(
            joint_pos=_ArrayView(np.arange(9, dtype=np.float64)[None]),
            body_pose_w=_ArrayView([[[1, 2, 3, 1, 0, 0, 0], [4, 5, 6, 1, 0, 0, 0]]]),
        )
    )
    gear = SimpleNamespace(
        data=SimpleNamespace(
            root_pose_w=_ArrayView([[0.1, 0.2, 0.3, 1, 0, 0, 0]]),
            root_vel_w=_ArrayView([[0.01, 0.02, 0.03, 0.04, 0.05, 0.06]]),
        )
    )
    cameras = {
        key: SimpleNamespace(
            data=SimpleNamespace(
                output={"rgb": _ArrayView((np.arange(32, dtype=np.uint8) + index * 32).reshape(1, 2, 4, 4))}
            )
        )
        for index, key in enumerate(("d435", "d405"))
    }
    contacts = {
        "left-a": [[[[3, 4, 0], [0, 0, 2]]]],
        "left-b": [[[[1, 2, 2]]]],
        "right-a": [[[[0, 0, 6]]]],
        "right-b": [[[[8, 6, 0]]]],
    }
    adapter.runtime = {
        "robot": robot,
        "gear": gear,
        "cameras": cameras,
        "contact_sensors": {
            key: SimpleNamespace(data=SimpleNamespace(force_matrix_w=_ArrayView(value)))
            for key, value in contacts.items()
        },
        "left_contact_paths": ["left-a", "left-b"],
        "right_contact_paths": ["right-a", "right-b"],
        "arm_command_rad": np.full(6, 999.0),
        "gripper_scalar_target": 999.0,
    }
    adapter.control = {"arm_joint_ids": [3, 1, 6, 0, 5, 2], "gripper_joint_ids": [8, 7], "end_effector_body_id": 1}
    transform = _ArrayView([[0, 0, 0, 0, 0, 2**-0.5, 2**-0.5]])
    velocity = _ArrayView([[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]])
    adapter.bin_view = SimpleNamespace(get_transforms=lambda: transform, get_velocities=lambda: velocity)
    adapter.geometry = {
        "object_local_corners": [[x, y, z] for x in (-0.05, 0.05) for y in (-0.05, 0.05) for z in (-0.05, 0.05)],
        "target_reference_pose": [0, 0, 0, 2**-0.5, 0, 0, 2**-0.5],
        "target_reference_bounds": [[-1, -1, 0], [1, 1, 1]],
        "wall_margin_m": 0.01,
        "floor_tolerance_m": 0.005,
    }
    return adapter


class TestUr10eMeasurementHelpers:
    def test_frame_emits_measured_joints_rgb_wxyz_and_maximum_filtered_contact_per_side(
        self, measured_adapter: tasks.Ur10eGearSimulation
    ) -> None:
        frame = measured_adapter._frame()

        assert isinstance(frame, Frame)
        np.testing.assert_array_equal(frame.state, [3, 1, 6, 0, 5, 2, 8])
        np.testing.assert_allclose(frame.evidence["bin_pose"], [0, 0, 0, 2**-0.5, 0, 0, 2**-0.5])
        np.testing.assert_array_equal(frame.evidence["contact_force"], [5, 10])
        np.testing.assert_array_equal(frame.evidence["ee_pose"], [4, 5, 6, 1, 0, 0, 0])
        assert set(frame.evidence) == {
            "gear_pose",
            "bin_pose",
            "ee_pose",
            "contact_force",
            "gear_velocity",
            "bin_velocity",
            "placement_margins",
        }
        assert set(frame.images) == {"d435", "d405"}
        for key, image in frame.images.items():
            source = measured_adapter.runtime["cameras"][key].data.output["rgb"].values
            np.testing.assert_array_equal(image, source[0, :, :, :3])
            assert image.shape == (2, 4, 3) and image.dtype == np.uint8 and not np.shares_memory(image, source)
        np.testing.assert_array_equal(
            frame.evidence["placement_margins"],
            placement_margins(frame.evidence["gear_pose"], frame.evidence["bin_pose"], measured_adapter.geometry),
        )
        assert sys.modules["torch"] is None

    def test_emitted_arrays_do_not_alias_future_simulator_measurements(
        self, measured_adapter: tasks.Ur10eGearSimulation
    ) -> None:
        frame = measured_adapter._frame()
        expected_state = frame.state.copy()
        expected_evidence = {key: value.copy() for key, value in frame.evidence.items()}
        expected_images = {key: value.copy() for key, value in frame.images.items()}
        runtime = measured_adapter.runtime

        runtime["robot"].data.joint_pos.values.fill(-1)
        runtime["robot"].data.body_pose_w.values.fill(-1)
        runtime["gear"].data.root_pose_w.values.fill(-1)
        runtime["gear"].data.root_vel_w.values.fill(-1)
        measured_adapter.bin_view.get_transforms().values.fill(-1)
        measured_adapter.bin_view.get_velocities().values.fill(-1)
        for camera in runtime["cameras"].values():
            camera.data.output["rgb"].values.fill(0)

        np.testing.assert_array_equal(frame.state, expected_state)
        for key, value in frame.evidence.items():
            np.testing.assert_array_equal(value, expected_evidence[key])
        for key, value in frame.images.items():
            np.testing.assert_array_equal(value, expected_images[key])

    @pytest.mark.parametrize("value", [None, np.nan, np.inf, -np.inf])
    def test_missing_or_nonfinite_filtered_contact_cannot_emit_a_frame(
        self, measured_adapter: tasks.Ur10eGearSimulation, value: float | None
    ) -> None:
        data = measured_adapter.runtime["contact_sensors"]["left-a"].data
        data.force_matrix_w = None if value is None else _ArrayView([[[[value, 0, 0]]]])

        with pytest.raises(ValueError, match=r"Missing filtered contact matrix|Non-finite contact measurement"):
            measured_adapter._frame()

    @pytest.mark.parametrize("count", [0, 1, 2])
    def test_bin_view_requires_exactly_one_actor(self, measured_adapter: tasks.Ur10eGearSimulation, count: int) -> None:
        view = SimpleNamespace(count=count)
        calls = []

        def create(path: str) -> SimpleNamespace:
            calls.append(path)
            return view

        measured_adapter.bin_path = "/World/Bin"
        measured_adapter.runtime["sim"] = SimpleNamespace(
            physics_sim_view=SimpleNamespace(create_rigid_body_view=create)
        )

        if count == 1:
            assert measured_adapter._bin_view() is view
        else:
            with pytest.raises(ValueError, match="must contain one actor"):
                measured_adapter._bin_view()
        assert calls == ["/World/Bin"]

    def test_hold_and_close_only_touch_owned_runtime_callbacks_without_another_step(
        self, measured_adapter: tasks.Ur10eGearSimulation
    ) -> None:
        calls = []
        robot = measured_adapter.runtime["robot"]

        def target(value: _ArrayView) -> None:
            np.testing.assert_array_equal(value.values, robot.data.joint_pos.values)
            assert not np.shares_memory(value.values, robot.data.joint_pos.values)
            calls.append("target")

        robot.set_joint_position_target = target
        robot.write_data_to_sim = lambda: calls.append("write")
        measured_adapter.runtime["sim"] = SimpleNamespace(stop=lambda: calls.append("stop"))

        measured_adapter.hold()
        measured_adapter.close()

        assert calls == ["target", "write", "stop"]


def _arguments(config: EvaluationConfig, output: Path, operation: str) -> argparse.Namespace:
    values = [
        operation,
        "--config",
        str(config.path),
        "--output",
        str(output),
        "--device",
        "cpu",
        "--host",
        "127.0.0.1",
        "--port",
        "8761",
        "--socket",
        str(output.parent / "synthetic-policy.sock"),
    ]
    if operation == "run":
        values += ["--accept-nvidia-terms", "--accept-nvidia-privacy"]
    return cli.create_parser().parse_args(values)


@pytest.fixture
def cli_boundary(task_bundle: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    config = task_bundle.config
    output = tmp_path / "evidence"
    boundary = SimpleNamespace(
        config=config,
        output=output,
        events=[],
        failure=None,
        exception=None,
        empty_app=False,
        release_calls=[],
        drift=None,
        validations=0,
        retained_failure=None,
        result={"status": "complete", "producer": "synthetic_cpu"},
    )
    monkeypatch.setenv("ACCEPT_EULA", "operator-original")
    monkeypatch.delenv("PRIVACY_CONSENT", raising=False)

    def event(name: str) -> None:
        boundary.events.append(name)
        if boundary.failure == name:
            raise boundary.exception if boundary.exception is not None else RuntimeError(f"synthetic {name} failure")

    def acquire(path: Path, *, owner: str) -> object:
        assert path == config.task.producer_root / "fixture.lock" and owner == "VLA simulation evaluation"
        assert all(os.environ[key] == "Y" for key in _CONSENT_ENV)
        event("lock.acquire")
        return boundary.lock

    def release(lock: object) -> None:
        assert lock is boundary.lock
        boundary.release_calls.append(lock)
        event("lock.release")

    def launch(args: argparse.Namespace) -> SimpleNamespace:
        assert vars(args) == {"headless": True, "enable_cameras": True, "device": "cpu"}
        assert all(os.environ[key] == "Y" for key in _CONSENT_ENV)
        event("app.create")
        return SimpleNamespace(app=None if boundary.empty_app else boundary.app)

    def create_simulation(selected: EvaluationConfig, inspection: dict[str, Any], *, device: str) -> SimpleNamespace:
        assert selected is config and device == "cpu"
        assert inspection["native_validation"] == "not_performed"
        event("simulation.create")
        return boundary.simulation

    def create_client(selected: EvaluationConfig, **kwargs: Any) -> SimpleNamespace:
        assert selected is config
        assert kwargs == {"host": "127.0.0.1", "port": 8761, "socket_path": tmp_path / "synthetic-policy.sock"}
        event("client.create")
        return boundary.client

    def evaluate(
        selected: EvaluationConfig,
        simulation: Any,
        client: Any,
        path: Path,
        *,
        validate_inputs: Any,
        publish_complete: bool,
    ) -> dict[str, Any]:
        assert selected is config and simulation is boundary.simulation and client is boundary.client
        assert publish_complete is False
        assert path == output and path.is_dir()
        if boundary.retained_failure is not None:
            write_json(path / "failure.json", boundary.retained_failure)
        event("evaluate")
        validate_inputs()
        boundary.validations += 1
        return boundary.result

    def create_backend(selected: EvaluationConfig, device: str) -> SimpleNamespace:
        assert selected is config and device == "cpu"
        event("backend.create")
        if boundary.drift is not None:
            path = config.path if boundary.drift == "config" else config.policy.checkpoint / "metadata.json"
            path.write_bytes(path.read_bytes() + b"\n")
        return boundary.backend

    def serve(selected: EvaluationConfig, backend: Any, **kwargs: Any) -> None:
        assert selected is config and backend is boundary.backend
        assert kwargs == {"host": "127.0.0.1", "port": 8761, "socket_path": tmp_path / "synthetic-policy.sock"}
        assert read_json(output / "server.json")["producer"] == boundary.backend.provenance
        event("server.serve")

    boundary.lock = object()
    boundary.app = SimpleNamespace(close=lambda: event("app.close"))
    boundary.simulation = SimpleNamespace(
        provenance={"producer": "synthetic_cpu"}, close=lambda: event("simulation.close")
    )
    boundary.client = SimpleNamespace(close=lambda: event("client.close"))
    boundary.backend = SimpleNamespace(provenance={"producer": "synthetic_cpu", "native_validation": "not_performed"})
    shutdown = task_bundle.modules["producer_shutdown"]
    shutdown.acquire_process_lock = acquire
    shutdown.release_process_lock = release
    shutdown.prepare_for_shutdown = lambda: event("shutdown.prepare")
    task_bundle.modules["collect_privileged_demos"]._INSTANCE_LOCK_PATH = config.task.producer_root / "fixture.lock"
    isaaclab = ModuleType("isaaclab")
    isaaclab.__path__ = []
    app_module = ModuleType("isaaclab.app")
    app_module.AppLauncher = launch
    isaaclab.app = app_module
    monkeypatch.setitem(sys.modules, "isaaclab", isaaclab)
    monkeypatch.setitem(sys.modules, "isaaclab.app", app_module)
    runner = ModuleType("sil.vla.runner")
    runner.run_evaluation = evaluate
    monkeypatch.setitem(sys.modules, "sil.vla.runner", runner)
    monkeypatch.setattr(tasks, "load_simulation", create_simulation)
    monkeypatch.setattr(tasks, "validate_native_runtime", lambda: {"isaacsim": "synthetic_runtime_metadata"})
    monkeypatch.setattr(transport, "PolicyClient", create_client)
    monkeypatch.setattr(backends, "load_backend", create_backend)
    monkeypatch.setattr(transport, "serve_policy", serve)
    return boundary


def _assert_environment_restored() -> None:
    actual = {name: os.environ.get(name) for name in _CONSENT_ENV}
    assert actual == {"ACCEPT_EULA": "operator-original", "PRIVACY_CONSENT": None}, (
        f"Consent environment not restored: {actual!r}"
    )


class TestCliRunOwnership:
    @pytest.mark.parametrize("flags", [[], ["--accept-nvidia-terms"], ["--accept-nvidia-privacy"]])
    def test_missing_independent_consent_flags_create_no_output_or_runtime(
        self, cli_boundary: SimpleNamespace, capsys: pytest.CaptureFixture[str], flags: list[str]
    ) -> None:
        boundary = cli_boundary

        code = cli.main(
            [
                "run",
                "--config",
                str(boundary.config.path),
                "--output",
                str(boundary.output),
                "--device",
                "cpu",
                *flags,
            ]
        )

        result = json.loads(capsys.readouterr().out)
        assert code == 2 and result["status"] == "failed"
        assert "separate --accept-nvidia-terms and --accept-nvidia-privacy" in result["error"]
        assert not boundary.events and not boundary.output.exists()
        _assert_environment_restored()

    def test_success_owns_exactly_one_application_lock_simulation_and_client(
        self, cli_boundary: SimpleNamespace
    ) -> None:
        boundary = cli_boundary
        args = _arguments(boundary.config, boundary.output, "run")

        result = cli.run(args, boundary.config)

        assert result is boundary.result and boundary.validations == 1
        assert boundary.events == [
            "lock.acquire",
            "app.create",
            "simulation.create",
            "client.create",
            "evaluate",
            "client.close",
            "shutdown.prepare",
            "simulation.close",
            "app.close",
            "lock.release",
        ]
        assert boundary.release_calls == [boundary.lock]
        sources = boundary.simulation.provenance["harness_sources"]
        assert file_identity(boundary.config.path) in sources
        unchanged(sources)
        assert not (boundary.output / "failure.json").exists()
        _assert_environment_restored()

    @pytest.mark.parametrize(
        ("phase", "cleanup"),
        [
            ("lock.acquire", []),
            ("app.create", ["lock.release"]),
            ("simulation.create", ["shutdown.prepare", "app.close", "lock.release"]),
            ("client.create", ["shutdown.prepare", "simulation.close", "app.close", "lock.release"]),
            ("evaluate", ["client.close", "shutdown.prepare", "simulation.close", "app.close", "lock.release"]),
        ],
    )
    def test_failure_releases_only_successfully_acquired_resources_and_records_exact_error(
        self, cli_boundary: SimpleNamespace, phase: str, cleanup: list[str]
    ) -> None:
        boundary = cli_boundary
        boundary.failure = phase
        args = _arguments(boundary.config, boundary.output, "run")

        with pytest.raises(RuntimeError, match=re.escape(f"synthetic {phase} failure")):
            cli.run(args, boundary.config)

        failure_index = boundary.events.index(phase)
        assert boundary.events[failure_index + 1 :] == cleanup
        assert boundary.release_calls == ([] if phase == "lock.acquire" else [boundary.lock])
        assert read_json(boundary.output / "failure.json") == {
            "status": "failed",
            "config_sha256": boundary.config.sha256,
            "error": f"RuntimeError: synthetic {phase} failure",
            "complete": False,
        }
        _assert_environment_restored()

    def test_keyboard_interrupt_retains_failure_evidence_and_closes_owned_resources(
        self, cli_boundary: SimpleNamespace
    ) -> None:
        boundary = cli_boundary
        boundary.failure = "evaluate"
        boundary.exception = KeyboardInterrupt("synthetic interrupt")

        with pytest.raises(KeyboardInterrupt, match="synthetic interrupt"):
            cli.run(_arguments(boundary.config, boundary.output, "run"), boundary.config)

        assert read_json(boundary.output / "failure.json")["error"] == "KeyboardInterrupt: synthetic interrupt"
        assert boundary.events[-5:] == [
            "client.close",
            "shutdown.prepare",
            "simulation.close",
            "app.close",
            "lock.release",
        ]
        _assert_environment_restored()

    def test_existing_runner_failure_evidence_is_not_overwritten(self, cli_boundary: SimpleNamespace) -> None:
        boundary = cli_boundary
        boundary.failure = "evaluate"
        boundary.retained_failure = {"status": "failed", "producer": "synthetic_runner", "partial_steps": 2}

        with pytest.raises(RuntimeError, match="synthetic evaluate failure"):
            cli.run(_arguments(boundary.config, boundary.output, "run"), boundary.config)

        assert read_json(boundary.output / "failure.json") == boundary.retained_failure
        assert boundary.release_calls == [boundary.lock]
        _assert_environment_restored()

    def test_insufficient_storage_fails_before_lock_or_application_initialization(
        self, cli_boundary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        boundary = cli_boundary
        config = replace(boundary.config, minimum_free_gib=1)
        monkeypatch.setattr(cli.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))

        with pytest.raises(ValueError, match="Insufficient evaluation storage"):
            cli.run(_arguments(config, boundary.output, "run"), config)

        assert not boundary.events and not boundary.release_calls
        _assert_environment_restored()

    @pytest.mark.parametrize("phase", ["client.close", "shutdown.prepare", "simulation.close"])
    def test_cleanup_failure_still_attempts_remaining_owned_resource_cleanup(
        self, cli_boundary: SimpleNamespace, phase: str
    ) -> None:
        boundary = cli_boundary
        boundary.failure = phase

        with pytest.raises(RuntimeError, match=re.escape(f"synthetic {phase} failure")):
            cli.run(_arguments(boundary.config, boundary.output, "run"), boundary.config)

        assert boundary.release_calls == [boundary.lock]
        _assert_environment_restored()
        expected = ["client.close", "shutdown.prepare", "simulation.close", "app.close", "lock.release"]
        actual = boundary.events[boundary.events.index("client.close") :]
        skipped = [name for name in expected if name not in actual]
        assert actual == expected, f"Cleanup stopped after {phase}: observed={actual!r}; skipped={skipped!r}"

    def test_failed_application_initialization_preserves_the_original_error(
        self, cli_boundary: SimpleNamespace
    ) -> None:
        boundary = cli_boundary
        boundary.empty_app = True

        with pytest.raises(ValueError, match="Isaac application did not initialize"):
            cli.run(_arguments(boundary.config, boundary.output, "run"), boundary.config)

        assert boundary.release_calls == [boundary.lock]
        assert "simulation.create" not in boundary.events
        _assert_environment_restored()

    def test_lock_release_failure_does_not_prevent_consent_environment_restoration(
        self, cli_boundary: SimpleNamespace
    ) -> None:
        boundary = cli_boundary
        boundary.failure = "lock.release"

        with pytest.raises(RuntimeError, match=r"synthetic lock\.release failure"):
            cli.run(_arguments(boundary.config, boundary.output, "run"), boundary.config)

        assert boundary.release_calls == [boundary.lock]
        assert "app.close" in boundary.events
        _assert_environment_restored()


class TestCliServeOwnership:
    def test_server_manifest_binds_exact_checkpoint_sources_backend_and_transport_before_serving(
        self, cli_boundary: SimpleNamespace
    ) -> None:
        boundary = cli_boundary
        args = _arguments(boundary.config, boundary.output, "serve")

        result = cli.serve(args, boundary.config)

        manifest = read_json(boundary.output / "server.json")
        assert result == {"status": "stopped", "operation": "serve", "output": str(boundary.output)}
        assert boundary.events == ["backend.create", "server.serve"]
        assert manifest["contract"] == boundary.config.metadata()
        assert manifest["checkpoint"] == fingerprint(boundary.config.policy.checkpoint)
        assert manifest["producer"] == boundary.backend.provenance
        assert manifest["transport"] == {"host": args.host, "port": args.port, "socket": str(args.socket)}
        unchanged(manifest["harness_sources"])
        assert not (boundary.output / "failure.json").exists() and not boundary.release_calls
        _assert_environment_restored()

    @pytest.mark.parametrize("phase", ["backend.create", "server.serve"])
    @pytest.mark.parametrize("interrupted", [False, True])
    def test_server_failure_records_exact_error_without_claiming_simulator_ownership(
        self, cli_boundary: SimpleNamespace, phase: str, interrupted: bool
    ) -> None:
        boundary = cli_boundary
        boundary.failure = phase
        error_type = KeyboardInterrupt if interrupted else RuntimeError
        boundary.exception = error_type("synthetic server failure")

        with pytest.raises(error_type, match="synthetic server failure"):
            cli.serve(_arguments(boundary.config, boundary.output, "serve"), boundary.config)

        assert read_json(boundary.output / "failure.json") == {
            "status": "interrupted" if interrupted else "failed",
            "error": f"{error_type.__name__}: synthetic server failure",
            "config_sha256": boundary.config.sha256,
        }
        assert (boundary.output / "server.json").exists() is (phase == "server.serve")
        assert not boundary.release_calls
        _assert_environment_restored()

    @pytest.mark.parametrize("drift", ["checkpoint", "config"])
    def test_model_loading_cannot_publish_a_manifest_after_checkpoint_or_source_drift(
        self, cli_boundary: SimpleNamespace, drift: str
    ) -> None:
        boundary = cli_boundary
        boundary.drift = drift
        message = "Checkpoint content differs" if drift == "checkpoint" else "Input changed"

        with pytest.raises(ValueError, match=message):
            cli.serve(_arguments(boundary.config, boundary.output, "serve"), boundary.config)

        assert boundary.events == ["backend.create"]
        assert not (boundary.output / "server.json").exists()
        assert message in read_json(boundary.output / "failure.json")["error"]


class TestCliOutputGuards:
    @pytest.mark.parametrize("operation", ["run", "serve"])
    def test_existing_output_is_preserved_before_any_native_or_model_initialization(
        self, cli_boundary: SimpleNamespace, operation: str
    ) -> None:
        boundary = cli_boundary
        boundary.output.mkdir()
        sentinel = boundary.output / "preserve.bin"
        sentinel.write_bytes(b"existing evidence")

        with pytest.raises(ValueError, match="Output must be fresh"):
            getattr(cli, operation)(_arguments(boundary.config, boundary.output, operation), boundary.config)

        assert not boundary.events and not boundary.release_calls
        assert list(boundary.output.iterdir()) == [sentinel] and sentinel.read_bytes() == b"existing evidence"
        _assert_environment_restored()

    @pytest.mark.parametrize("operation", ["run", "serve"])
    def test_output_overlapping_checkpoint_is_rejected_without_creating_it(
        self, cli_boundary: SimpleNamespace, operation: str
    ) -> None:
        boundary = cli_boundary
        output = boundary.config.policy.checkpoint / "evidence"

        with pytest.raises(ValueError, match="Output overlaps protected input"):
            getattr(cli, operation)(_arguments(boundary.config, output, operation), boundary.config)

        assert not output.exists() and not boundary.events and not boundary.release_calls
        _assert_environment_restored()
