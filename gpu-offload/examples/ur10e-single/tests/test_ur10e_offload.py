"""Tests for the checkpoint checks that gate UR10e inference."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import torch

from training.vla.scripts.finalize_candidate import finalize_candidate
from training.vla.scripts.preflight_dataset import build_dataset_record
from training.vla.scripts.vla_contracts import RecordKind, write_record

# The image puts both modules on /workspace/app, and run_ur10e imports ur10e_offload by name.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_ur10e
import ur10e_offload
from ur10e_offload import CANDIDATE_MANIFEST_NAME, CheckpointVerificationError, PolicyRunner

_CREATED_AT = "2026-10-22T00:00:00Z"


def _stage_candidate(tmp_path: Path, policy_type: str = "pi05") -> tuple[Path, dict[str, Any]]:
    """Finalize a candidate with the training pipeline, then stage it the way a deployment does."""
    policy_root = tmp_path / "checkpoints/last/pretrained_model"
    policy_root.mkdir(parents=True)
    (policy_root / "config.json").write_text('{"type": "pi05"}\n', encoding="utf-8")
    (policy_root / "model.safetensors").write_bytes(b"weights")
    (policy_root / "policy_preprocessor.json").write_text("{}\n", encoding="utf-8")
    dataset = tmp_path / "dataset"
    (dataset / "meta").mkdir(parents=True)
    (dataset / "data").mkdir()
    features = {"observation.images.scene": {"dtype": "video", "shape": [360, 640, 3]}}
    (dataset / "meta/info.json").write_text(json.dumps({"total_episodes": 1, "features": features}), encoding="utf-8")
    (dataset / "data/episode.parquet").write_bytes(b"episode")
    dataset_manifest = tmp_path / "dataset.json"
    write_record(dataset_manifest, build_dataset_record(dataset, "azureml:ur10e-data:1", "org/ur10e", _CREATED_AT))
    training_record = tmp_path / "training-record.json"
    write_record(
        training_record,
        {
            "schema_version": 1,
            "kind": RecordKind.RUN.value,
            "created_at": _CREATED_AT,
            "run_id": "run-1",
            "identities": {"dataset": "a" * 64},
            "effective_batch": {"micro_batch_per_rank": 1, "world_size": 1, "accumulation_steps": 1, "total": 1},
        },
    )
    candidate = tmp_path / "candidate"
    record = finalize_candidate(
        tmp_path / "checkpoints",
        training_record,
        dataset_manifest,
        candidate,
        tmp_path / "candidate_manifest",
        "lerobot-pi",
        policy_type,
        "ur10e-training-pipeline",
    )
    shutil.copy2(tmp_path / "candidate_manifest" / CANDIDATE_MANIFEST_NAME, candidate / CANDIDATE_MANIFEST_NAME)
    return candidate, record


def _edit_manifest(candidate: Path, change: Callable[[dict[str, Any]], object]) -> None:
    path = candidate / CANDIDATE_MANIFEST_NAME
    record = json.loads(path.read_text(encoding="utf-8"))
    change(record)
    path.write_text(json.dumps(record), encoding="utf-8")


def test_given_pipeline_candidate_when_verified_then_its_lineage_is_returned(tmp_path: Path) -> None:
    candidate, record = _stage_candidate(tmp_path)

    lineage = ur10e_offload.verify_candidate_manifest(str(candidate))

    assert lineage == {
        "artifact_manifest_sha256": record["artifact_manifest_sha256"],
        "training_fingerprint": record["training_fingerprint"],
        "dataset_fingerprint": record["dataset_fingerprint"],
        "evidence_job": "ur10e-training-pipeline",
    }
    assert ur10e_offload._candidate_files(candidate) == record["files"]


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        pytest.param(
            lambda candidate: (candidate / "model.safetensors").write_bytes(b"other weights"),
            "changed: model.safetensors",
            id="replaced-weights",
        ),
        pytest.param(
            lambda candidate: (candidate / "model.safetensors").write_bytes(b"wei"),
            "changed: model.safetensors",
            id="truncated-weights",
        ),
        pytest.param(
            lambda candidate: (candidate / "model.safetensors.part.0").write_bytes(b"weights"),
            "unexpected: model.safetensors.part.0",
            id="leftover-split-part",
        ),
        pytest.param(
            lambda candidate: (candidate / "policy_preprocessor.json").unlink(),
            "missing: policy_preprocessor.json",
            id="removed-processor",
        ),
        pytest.param(
            lambda candidate: (candidate / CANDIDATE_MANIFEST_NAME).unlink(),
            "No candidate-manifest.json",
            id="no-manifest",
        ),
        pytest.param(
            lambda candidate: (candidate / CANDIDATE_MANIFEST_NAME).write_text("{", encoding="utf-8"),
            "Unable to read",
            id="malformed-manifest",
        ),
        pytest.param(
            lambda candidate: _edit_manifest(candidate, lambda record: record.update(kind="run")),
            "is not a candidate manifest",
            id="not-a-candidate-record",
        ),
        pytest.param(
            lambda candidate: _edit_manifest(candidate, lambda record: record.update(policy_type="pi0")),
            "describes a 'pi0' candidate",
            id="other-policy-type",
        ),
        pytest.param(
            lambda candidate: _edit_manifest(candidate, lambda record: record.update(artifact_manifest_sha256="0")),
            "has no valid artifact_manifest_sha256",
            id="malformed-digest",
        ),
        pytest.param(
            lambda candidate: _edit_manifest(candidate, lambda record: record.pop("evidence_job")),
            "has no evidence_job",
            id="missing-lineage",
        ),
        pytest.param(
            lambda candidate: _edit_manifest(candidate, lambda record: record["files"][0].update(sha256="0" * 64)),
            "lists files that do not match its artifact digest",
            id="edited-file-list",
        ),
    ],
)
def test_given_checkpoint_that_differs_from_its_candidate_when_verified_then_it_is_refused(
    tmp_path: Path, tamper: Callable[[Path], object], message: str
) -> None:
    candidate, _ = _stage_candidate(tmp_path)
    tamper(candidate)

    with pytest.raises(CheckpointVerificationError, match=message):
        ur10e_offload.verify_candidate_manifest(str(candidate))


class _Policy(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.action_out_proj = torch.nn.Linear(2, 2)
        self.config = SimpleNamespace(device="cpu", use_amp=False, n_action_steps=50)


def _weights(width: int = 2) -> dict[str, torch.Tensor]:
    return {"action_out_proj.weight": torch.ones(2, width), "action_out_proj.bias": torch.zeros(2)}


def _pi05_loader(weights: dict[str, torch.Tensor] | None, strict: bool = True) -> SimpleNamespace:
    """Mirror LeRobot's pi05 loader, which prints a warning and returns the policy when loading fails."""

    def from_pretrained(path: str, config: Any = None) -> _Policy:
        policy = _Policy()
        if weights is None:
            print("Returning model without loading pretrained weights")
            return policy
        try:
            policy.load_state_dict(weights, strict=strict)
        except RuntimeError as error:
            print(f"Warning: Could not load state dict: {error}")
        return policy

    return SimpleNamespace(from_pretrained=from_pretrained)


def test_given_complete_weights_when_policy_loaded_then_it_is_returned() -> None:
    original_load_state_dict = torch.nn.Module.load_state_dict

    policy = ur10e_offload.load_complete_policy(_pi05_loader(_weights()), "/models/pi05", config=None)

    assert torch.equal(policy.action_out_proj.weight, torch.ones(2, 2))
    assert torch.nn.Module.load_state_dict is original_load_state_dict


def _submodule_only(path: str, config: Any = None) -> _Policy:
    policy = _Policy()
    policy.action_out_proj.load_state_dict({"weight": torch.ones(2, 2), "bias": torch.zeros(2)})
    return policy


@pytest.mark.parametrize(
    ("loader", "message"),
    [
        pytest.param(_pi05_loader(_weights(width=3)), "failed to load the weights", id="swallowed-shape-mismatch"),
        pytest.param(_pi05_loader(None), "returned without loading the weights", id="load-skipped"),
        pytest.param(
            _pi05_loader({"action_out_proj.bias": torch.zeros(2)}, strict=False),
            "1 missing and 0 unexpected keys",
            id="partial-load",
        ),
        pytest.param(
            _pi05_loader({**_weights(), "lm_head.weight": torch.zeros(2)}, strict=False),
            "0 missing and 1 unexpected keys",
            id="extra-weights",
        ),
        pytest.param(
            SimpleNamespace(from_pretrained=_submodule_only),
            "returned without loading the weights",
            id="only-submodule-loaded",
        ),
    ],
)
def test_given_policy_without_all_its_weights_when_loaded_then_it_is_refused(loader: Any, message: str) -> None:
    original_load_state_dict = torch.nn.Module.load_state_dict

    with pytest.raises(CheckpointVerificationError, match=message):
        ur10e_offload.load_complete_policy(loader, "/models/pi05", config=None)

    assert torch.nn.Module.load_state_dict is original_load_state_dict


def test_given_swallowed_load_error_when_refused_then_the_original_error_is_preserved() -> None:
    with pytest.raises(CheckpointVerificationError) as caught:
        ur10e_offload.load_complete_policy(_pi05_loader(_weights(width=3)), "/models/pi05", config=None)

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "size mismatch" in str(caught.value.__cause__)


@pytest.fixture
def fake_lerobot(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Stand in for the three lerobot entry points PolicyRunner.load imports."""
    state = SimpleNamespace(config_type="pi05", weights=_weights(), config_reads=0, policy_loads=0)

    def read_config(path: str) -> SimpleNamespace:
        state.config_reads += 1
        return SimpleNamespace(type=state.config_type, compile_model=True)

    def load_policy(path: str, config: Any = None) -> _Policy:
        state.policy_loads += 1
        return _pi05_loader(state.weights).from_pretrained(path, config)

    modules = {
        "lerobot": ModuleType("lerobot"),
        "lerobot.configs": ModuleType("lerobot.configs"),
        "lerobot.configs.policies": ModuleType("lerobot.configs.policies"),
        "lerobot.policies": ModuleType("lerobot.policies"),
        "lerobot.policies.factory": ModuleType("lerobot.policies.factory"),
        "lerobot.policies.pi05": ModuleType("lerobot.policies.pi05"),
        "lerobot.policies.pi05.modeling_pi05": ModuleType("lerobot.policies.pi05.modeling_pi05"),
    }
    modules["lerobot.configs.policies"].PreTrainedConfig = SimpleNamespace(from_pretrained=read_config)
    modules["lerobot.policies.pi05.modeling_pi05"].PI05Policy = SimpleNamespace(from_pretrained=load_policy)
    modules["lerobot.policies.factory"].make_pre_post_processors = lambda **kwargs: ("preprocessor", "postprocessor")
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return state


def test_given_verified_candidate_when_runner_loads_then_the_stage_reports_it(
    tmp_path: Path, fake_lerobot: SimpleNamespace
) -> None:
    candidate, record = _stage_candidate(tmp_path)
    runner = PolicyRunner(str(candidate), device="cpu")

    policy, preprocessor, postprocessor = runner.load({"observation.images.scene": "observation.images.base_0_rgb"})
    info = runner.describe(policy)

    assert (preprocessor, postprocessor) == ("preprocessor", "postprocessor")
    assert info["candidate_manifest_sha256"] == record["artifact_manifest_sha256"]
    assert info["training_fingerprint"] == record["training_fingerprint"]
    assert info["evidence_job"] == "ur10e-training-pipeline"


def test_given_tampered_candidate_when_runner_loads_then_no_weights_are_read(
    tmp_path: Path, fake_lerobot: SimpleNamespace
) -> None:
    candidate, _ = _stage_candidate(tmp_path)
    (candidate / "model.safetensors").write_bytes(b"other weights")

    with pytest.raises(CheckpointVerificationError, match=r"changed: model\.safetensors"):
        PolicyRunner(str(candidate), device="cpu").load()

    assert (fake_lerobot.config_reads, fake_lerobot.policy_loads) == (0, 0)


@pytest.mark.parametrize(
    ("config_type", "weights", "message"),
    [
        pytest.param("pi0", _weights(), "holds a 'pi0' policy", id="other-policy-config"),
        pytest.param("pi05", _weights(width=3), "failed to load the weights", id="weights-do-not-fit"),
        pytest.param("pi05", None, "returned without loading the weights", id="weights-skipped"),
    ],
)
def test_given_verified_candidate_that_cannot_load_when_runner_loads_then_it_is_refused(
    tmp_path: Path,
    fake_lerobot: SimpleNamespace,
    config_type: str,
    weights: dict[str, torch.Tensor] | None,
    message: str,
) -> None:
    candidate, _ = _stage_candidate(tmp_path)
    fake_lerobot.config_type = config_type
    fake_lerobot.weights = weights

    with pytest.raises(CheckpointVerificationError, match=message):
        PolicyRunner(str(candidate), device="cpu").load()


def _events(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.startswith('{"')]


@pytest.mark.parametrize("mode", ["self-check", "headless", "record"])
def test_given_refused_checkpoint_when_control_loop_starts_then_the_robot_is_never_touched(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["run_ur10e.py", "--mode", mode, "--checkpoint-path", str(checkpoint)])

    def refuse(self: PolicyRunner, rename_map: dict[str, str] | None = None) -> None:
        raise CheckpointVerificationError(
            f"{checkpoint} does not match its candidate manifest: changed: model.safetensors"
        )

    monkeypatch.setattr(PolicyRunner, "load", refuse)
    build_robot = mocker.patch.object(run_ur10e, "_build_robot")
    mode_runners = [
        mocker.patch.object(run_ur10e, name) for name in ("_run_self_check", "_run_headless", "_run_record")
    ]

    assert run_ur10e.main() == 1

    build_robot.assert_not_called()
    for runner in mode_runners:
        runner.assert_not_called()
    events = _events(capsys.readouterr().out)
    assert [event["event"] for event in events] == ["load_failed"]
    assert "changed: model.safetensors" in events[0]["error"]


def test_given_verified_candidate_when_control_loop_starts_then_the_mode_runs_after_the_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["run_ur10e.py", "--mode", "headless", "--checkpoint-path", str(checkpoint)])
    info = {
        "device": "cuda",
        "use_amp": False,
        "n_action_steps": 50,
        "policy_class": "PI05Policy",
        "cuda_available": True,
        "cuda_device_name": "gpu",
        "hostname": "stage",
        "candidate_manifest_sha256": "c" * 64,
        "training_fingerprint": "d" * 64,
        "evidence_job": "ur10e-training-pipeline",
    }
    monkeypatch.setattr(PolicyRunner, "load", lambda self, rename_map=None: ("policy", "pre", "post"))
    monkeypatch.setattr(PolicyRunner, "describe", lambda self, policy: info)
    run_headless = mocker.patch.object(run_ur10e, "_run_headless", return_value=0)

    assert run_ur10e.main() == 0

    run_headless.assert_called_once()
    loaded = _events(capsys.readouterr().out)[0]
    assert loaded["event"] == "loaded"
    assert loaded["candidate_manifest_sha256"] == "c" * 64
    assert loaded["evidence_job"] == "ur10e-training-pipeline"
