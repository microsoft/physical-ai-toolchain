"""CPU-only configuration, provenance, and CLI contracts for ``sil.vla``."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import pytest
from sil.vla.__main__ import create_parser, main
from sil.vla.artifacts import file_identity, fingerprint, fresh_directory, inventory, unchanged, write_json
from sil.vla.config import EvaluationConfig, VectorMapping, canonical, digest, load_config, read_json


@pytest.fixture
def config_source() -> dict[str, Any]:
    """Provide an explicit three-channel contract without requiring model assets."""
    return {
        "schema_version": 1,
        "instruction": "Move the observed part into the tray.",
        "channels": ["shoulder", "gripper", "wrist"],
        "image_shapes": {"front": [2, 4, 3], "wrist": [4, 2, 3]},
        "policy": {
            "backend": "openpi",
            "checkpoint": "assets/checkpoint",
            "checkpoint_sha256": "a" * 64,
            "training_config": "synthetic_cpu_contract",
            "state_key": "state",
            "prompt_key": "prompt",
            "image_keys": {"front": "images/front", "wrist": "images/wrist"},
            "state_mapping": {"indices": [1, 0, 2], "scale": [10, 2, 0.25], "offset": [100, 200, 300]},
            "action_mapping": {"indices": [2, 0, 1], "scale": [2, -1, 0.5], "offset": [1, 0.25, -0.125]},
            "delta_indices": [0, 2],
            "action_horizon": 3,
        },
        "task": {
            "adapter": "synthetic_external_task",
            "producer_root": "producer",
            "expert_config": "profiles/expert.json",
            "stage": "assets/scene.usda",
            "reference_root": "assets",
            "runtime_assets": [],
        },
        "evaluation": {
            "control_hz": 30,
            "execution_horizon": 2,
            "seeds": {"validation": [11], "test": [23]},
            "max_steps": 3,
            "timeout_seconds": 5,
            "minimum_free_gib": 0,
            "joint_limit_behavior": "reject",
        },
    }


def _load(tmp_path: Path, source: dict[str, Any]) -> EvaluationConfig:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    return load_config(path)


def _assign(source: dict[str, Any], path: tuple[str | int, ...], value: Any) -> None:
    target = source
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.fixture
def config(tmp_path: Path, config_source: dict[str, Any]) -> EvaluationConfig:
    """Load the real JSON parser rather than constructing unchecked dataclasses."""
    return _load(tmp_path, config_source)


@pytest.fixture
def checkpoint(tmp_path: Path) -> Path:
    """Use small opaque files, never a model checkpoint or downloadable asset."""
    root = tmp_path / "checkpoint"
    (root / "assets").mkdir(parents=True)
    (root / "config.json").write_text('{"sha256":"not-an-authoritative-self-digest"}', encoding="utf-8")
    (root / "assets" / "normalization.json").write_bytes(b'{"mean":[1,2,3]}\n')
    return root


class TestStrictJson:
    def test_canonical_json_and_digest_are_finite_and_order_independent(self) -> None:
        expected = b'{"a":[1,2],"z":{"value":0.5}}'
        first = {"z": {"value": 0.5}, "a": [1, 2]}
        second = {"a": [1, 2], "z": {"value": 0.5}}

        assert canonical(first) == canonical(second) == expected
        assert digest(first) == hashlib.sha256(expected).hexdigest()

    @pytest.mark.parametrize(
        "document",
        [
            '{"seed":1,"seed":2}',
            '{"policy":{"state_key":"a","state_key":"b"}}',
            '{"values":[{"x":1,"x":2}]}',
        ],
    )
    def test_duplicate_json_keys_are_rejected_at_every_depth(self, tmp_path: Path, document: str) -> None:
        path = tmp_path / "duplicate.json"
        path.write_text(document, encoding="utf-8")

        with pytest.raises(ValueError, match="Duplicate JSON keys"):
            read_json(path)

    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
    def test_nonfinite_json_is_rejected(self, tmp_path: Path, token: str) -> None:
        path = tmp_path / "nonfinite.json"
        path.write_text('{"nested":{"value":' + token + "}}", encoding="utf-8")

        with pytest.raises(ValueError):
            read_json(path)

    @pytest.mark.parametrize("document", ["[]", "null", "1", '"text"', "true", "{", '{"x":1} trailing'])
    def test_json_requires_one_complete_object(self, tmp_path: Path, document: str) -> None:
        path = tmp_path / "invalid.json"
        path.write_text(document, encoding="utf-8")

        with pytest.raises(ValueError):
            read_json(path)

    @pytest.mark.parametrize("key", ["schema_version", "control_hz"])
    def test_config_loader_uses_duplicate_detection(
        self, tmp_path: Path, config_source: dict[str, Any], key: str
    ) -> None:
        text = json.dumps(config_source)
        original = f'"{key}": {1 if key == "schema_version" else 30}'
        path = tmp_path / "config.json"
        path.write_text(text.replace(original, f"{original}, {original}"), encoding="utf-8")

        with pytest.raises(ValueError, match="Duplicate JSON keys"):
            load_config(path)


class TestConfiguration:
    def test_metadata_binds_ordered_observations_timing_and_config(self, config: EvaluationConfig) -> None:
        assert config.metadata() == {
            "protocol": "physical_ai_vla_policy_v1",
            "config_sha256": config.sha256,
            "checkpoint_sha256": "a" * 64,
            "backend": "openpi",
            "channels": ["shoulder", "gripper", "wrist"],
            "action_representation": "absolute_joint_positions",
            "units": "rad",
            "control_hz": 30,
            "action_horizon": 3,
            "execution_horizon": 2,
            "image_shapes": {"front": [2, 4, 3], "wrist": [4, 2, 3]},
            "episodes": [{"split": "validation", "seed": 11}, {"split": "test", "seed": 23}],
        }
        expected = json.dumps(config.source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        assert config.sha256 == hashlib.sha256(expected).hexdigest()
        assert config.episodes == (("validation", 11), ("test", 23))

    @pytest.mark.parametrize(
        "path",
        [
            ("schema_version",),
            ("policy", "action_horizon"),
            ("policy", "state_mapping", "indices", 0),
            ("policy", "action_mapping", "indices", 0),
            ("policy", "delta_indices", 0),
            ("image_shapes", "front", 0),
            ("image_shapes", "front", 2),
            ("evaluation", "control_hz"),
            ("evaluation", "execution_horizon"),
            ("evaluation", "max_steps"),
            ("evaluation", "timeout_seconds"),
            ("evaluation", "minimum_free_gib"),
            ("evaluation", "seeds", "validation", 0),
        ],
    )
    @pytest.mark.parametrize("value", [False, True])
    def test_boolean_numeric_configuration_is_rejected(
        self, tmp_path: Path, config_source: dict[str, Any], path: tuple[str | int, ...], value: bool
    ) -> None:
        _assign(config_source, path, value)

        with pytest.raises(ValueError):
            _load(tmp_path, config_source)

    @pytest.mark.parametrize("mapping", ["state_mapping", "action_mapping"])
    @pytest.mark.parametrize("field", ["scale", "offset"])
    def test_mixed_boolean_affine_coefficients_are_rejected(
        self, tmp_path: Path, config_source: dict[str, Any], mapping: str, field: str
    ) -> None:
        config_source["policy"][mapping][field][0] = True

        with pytest.raises(ValueError):
            _load(tmp_path, config_source)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
    @pytest.mark.parametrize("field", ["timeout_seconds", "minimum_free_gib"])
    def test_nonfinite_config_values_fail_before_use(
        self, tmp_path: Path, config_source: dict[str, Any], field: str, value: float
    ) -> None:
        config_source["evaluation"][field] = value

        with pytest.raises(ValueError):
            _load(tmp_path, config_source)

    @pytest.mark.parametrize("section", [None, "policy", "task", "evaluation", "state_mapping", "action_mapping"])
    def test_unknown_configuration_fields_are_rejected(
        self, tmp_path: Path, config_source: dict[str, Any], section: str | None
    ) -> None:
        target = config_source if section is None else config_source.get(section, config_source["policy"].get(section))
        target["oracle_state"] = [1, 2, 3]

        with pytest.raises(ValueError, match="requires exactly"):
            _load(tmp_path, config_source)

    @pytest.mark.parametrize(
        ("path", "value"),
        [
            (("channels",), ["shoulder", "shoulder", "wrist"]),
            (("channels",), []),
            (("policy", "action_horizon"), 0),
            (("policy", "action_horizon"), 1001),
            (("policy", "action_horizon"), 3.0),
            (("evaluation", "execution_horizon"), 0),
            (("evaluation", "execution_horizon"), 4),
            (("evaluation", "max_steps"), 0),
            (("evaluation", "timeout_seconds"), 0),
            (("evaluation", "minimum_free_gib"), -1),
            (("evaluation", "seeds"), {}),
            (("evaluation", "seeds"), {"validation": []}),
            (("evaluation", "seeds"), {"validation": [11, 11]}),
            (("evaluation", "seeds"), {"train": [11], "test": [11]}),
            (("evaluation", "seeds"), {"unspecified": [11]}),
            (("evaluation", "seeds"), {"test": [-1]}),
            (("evaluation", "seeds"), {"test": [2**32]}),
            (("evaluation", "seeds"), {"test": [11.0]}),
            (("policy", "checkpoint_sha256"), "REPLACE_WITH_CHECKPOINT_TREE_SHA256"),
            (("policy", "checkpoint_sha256"), "A" * 64),
            (("policy", "backend"), "scripted_oracle"),
            (("policy", "training_config"), None),
            (("policy", "delta_indices"), [0, 0]),
            (("policy", "delta_indices"), [3]),
            (("policy", "state_mapping", "indices"), [3, 0, 2]),
            (("policy", "state_mapping", "indices"), [0, 0, 2]),
            (("policy", "action_mapping", "scale"), [0, 1, 1]),
            (("policy", "action_mapping", "offset"), [0, 0]),
            (("image_shapes", "front"), [3, 4, 3]),
            (("image_shapes", "front"), [2, 4, 4]),
            (("image_shapes", "front"), [0, 4, 3]),
            (("image_shapes",), {"Unsafe/Camera": [2, 4, 3]}),
            (("policy", "image_keys"), {"front": "images/front"}),
            (("policy", "image_keys", "front"), "images/wrist"),
            (("policy", "state_key"), "images/front"),
            (("policy", "prompt_key"), "state"),
            (("policy", "state_key"), " "),
            (("instruction",), "bad\ninstruction"),
            (("task", "runtime_assets"), ["material.mdl", "material.mdl"]),
            (("evaluation", "joint_limit_behavior"), "silently_ignore"),
            (("evaluation", "joint_limit_behavior"), True),
        ],
    )
    def test_invalid_contracts_are_rejected(
        self, tmp_path: Path, config_source: dict[str, Any], path: tuple[str | int, ...], value: Any
    ) -> None:
        _assign(config_source, path, value)

        with pytest.raises(ValueError):
            _load(tmp_path, config_source)

    def test_action_mapping_requires_every_simulator_channel(
        self, tmp_path: Path, config_source: dict[str, Any]
    ) -> None:
        config_source["policy"]["action_mapping"] = {"indices": [0, 1], "scale": [1, 1], "offset": [0, 0]}

        with pytest.raises(ValueError, match="cover every simulator channel"):
            _load(tmp_path, config_source)

    def test_camera_payload_budget_is_checked_without_allocating_images(
        self, tmp_path: Path, config_source: dict[str, Any]
    ) -> None:
        config_source["image_shapes"]["front"] = [4096, 4096, 3]

        with pytest.raises(ValueError, match="Camera payload exceeds limit"):
            _load(tmp_path, config_source)

    def test_seed_inventory_has_a_finite_budget(self, tmp_path: Path, config_source: dict[str, Any]) -> None:
        config_source["evaluation"]["seeds"] = {"test": list(range(10001))}

        with pytest.raises(ValueError, match="Seeds overlap or exceed budget"):
            _load(tmp_path, config_source)

    def test_seed_boundaries_and_caller_supplied_split_order_are_preserved(
        self, tmp_path: Path, config_source: dict[str, Any]
    ) -> None:
        config_source["evaluation"]["seeds"] = {"test": [2**32 - 1], "train": [0], "unverified": [7]}

        config = _load(tmp_path, config_source)

        assert config.episodes == (("test", 2**32 - 1), ("train", 0), ("unverified", 7))

    def test_rho_does_not_apply_a_second_delta_transform(self, tmp_path: Path, config_source: dict[str, Any]) -> None:
        config_source["policy"].update(backend="rho", training_config=None)

        with pytest.raises(ValueError, match="already restores absolute actions"):
            _load(tmp_path, config_source)

        config_source["policy"]["delta_indices"] = []
        assert _load(tmp_path, config_source).policy.delta_indices == ()

    def test_rho_requires_checkpoint_owned_training_configuration(
        self, tmp_path: Path, config_source: dict[str, Any]
    ) -> None:
        config_source["policy"].update(backend="rho", delta_indices=[])

        with pytest.raises(ValueError, match="Rho reads configuration from the checkpoint"):
            _load(tmp_path, config_source)

    @pytest.mark.parametrize("section", ["checkpoint", "producer_root", "expert_config", "stage", "reference_root"])
    def test_input_paths_must_be_local(self, tmp_path: Path, config_source: dict[str, Any], section: str) -> None:
        owner = "policy" if section == "checkpoint" else "task"
        config_source[owner][section] = "https://example.invalid/asset"

        with pytest.raises(ValueError, match="must be a local path"):
            _load(tmp_path, config_source)

    def test_paths_resolve_against_config_location_not_current_directory(
        self, tmp_path: Path, config_source: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        location = tmp_path / "configuration"
        location.mkdir()
        unrelated = tmp_path / "unrelated"
        unrelated.mkdir()
        monkeypatch.chdir(unrelated)

        config = _load(location, config_source)

        assert config.path == location / "config.json"
        assert config.policy.checkpoint == location / "assets" / "checkpoint"
        for name in ("producer_root", "expert_config", "stage", "reference_root"):
            assert getattr(config.task, name) == (location / config_source["task"][name]).resolve()
        assert config.source == config_source
        assert not config.policy.checkpoint.exists()

    @pytest.mark.parametrize("backend", ["rho", "openpi"])
    def test_example_templates_require_replacement_and_load_after_localization(
        self, tmp_path: Path, checkpoint: Path, backend: str
    ) -> None:
        example = Path(__file__).resolve().parents[3] / "examples" / "vla" / f"{backend}-ur10e.example.json"
        original = example.read_bytes()

        with pytest.raises(ValueError, match="Supply checkpoint tree SHA-256"):
            load_config(example)

        source = read_json(example)
        source["policy"].update(checkpoint=str(checkpoint), checkpoint_sha256=fingerprint(checkpoint)["sha256"])
        if backend == "openpi":
            source["policy"]["training_config"] = "synthetic_cpu_contract"
        for name in ("producer_root", "expert_config", "stage", "reference_root"):
            source["task"][name] = str(tmp_path / name)
        config = _load(tmp_path, source)

        assert config.policy.backend == backend
        assert config.policy.checkpoint == checkpoint
        assert config.execution_horizon <= config.policy.action_horizon
        assert example.read_bytes() == original


class TestObservationAndMapping:
    def test_measured_state_and_rgb_are_copied_without_extra_features(self, config: EvaluationConfig) -> None:
        state = np.array([10, 20, 30], dtype=np.int32)
        images = {
            name: np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)[:, ::-1]
            for name, shape in config.image_shapes.items()
        }

        observed, cameras = config.validate_observation(state, images)

        np.testing.assert_array_equal(observed, state)
        assert observed.dtype == np.float64
        assert not np.shares_memory(observed, state)
        assert set(cameras) == {"front", "wrist"}
        for name, image in cameras.items():
            np.testing.assert_array_equal(image, images[name])
            assert image.dtype == np.uint8 and image.flags.c_contiguous
            assert not np.shares_memory(image, images[name])

    @pytest.mark.parametrize(
        "state",
        [
            np.zeros(2),
            np.zeros(4),
            np.zeros((1, 3)),
            np.ones(3, dtype=bool),
            np.array(["1", "2", "3"]),
            np.array([1, 2, 3], dtype=object),
            np.array([0, np.nan, 0]),
            np.array([0, np.inf, 0]),
        ],
    )
    def test_state_requires_exact_finite_numeric_channels(self, config: EvaluationConfig, state: np.ndarray) -> None:
        images = {name: np.zeros(shape, dtype=np.uint8) for name, shape in config.image_shapes.items()}

        with pytest.raises(ValueError, match="Measured state"):
            config.validate_observation(state, images)

    @pytest.mark.parametrize("case", ["missing", "extra", "float", "bool", "object", "shape", "channels_first"])
    def test_camera_contract_excludes_undeclared_and_wrong_dtype_images(
        self, config: EvaluationConfig, case: str
    ) -> None:
        images = {name: np.zeros(shape, dtype=np.uint8) for name, shape in config.image_shapes.items()}
        if case == "missing":
            del images["wrist"]
        elif case == "extra":
            images["oracle_segmentation"] = images["front"]
        elif case in {"float", "bool", "object"}:
            images["front"] = images["front"].astype({"float": np.float32, "bool": bool, "object": object}[case])
        elif case == "shape":
            images["front"] = np.zeros((4, 4, 3), dtype=np.uint8)
        else:
            images["front"] = images["front"].transpose(2, 0, 1)

        with pytest.raises(ValueError, match="Camera"):
            config.validate_observation(np.zeros(3), images)

    def test_vector_mapping_selects_reorders_and_applies_affine_conversion(self) -> None:
        mapping = VectorMapping.parse({"indices": [2, 0], "scale": [2, -0.5], "offset": [1, 4]}, "mapping")
        values = np.array([[10, 99, 3, 88], [20, 77, 4, 66]], dtype=np.float64)
        original = values.copy()

        np.testing.assert_array_equal(mapping.apply(values), [[7, -1], [9, -6]])
        np.testing.assert_array_equal(mapping.apply(values[0]), [7, -1])
        np.testing.assert_array_equal(values, original)

    @pytest.mark.parametrize(
        "values", [np.zeros(2), np.zeros((1, 1, 3)), np.ones(3, dtype=bool), np.array([0, np.inf, 0])]
    )
    def test_mapping_rejects_missing_or_unsafe_input_channels(self, values: np.ndarray) -> None:
        mapping = VectorMapping.parse({"indices": [2, 0], "scale": [1, 1], "offset": [0, 0]}, "mapping")

        with pytest.raises(ValueError):
            mapping.apply(values)

    @pytest.mark.parametrize("behavior", ["reject", "clip"])
    def test_deltas_anchor_to_raw_state_once_and_do_not_special_case_grippers(
        self, tmp_path: Path, config_source: dict[str, Any], behavior: str
    ) -> None:
        config_source["evaluation"]["joint_limit_behavior"] = behavior
        config = _load(tmp_path, config_source)
        state = np.array([10, 20, 30], dtype=np.float64)
        actions = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float64)

        decoded = config.decode_actions(actions, state)

        np.testing.assert_array_equal(decoded, [[17, -0.75, 30.875], [23, -3.75, 32.375], [29, -6.75, 33.875]])
        np.testing.assert_array_equal(state, [10, 20, 30])
        np.testing.assert_array_equal(actions, [[1, 2, 3], [4, 5, 6], [7, 8, 9]])
        assert decoded.shape == (3, 3)
        assert config.joint_limit_behavior == behavior

    @pytest.mark.parametrize("shape", [(2, 3), (4, 3), (3, 2), (3,), (1, 3, 3)])
    def test_action_chunks_are_not_padded_or_truncated(self, config: EvaluationConfig, shape: tuple[int, ...]) -> None:
        with pytest.raises(ValueError):
            config.decode_actions(np.zeros(shape), np.zeros(3))


class TestArtifactIdentity:
    def test_file_identity_hashes_bytes_and_records_filesystem_identity(self, tmp_path: Path) -> None:
        source = tmp_path / "source.bin"
        contents = b"small opaque CPU fixture\x00\xff"
        source.write_bytes(contents)

        identity = file_identity(source)

        assert identity["path"] == identity["resolved_path"] == str(source)
        assert identity["sha256"] == hashlib.sha256(contents).hexdigest()
        assert identity["signature"] == [
            getattr(source.stat(), field) for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        ]
        unchanged([identity])

    @pytest.mark.parametrize("mutation", ["contents", "inode", "symlink"])
    def test_input_drift_invalidates_saved_identity(self, tmp_path: Path, mutation: str) -> None:
        source = tmp_path / "source.bin"
        source.write_bytes(b"original")
        link = tmp_path / "source-link"
        link.symlink_to(source)
        observed = link if mutation == "symlink" else source
        identity = file_identity(observed)
        if mutation == "contents":
            source.write_bytes(b"modified")
        else:
            replacement = tmp_path / "replacement.bin"
            replacement.write_bytes(b"original")
            if mutation == "inode":
                replacement.replace(source)
            else:
                link.unlink()
                link.symlink_to(replacement)

        with pytest.raises(ValueError, match="Input changed"):
            unchanged([identity])

    def test_mutation_while_hashing_is_detected_at_the_filesystem_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "source.bin"
        source.write_bytes(b"original")
        original_open = Path.open

        @contextmanager
        def changing_open(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Iterator[BinaryIO]:
            with original_open(path, mode, *args, **kwargs) as stream:
                yield stream
            if path == source and mode == "rb":
                with original_open(source, "ab") as stream:
                    stream.write(b"changed")

        monkeypatch.setattr(Path, "open", changing_open)

        with pytest.raises(ValueError, match="File changed while hashing"):
            file_identity(source)

    @pytest.mark.parametrize("kind", ["directory", "fifo"])
    def test_file_identity_requires_a_regular_file(self, tmp_path: Path, kind: str) -> None:
        source = tmp_path / "not-a-file"
        if kind == "directory":
            source.mkdir()
        else:
            os.mkfifo(source)

        with pytest.raises(ValueError, match="Not a regular file"):
            file_identity(source)

    def test_checkpoint_fingerprint_uses_relative_paths_and_content_not_self_digest(
        self, checkpoint: Path, tmp_path: Path
    ) -> None:
        copied = tmp_path / "relocated"
        copied.mkdir()
        for source in sorted(checkpoint.rglob("*"), reverse=True):
            if source.is_file():
                target = copied / source.relative_to(checkpoint)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
        expected = {
            source.relative_to(checkpoint).as_posix(): {
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "size_bytes": source.stat().st_size,
            }
            for source in checkpoint.rglob("*")
            if source.is_file()
        }

        first, second = fingerprint(checkpoint), fingerprint(copied)

        assert first["files"] == second["files"] == expected
        expected_digest = hashlib.sha256(
            json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert first["sha256"] == second["sha256"] == expected_digest
        assert first["path"] != second["path"]

    @pytest.mark.parametrize("mutation", ["path", "normalization", "embedded_digest"])
    def test_checkpoint_relative_paths_and_all_asset_bytes_affect_fingerprint(
        self, checkpoint: Path, mutation: str
    ) -> None:
        before = fingerprint(checkpoint)
        asset = checkpoint / "assets" / "normalization.json"
        if mutation == "path":
            asset.rename(asset.with_name("renamed.json"))
        elif mutation == "normalization":
            asset.write_bytes(b'{"mean":[9,8,7]}\n')
        else:
            (checkpoint / "config.json").write_text(json.dumps({"sha256": before["sha256"]}), encoding="utf-8")

        assert fingerprint(checkpoint)["sha256"] != before["sha256"]

    @pytest.mark.parametrize("kind", ["file", "directory", "dangling", "fifo"])
    def test_checkpoint_inventory_rejects_symlinks_and_nonregular_files(
        self, checkpoint: Path, tmp_path: Path, kind: str
    ) -> None:
        entry = checkpoint / "unsafe"
        if kind == "fifo":
            os.mkfifo(entry)
        else:
            target = {
                "file": checkpoint / "config.json",
                "directory": checkpoint / "assets",
                "dangling": tmp_path / "missing",
            }[kind]
            entry.symlink_to(target, target_is_directory=kind == "directory")

        with pytest.raises(ValueError, match=r"symlinks|regular file"):
            fingerprint(checkpoint)

    def test_checkpoint_must_be_a_nonempty_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="empty"):
            fingerprint(tmp_path)
        file = tmp_path / "single-file"
        file.write_bytes(b"data")
        with pytest.raises(ValueError, match="local directory"):
            fingerprint(file)


class TestArtifactPublication:
    def test_fresh_directory_allocates_only_a_disjoint_output(self, tmp_path: Path, checkpoint: Path) -> None:
        output = tmp_path / "results" / "run"

        assert fresh_directory(output, [checkpoint]) == output
        assert output.is_dir()
        assert not list(output.iterdir())

    @pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling"])
    def test_existing_or_symlink_outputs_are_not_reused(self, tmp_path: Path, kind: str) -> None:
        output = tmp_path / "output"
        if kind == "file":
            output.write_bytes(b"preserve")
        elif kind == "directory":
            output.mkdir()
        else:
            target = tmp_path / "target"
            if kind == "symlink":
                target.mkdir()
            output.symlink_to(target)

        with pytest.raises(ValueError, match="must be fresh"):
            fresh_directory(output, [])
        if kind == "file":
            assert output.read_bytes() == b"preserve"

    @pytest.mark.parametrize("overlap", ["equal", "inside", "ancestor", "symlink_ancestor"])
    def test_fresh_output_cannot_overlap_protected_paths(self, tmp_path: Path, overlap: str) -> None:
        protected = tmp_path / "input"
        output = tmp_path / "output"
        if overlap == "equal":
            protected = output
        elif overlap == "inside":
            protected.mkdir()
            output = protected / "output"
        elif overlap == "ancestor":
            protected = output / "input"
        else:
            protected.mkdir()
            alias = tmp_path / "alias"
            alias.symlink_to(protected, target_is_directory=True)
            output = alias / "output"

        with pytest.raises(ValueError, match="overlaps protected input"):
            fresh_directory(output, [protected])
        assert not output.exists()

    def test_json_publication_never_overwrites_without_explicit_replacement(self, tmp_path: Path) -> None:
        path = tmp_path / "progress.json"
        write_json(path, {"z": 2, "a": 1})
        original = path.read_bytes()

        with pytest.raises(FileExistsError):
            write_json(path, {"a": 3})

        assert path.read_bytes() == original == b'{"a":1,"z":2}\n'
        assert sorted(item.name for item in tmp_path.iterdir()) == ["progress.json"]
        write_json(path, {"a": 3}, replace=True)
        assert read_json(path) == {"a": 3}

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
    @pytest.mark.parametrize("replace", [False, True])
    def test_nonfinite_manifests_leave_no_partial_output_or_overwrite(
        self, tmp_path: Path, value: float, replace: bool
    ) -> None:
        path = tmp_path / "manifest.json"
        if replace:
            write_json(path, {"complete": False})
        before = path.read_bytes() if replace else None

        with pytest.raises(ValueError):
            write_json(path, {"metrics": {"value": value}}, replace=replace)

        assert (path.read_bytes() if path.exists() else None) == before
        assert list(tmp_path.glob(".manifest.json.*")) == []

    @pytest.mark.parametrize("kind", ["leaf", "parent", "dangling"])
    def test_json_publication_refuses_symlink_destinations(self, tmp_path: Path, kind: str) -> None:
        target = tmp_path / "target"
        target.mkdir()
        original = target / "record.json"
        original.write_bytes(b"preserve")
        alias = tmp_path / "alias"
        if kind == "parent":
            alias.symlink_to(target, target_is_directory=True)
            path = alias / "record.json"
        else:
            alias.symlink_to(original if kind == "leaf" else target / "missing.json")
            path = alias

        with pytest.raises(ValueError, match="Output parent changed"):
            write_json(path, {"overwrite": True}, replace=True)
        assert original.read_bytes() == b"preserve"

    def test_manifest_inventories_artifacts_before_publication(self, tmp_path: Path) -> None:
        (tmp_path / "nested").mkdir()
        payload = tmp_path / "nested" / "trace.json"
        write_json(payload, {"measured_state": [1, 2, 3]})

        artifacts = inventory(tmp_path)
        write_json(tmp_path / "manifest.json", {"complete": True, "artifacts": artifacts})

        assert artifacts == {
            "nested/trace.json": {
                "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
                "size_bytes": payload.stat().st_size,
            }
        }
        assert "manifest.json" not in artifacts
        assert read_json(tmp_path / "manifest.json")["artifacts"] == artifacts

    def test_evidence_inventory_refuses_symlinks(self, tmp_path: Path) -> None:
        (tmp_path / "link").symlink_to(tmp_path / "missing")

        with pytest.raises(ValueError, match="Evidence must not contain symlinks"):
            inventory(tmp_path)


class TestCliAndImportBoundary:
    @pytest.mark.parametrize("operation", ["serve", "run"])
    def test_cli_parses_only_explicit_runtime_choices(self, operation: str, tmp_path: Path) -> None:
        arguments = [
            operation,
            "--config",
            str(tmp_path / "config.json"),
            "--output",
            str(tmp_path / "output"),
            "--device",
            "cpu",
            "--host",
            "127.0.0.1",
            "--port",
            "12345",
            "--socket",
            str(tmp_path / "policy.sock"),
        ]
        if operation == "run":
            arguments += ["--accept-nvidia-terms", "--accept-nvidia-privacy"]

        args = create_parser().parse_args(arguments)

        assert args.operation == operation and args.device == "cpu"
        assert args.config == tmp_path / "config.json" and args.output == tmp_path / "output"
        assert args.host == "127.0.0.1" and args.port == 12345
        assert args.socket == tmp_path / "policy.sock"
        if operation == "run":
            assert args.accept_nvidia_terms is True and args.accept_nvidia_privacy is True

    def test_native_consent_flags_are_independent_and_default_false(self) -> None:
        parser = create_parser()
        arguments = ["run", "--config", "config.json", "--output", "output"]

        default = parser.parse_args(arguments)
        terms_only = parser.parse_args([*arguments, "--accept-nvidia-terms"])

        assert default.accept_nvidia_terms is False and default.accept_nvidia_privacy is False
        assert terms_only.accept_nvidia_terms is True and terms_only.accept_nvidia_privacy is False

    @pytest.mark.parametrize(
        "arguments",
        [
            [],
            ["inspect"],
            ["serve", "--config", "config.json"],
            ["run", "--output", "output"],
            ["inspect", "--conf", "config.json"],
            ["fingerprint", "--check", "checkpoint"],
            ["inspect", "--config", "config.json", "--oracle", "state"],
        ],
    )
    def test_cli_rejects_missing_unknown_and_abbreviated_arguments(self, arguments: list[str]) -> None:
        with pytest.raises(SystemExit) as error:
            create_parser().parse_args(arguments)
        assert error.value.code == 2

    def test_fingerprint_cli_runs_on_opaque_local_files(
        self, checkpoint: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["fingerprint", "--checkpoint", str(checkpoint)])
        result = json.loads(capsys.readouterr().out)

        assert code == 0 and result["status"] == "pass"
        assert result["model_loaded"] is False
        assert result["checkpoint"] == fingerprint(checkpoint)

    def test_invalid_config_cli_returns_structured_input_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "invalid.json"
        path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

        code = main(["inspect", "--config", str(path)])
        result = json.loads(capsys.readouterr().out)

        assert code == 2 and result["status"] == "failed"
        assert "Duplicate JSON keys" in result["error"]

    def test_all_harness_modules_import_without_model_or_simulator_runtimes(self) -> None:
        evaluation = Path(__file__).resolve().parents[3]
        script = textwrap.dedent("""
            from __future__ import annotations

            import importlib
            import importlib.abc
            import json
            import sys
            from importlib.machinery import ModuleSpec
            from pathlib import Path
            from types import ModuleType

            blocked = {"torch", "rho", "openpi", "isaaclab", "isaacsim", "omni", "pxr", "jax", "training"}

            class RuntimeGuard(importlib.abc.MetaPathFinder):
                def find_spec(
                    self, fullname: str, path: list[str] | None = None, target: ModuleType | None = None
                ) -> ModuleSpec | None:
                    if fullname.split(".")[0] in blocked:
                        raise AssertionError(f"Heavy runtime import attempted: {fullname}")
                    return None

            sys.meta_path.insert(0, RuntimeGuard())
            root = Path(sys.argv[1])
            sys.path.insert(0, str(root))
            modules = ["sil.vla"] + [
                f"sil.vla.{path.stem}" for path in sorted((root / "sil" / "vla").glob("*.py"))
                if path.stem != "__init__"
            ]
            for name in modules:
                importlib.import_module(name)
            loaded = sorted(name for name in sys.modules if name.split(".")[0] in blocked)
            assert not loaded, loaded
            print(json.dumps({"modules": modules, "heavy_modules": loaded}))
        """)

        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(evaluation)],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        report = json.loads(result.stdout)

        assert {"sil.vla.config", "sil.vla.artifacts", "sil.vla.transport", "sil.vla.__main__"} <= set(
            report["modules"]
        )
        assert report["heavy_modules"] == []
