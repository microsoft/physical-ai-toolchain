"""Tests for sil/scripts/run-local-lerobot-eval.py."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

torch = pytest.importorskip("torch")

# Stub heavy / external deps before script import.
if "pyarrow" not in sys.modules:
    _pa = types.ModuleType("pyarrow")
    _pq = types.ModuleType("pyarrow.parquet")
    _pq.read_table = MagicMock()
    _pa.parquet = _pq
    sys.modules["pyarrow"] = _pa
    sys.modules["pyarrow.parquet"] = _pq

if "av" not in sys.modules:
    sys.modules["av"] = types.ModuleType("av")

for _n in ("lerobot", "lerobot.policies", "lerobot.policies.act"):
    sys.modules.setdefault(_n, types.ModuleType(_n))
sys.modules.setdefault(
    "lerobot.policies.act.modeling_act",
    types.ModuleType("lerobot.policies.act.modeling_act"),
)
sys.modules.setdefault("safetensors", types.ModuleType("safetensors"))
sys.modules.setdefault("safetensors.torch", types.ModuleType("safetensors.torch"))

_SCRIPT = Path(__file__).resolve().parents[1] / "sil" / "scripts" / "run-local-lerobot-eval.py"
_spec = importlib.util.spec_from_file_location("run_local_lerobot_eval", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


# ---------------- helpers ----------------


def _make_args(**overrides) -> SimpleNamespace:
    defaults = dict(
        policy_path="/tmp/policy",
        model_name=None,
        model_version=None,
        dataset_dir="/tmp/ds",
        episodes=1,
        output_dir="outputs/local-eval",
        device="cpu",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patch_av(monkeypatch: pytest.MonkeyPatch, frames: list[np.ndarray]) -> None:
    av_mod = types.ModuleType("av")

    class _Frame:
        def __init__(self, arr):
            self._arr = arr

        def to_ndarray(self, format="rgb24"):
            return self._arr

    class _Stream:
        pass

    class _Container:
        def __init__(self):
            self.streams = SimpleNamespace(video=[_Stream()])

        def decode(self, _stream):
            return [_Frame(f) for f in frames]

        def close(self):
            pass

    av_mod.open = lambda _path: _Container()
    monkeypatch.setitem(sys.modules, "av", av_mod)


def _write_info(dataset_dir: Path, fps: int = 30, action_dim: int = 6, state_dim: int = 6) -> dict:
    meta = dataset_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    info = {
        "fps": fps,
        "chunks_size": 1000,
        "total_episodes": 1,
        "features": {
            "observation.images.color": {"dtype": "video", "shape": [96, 96, 3]},
            "observation.state": {"dtype": "float32", "shape": [state_dim]},
            "action": {"dtype": "float32", "shape": [action_dim]},
        },
    }
    (meta / "info.json").write_text(json.dumps(info))
    return info


def _setup_run_evaluation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    n_frames: int = 4,
    n_dims: int = 6,
    write_episodes_jsonl: bool = False,
) -> dict:
    """Set up a working run_evaluation environment."""
    info = _write_info(tmp_path, action_dim=n_dims, state_dim=n_dims)

    if write_episodes_jsonl:
        (tmp_path / "meta" / "episodes.jsonl").write_text('{"episode_index": 0}\n')

    # Create chunk-000/episode_000000.parquet (first candidate).
    data_dir = tmp_path / "data" / "chunk-000"
    data_dir.mkdir(parents=True)
    data_file = data_dir / "episode_000000.parquet"
    data_file.write_bytes(b"")

    video_dir = tmp_path / "videos" / "observation.images.color" / "chunk-000"
    video_dir.mkdir(parents=True)
    video_file = video_dir / "episode_000000.mp4"
    video_file.write_bytes(b"")

    # Mock pq.read_table to return synthetic columnar data.
    table = MagicMock()
    table.column_names = ["timestamp", "observation.state", "action"]

    state_list = [np.zeros(n_dims, dtype=np.float32).tolist() for _ in range(n_frames)]
    action_list = [np.zeros(n_dims, dtype=np.float32).tolist() for _ in range(n_frames)]
    ts_list = list(range(n_frames))

    def _getitem(col):
        m = MagicMock()
        if col == "timestamp":
            m.to_pylist.return_value = ts_list
        elif col == "observation.state":
            m.to_pylist.return_value = state_list
        else:
            m.to_pylist.return_value = action_list
        return m

    table.__getitem__ = lambda self, col: _getitem(col)
    monkeypatch.setattr(_mod.pq, "read_table", lambda _path: table)

    # Stub video decoding.
    frames = [np.zeros((96, 96, 3), dtype=np.uint8) for _ in range(n_frames)]
    monkeypatch.setattr(_mod, "load_video_frames", lambda _p: frames)

    # Stub policy loader.
    policy = MagicMock()
    policy.parameters.return_value = [torch.zeros(1)]
    policy.select_action.return_value = torch.zeros(1, n_dims)
    policy.to.return_value = policy
    policy.reset = MagicMock()

    act_mod = sys.modules["lerobot.policies.act.modeling_act"]
    act_mod.ACTPolicy = SimpleNamespace(from_pretrained=lambda _path: policy)

    monkeypatch.setattr(_mod, "_load_normalizer_stats", lambda *_a, **_k: None)

    return {"policy": policy, "info": info, "frames": frames}


# ---------------- TestResolveDevice ----------------


class TestResolveDevice:
    def test_cuda_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        assert _mod.resolve_device("cuda") == "cuda"

    def test_cuda_falls_back_to_mps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
        assert _mod.resolve_device("cuda") == "mps"

    def test_mps_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
        assert _mod.resolve_device("mps") == "mps"

    def test_falls_back_to_cpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
        assert _mod.resolve_device("cuda") == "cpu"

    def test_cpu_explicit(self) -> None:
        assert _mod.resolve_device("cpu") == "cpu"


# ---------------- TestFindDataFile ----------------


class TestFindDataFile:
    def test_first_candidate(self, tmp_path: Path) -> None:
        d = tmp_path / "data" / "chunk-000"
        d.mkdir(parents=True)
        f = d / "episode_000000.parquet"
        f.write_bytes(b"")
        assert _mod.find_data_file(str(tmp_path), 0, {"chunks_size": 1000}) == str(f)

    def test_second_candidate(self, tmp_path: Path) -> None:
        d = tmp_path / "data" / "chunk-007"
        d.mkdir(parents=True)
        f = d / "file-007.parquet"
        f.write_bytes(b"")
        assert _mod.find_data_file(str(tmp_path), 7, {"chunks_size": 1000}) == str(f)

    def test_no_candidate_returns_none(self, tmp_path: Path) -> None:
        assert _mod.find_data_file(str(tmp_path), 0, {}) is None


# ---------------- TestFindVideoFile ----------------


class TestFindVideoFile:
    def test_first_candidate(self, tmp_path: Path) -> None:
        d = tmp_path / "videos" / "key" / "chunk-000"
        d.mkdir(parents=True)
        f = d / "episode_000000.mp4"
        f.write_bytes(b"")
        assert _mod.find_video_file(str(tmp_path), "key", 0, {"chunks_size": 1000}) == str(f)

    def test_second_candidate(self, tmp_path: Path) -> None:
        d = tmp_path / "videos" / "key" / "chunk-005"
        d.mkdir(parents=True)
        f = d / "file-005.mp4"
        f.write_bytes(b"")
        assert _mod.find_video_file(str(tmp_path), "key", 5, {"chunks_size": 1000}) == str(f)

    def test_no_candidate_returns_none(self, tmp_path: Path) -> None:
        assert _mod.find_video_file(str(tmp_path), "key", 0, {}) is None


# ---------------- TestLoadVideoFrames ----------------


class TestLoadVideoFrames:
    def test_decodes_frames(self, monkeypatch: pytest.MonkeyPatch) -> None:
        frames = [np.zeros((4, 4, 3), dtype=np.uint8), np.ones((4, 4, 3), dtype=np.uint8)]
        _patch_av(monkeypatch, frames)
        result = _mod.load_video_frames("/tmp/x.mp4")
        assert len(result) == 2
        assert result[0].shape == (4, 4, 3)


# ---------------- TestDownloadAmlModel ----------------


class TestDownloadAmlModel:
    def test_finds_safetensors_in_subdir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mock_azure_ml) -> None:
        download_root = tmp_path / "tmp" / "aml-model-download"
        sub = download_root / "my-model" / "pretrained_model"
        sub.mkdir(parents=True)
        (sub / "model.safetensors").write_bytes(b"")
        # Also create empty parent so iterdir returns the subdir.
        monkeypatch.chdir(tmp_path)

        mock_ml, _ = mock_azure_ml
        client = MagicMock()
        mock_ml.MLClient = MagicMock(return_value=client)

        ident_mod = sys.modules["azure.identity"]
        ident_mod.DefaultAzureCredential = MagicMock()

        result = _mod.download_aml_model("my-model", "1")
        assert result == Path("tmp/aml-model-download/my-model/pretrained_model")
        client.models.download.assert_called_once()

    def test_finds_bin_files(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mock_azure_ml) -> None:
        download_root = tmp_path / "tmp" / "aml-model-download"
        sub = download_root / "m" / "checkpoint"
        sub.mkdir(parents=True)
        (sub / "weights.bin").write_bytes(b"")
        monkeypatch.chdir(tmp_path)

        mock_ml, _ = mock_azure_ml
        mock_ml.MLClient = MagicMock(return_value=MagicMock())
        sys.modules["azure.identity"].DefaultAzureCredential = MagicMock()

        result = _mod.download_aml_model("m", "2")
        assert result == Path("tmp/aml-model-download/m/checkpoint")

    def test_returns_download_dir_when_no_match(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mock_azure_ml
    ) -> None:
        # No model_name dir created → model_path = download_dir; iterdir on download_dir
        # yields nothing matching → loop ends; returns download_dir.
        monkeypatch.chdir(tmp_path)
        mock_ml, _ = mock_azure_ml
        mock_ml.MLClient = MagicMock(return_value=MagicMock())
        sys.modules["azure.identity"].DefaultAzureCredential = MagicMock()

        result = _mod.download_aml_model("missing", "9")
        # Falls through to download_dir which has no matching files; the loop iterates
        # over [download_dir] only because model_path.is_dir() is True but no glob
        # matches; result remains download_dir.
        assert result == Path("tmp/aml-model-download")


# ---------------- TestLoadNormalizerStats ----------------


class TestLoadNormalizerStats:
    def test_no_files_returns_early(self, tmp_path: Path) -> None:
        policy = MagicMock()
        _mod._load_normalizer_stats(policy, tmp_path)
        policy.load_state_dict.assert_not_called()

    def test_skips_non_processor_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "model.safetensors").write_bytes(b"")
        st = sys.modules["safetensors.torch"]
        st.load_file = MagicMock(return_value={"observation.state.mean": torch.zeros(6)})
        policy = MagicMock()
        _mod._load_normalizer_stats(policy, tmp_path)
        # File present but lacks "processor" → stats stays empty → early return.
        policy.load_state_dict.assert_not_called()

    def test_loads_matching_buffers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "preprocessor.safetensors").write_bytes(b"")
        st = sys.modules["safetensors.torch"]
        mean_t = torch.ones(6)
        std_t = torch.full((6,), 2.0)
        bad_short = torch.zeros(1)
        st.load_file = MagicMock(
            return_value={
                "observation.state.mean": mean_t,
                "observation.state.std": std_t,
                "observation.state.median": bad_short,  # not in {mean,std,min,max}
                "noseparator": bad_short,  # rsplit gives 1 part
            }
        )
        policy = MagicMock()
        policy.state_dict.return_value = {
            "normalize_inputs.buffer_observation_state.mean": torch.zeros(6),
            "normalize_inputs.buffer_observation_state.std": torch.zeros(6),
        }
        _mod._load_normalizer_stats(policy, tmp_path)
        policy.load_state_dict.assert_called_once()
        kwargs = policy.load_state_dict.call_args
        assert kwargs.kwargs.get("strict") is False or kwargs.args[1:] == (False,)

    def test_no_matching_buffers_skips_load(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "preprocessor.safetensors").write_bytes(b"")
        st = sys.modules["safetensors.torch"]
        st.load_file = MagicMock(return_value={"observation.state.mean": torch.ones(6)})
        policy = MagicMock()
        # state_dict has no matching buffer name.
        policy.state_dict.return_value = {"unrelated.weight": torch.zeros(2)}
        _mod._load_normalizer_stats(policy, tmp_path)
        policy.load_state_dict.assert_not_called()


# ---------------- TestRunEvaluation ----------------


class TestRunEvaluation:
    def test_happy_path_writes_results(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_run_evaluation(monkeypatch, tmp_path, n_frames=5)
        out = tmp_path / "out"
        args = _make_args(dataset_dir=str(tmp_path), output_dir=str(out), episodes=1)
        _mod.run_evaluation(args)
        assert (out / "eval_results.json").exists()
        assert (out / "ep000_predictions.npz").exists()
        assert (out / "plots" / "ep000_action_deltas.png").exists()

    def test_strips_config_fields(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_run_evaluation(monkeypatch, tmp_path, n_frames=5)
        policy_dir = tmp_path / "policy"
        policy_dir.mkdir()
        cfg = {"use_peft": True, "pretrained_path": "x", "peft_config": {}, "keep": 1}
        (policy_dir / "config.json").write_text(json.dumps(cfg))
        args = _make_args(
            dataset_dir=str(tmp_path),
            output_dir=str(tmp_path / "out"),
            policy_path=str(policy_dir),
        )
        _mod.run_evaluation(args)
        new_cfg = json.loads((policy_dir / "config.json").read_text())
        assert "use_peft" not in new_cfg
        assert new_cfg["keep"] == 1

    def test_config_without_strip_fields_unchanged(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_run_evaluation(monkeypatch, tmp_path, n_frames=5)
        policy_dir = tmp_path / "policy"
        policy_dir.mkdir()
        (policy_dir / "config.json").write_text(json.dumps({"keep": 1}))
        args = _make_args(
            dataset_dir=str(tmp_path),
            output_dir=str(tmp_path / "out"),
            policy_path=str(policy_dir),
        )
        _mod.run_evaluation(args)
        assert json.loads((policy_dir / "config.json").read_text()) == {"keep": 1}

    def test_skip_no_data(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_run_evaluation(monkeypatch, tmp_path, n_frames=4)
        # Remove the data file → find_data_file returns None.
        (tmp_path / "data" / "chunk-000" / "episode_000000.parquet").unlink()
        args = _make_args(dataset_dir=str(tmp_path), output_dir=str(tmp_path / "out"))
        _mod.run_evaluation(args)
        # No metrics → early return, no eval_results.json.
        assert not (tmp_path / "out" / "eval_results.json").exists()

    def test_skip_no_video(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_run_evaluation(monkeypatch, tmp_path, n_frames=4)
        (tmp_path / "videos" / "observation.images.color" / "chunk-000" / "episode_000000.mp4").unlink()
        args = _make_args(dataset_dir=str(tmp_path), output_dir=str(tmp_path / "out"))
        _mod.run_evaluation(args)
        assert not (tmp_path / "out" / "eval_results.json").exists()

    def test_image_key_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Write info with no video/image features → falls back to default key.
        meta = tmp_path / "meta"
        meta.mkdir()
        info = {
            "fps": 30,
            "chunks_size": 1000,
            "total_episodes": 1,
            "features": {
                "observation.state": {"dtype": "float32", "shape": [6]},
                "action": {"dtype": "float32", "shape": [6]},
            },
        }
        (meta / "info.json").write_text(json.dumps(info))
        # No data file → skipped, but path through image_key fallback must execute.
        policy = MagicMock()
        policy.parameters.return_value = [torch.zeros(1)]
        policy.to.return_value = policy
        sys.modules["lerobot.policies.act.modeling_act"].ACTPolicy = SimpleNamespace(from_pretrained=lambda _p: policy)
        monkeypatch.setattr(_mod, "_load_normalizer_stats", lambda *_a, **_k: None)
        args = _make_args(dataset_dir=str(tmp_path), output_dir=str(tmp_path / "out"))
        _mod.run_evaluation(args)

    def test_episodes_jsonl_total(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_run_evaluation(monkeypatch, tmp_path, n_frames=4, write_episodes_jsonl=True)
        args = _make_args(dataset_dir=str(tmp_path), output_dir=str(tmp_path / "out"), episodes=10)
        _mod.run_evaluation(args)
        assert (tmp_path / "out" / "eval_results.json").exists()

    def test_n_dims_one_branch(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_run_evaluation(monkeypatch, tmp_path, n_frames=4, n_dims=1)
        args = _make_args(dataset_dir=str(tmp_path), output_dir=str(tmp_path / "out"))
        _mod.run_evaluation(args)
        assert (tmp_path / "out" / "eval_results.json").exists()

    def test_many_dims_small_labelsize(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_run_evaluation(monkeypatch, tmp_path, n_frames=4, n_dims=10)
        args = _make_args(dataset_dir=str(tmp_path), output_dir=str(tmp_path / "out"))
        _mod.run_evaluation(args)
        assert (tmp_path / "out" / "eval_results.json").exists()

    def test_step_print_at_end(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # n_frames=5 → num_steps=4 → step iterates 0..3, both branches of `step<3 or last` fire.
        _setup_run_evaluation(monkeypatch, tmp_path, n_frames=5)
        args = _make_args(dataset_dir=str(tmp_path), output_dir=str(tmp_path / "out"))
        _mod.run_evaluation(args)

    def test_aml_branch_invokes_download(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_run_evaluation(monkeypatch, tmp_path, n_frames=4)
        called = {}

        def fake_download(name, version):
            called["name"] = name
            called["version"] = version
            return tmp_path / "downloaded"

        monkeypatch.setattr(_mod, "download_aml_model", fake_download)
        args = _make_args(
            dataset_dir=str(tmp_path),
            output_dir=str(tmp_path / "out"),
            policy_path=None,
            model_name="m",
            model_version="1",
        )
        _mod.run_evaluation(args)
        assert called["name"] == "m" and called["version"] == "1"


# ---------------- TestMain ----------------


class TestMain:
    def test_no_policy_source_errors(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "argv", ["run-local-lerobot-eval", "--dataset-dir", str(tmp_path)])
        with pytest.raises(SystemExit):
            _mod.main()

    def test_missing_dataset_exits(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run-local-lerobot-eval",
                "--policy-path",
                "/x",
                "--dataset-dir",
                str(tmp_path / "missing"),
            ],
        )
        with pytest.raises(SystemExit) as exc:
            _mod.main()
        assert exc.value.code == 1

    def test_invokes_run_evaluation_policy_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        called = {}
        monkeypatch.setattr(_mod, "run_evaluation", lambda a: called.setdefault("a", a))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run-local-lerobot-eval",
                "--policy-path",
                "/x",
                "--dataset-dir",
                str(tmp_path),
            ],
        )
        _mod.main()
        assert called["a"].policy_path == "/x"

    def test_invokes_run_evaluation_aml(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        called = {}
        monkeypatch.setattr(_mod, "run_evaluation", lambda a: called.setdefault("a", a))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run-local-lerobot-eval",
                "--model-name",
                "m",
                "--model-version",
                "2",
                "--dataset-dir",
                str(tmp_path),
            ],
        )
        _mod.main()
        assert called["a"].model_name == "m"
