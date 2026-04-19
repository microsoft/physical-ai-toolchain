"""Unit tests for ``sil/scripts/test-lerobot-eval.py``."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

torch = pytest.importorskip("torch")

# Stub heavy/native deps before loading the script.
if "pyarrow" not in sys.modules:
    _pa = types.ModuleType("pyarrow")
    _pa_pq = types.ModuleType("pyarrow.parquet")
    _pa_pq.read_table = MagicMock()
    _pa.parquet = _pa_pq  # type: ignore[attr-defined]
    sys.modules["pyarrow"] = _pa
    sys.modules["pyarrow.parquet"] = _pa_pq

if "av" not in sys.modules:
    sys.modules["av"] = types.ModuleType("av")

# lerobot.* imports happen inside run_inference_test only; ensure stubs exist.
for _name in ("lerobot", "lerobot.policies", "lerobot.policies.act", "lerobot.processor"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules.setdefault("lerobot.policies.act.modeling_act", types.ModuleType("lerobot.policies.act.modeling_act"))
sys.modules.setdefault("lerobot.processor.pipeline", types.ModuleType("lerobot.processor.pipeline"))

_SCRIPT = Path(__file__).resolve().parents[1] / "sil" / "scripts" / "test-lerobot-eval.py"
_spec = importlib.util.spec_from_file_location("test_lerobot_eval_script", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestResolveDevice:
    def test_cuda_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
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


class TestBuildObservation:
    def test_returns_expected_keys_and_shapes(self) -> None:
        state = np.zeros(6, dtype=np.float32)
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        obs = _mod.build_observation(state, image)
        assert set(obs.keys()) == {"observation.state", "observation.images.color"}
        assert obs["observation.state"].shape == (6,)
        assert obs["observation.images.color"].shape == (3, 4, 4)
        assert float(obs["observation.images.color"].max()) <= 1.0


class TestLoadEpisodeData:
    def test_returns_dict_of_columns(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        table = MagicMock()
        table.column_names = ["timestamp", "action"]
        table.__getitem__.side_effect = lambda k: MagicMock(to_pylist=lambda: [1, 2, 3])
        monkeypatch.setattr(_mod.pq, "read_table", lambda _p: table)
        result = _mod.load_episode_data(str(tmp_path), 0)
        assert result == {"timestamp": [1, 2, 3], "action": [1, 2, 3]}


class TestLoadVideoFrame:
    def _patch_av(self, monkeypatch: pytest.MonkeyPatch, frames: list[np.ndarray]) -> MagicMock:
        av_mod = MagicMock()
        container = MagicMock()
        stream = MagicMock()
        container.streams.video = [stream]

        def make_av_frame(arr: np.ndarray) -> MagicMock:
            f = MagicMock()
            f.to_ndarray.return_value = arr
            return f

        container.decode.return_value = [make_av_frame(a) for a in frames]
        av_mod.open.return_value = container
        monkeypatch.setitem(sys.modules, "av", av_mod)
        return container

    def test_returns_target_frame(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        frames = [np.full((2, 2, 3), i, dtype=np.uint8) for i in range(3)]
        container = self._patch_av(monkeypatch, frames)
        result = _mod.load_video_frame(str(tmp_path), 0, 1)
        assert result[0, 0, 0] == 1
        container.close.assert_called()

    def test_missing_frame_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._patch_av(monkeypatch, [np.zeros((2, 2, 3), dtype=np.uint8)])
        with pytest.raises(IndexError):
            _mod.load_video_frame(str(tmp_path), 0, 99)


# ---------------------------------------------------------------------------
# main / run_inference_test
# ---------------------------------------------------------------------------


def _make_args(**overrides) -> object:
    defaults = dict(
        policy_repo="repo",
        dataset_dir="/tmp/ds",
        episode=0,
        start_frame=0,
        num_steps=2,
        device="cpu",
        output=None,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def _setup_run_inference_test(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, num_frames: int = 5) -> dict:
    """Patch all heavy dependencies of run_inference_test and return the mocks."""
    # Mock ACTPolicy + PolicyProcessorPipeline via the modules already in sys.modules.
    act_mod = sys.modules["lerobot.policies.act.modeling_act"]
    pipeline_mod = sys.modules["lerobot.processor.pipeline"]

    policy = MagicMock()
    # parameters() must yield tensor-likes with .numel()
    param = MagicMock()
    param.numel.return_value = 1_000_000
    policy.parameters.return_value = [param, param]
    act_policy_cls = MagicMock()
    act_policy_cls.from_pretrained.return_value = policy
    monkeypatch.setattr(act_mod, "ACTPolicy", act_policy_cls, raising=False)

    preprocessor = MagicMock(side_effect=lambda x: x)
    preprocessor.steps = []
    postprocessor = MagicMock(return_value={"action": torch.zeros(1, 6)})
    postprocessor.steps = []
    pipeline_cls = MagicMock()
    pipeline_cls.from_pretrained.side_effect = [preprocessor, postprocessor]
    monkeypatch.setattr(pipeline_mod, "PolicyProcessorPipeline", pipeline_cls, raising=False)

    # Dataset info file.
    info = {
        "fps": 30,
        "features": {
            "action": {"shape": [6]},
            "observation.state": {"shape": [6]},
            "observation.images.color": {"shape": [3, 480, 640]},
        },
    }
    meta_dir = tmp_path / "meta"
    meta_dir.mkdir()
    (meta_dir / "info.json").write_text(json.dumps(info))

    # Episode parquet data.
    ep_data = {
        "timestamp": list(range(num_frames)),
        "observation.state": [[0.0] * 6 for _ in range(num_frames)],
        "action": [[0.1] * 6 for _ in range(num_frames)],
    }
    monkeypatch.setattr(_mod, "load_episode_data", lambda d, e: ep_data)
    monkeypatch.setattr(
        _mod,
        "load_video_frame",
        lambda d, e, f: np.zeros((480, 640, 3), dtype=np.uint8),
    )

    # Force device to cpu.
    monkeypatch.setattr(_mod, "resolve_device", lambda r: "cpu")
    return {"policy": policy, "preprocessor": preprocessor, "postprocessor": postprocessor}


class TestRunInferenceTest:
    def test_completes_and_writes_output(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_run_inference_test(monkeypatch, tmp_path)
        out_file = tmp_path / "preds.npz"
        args = _make_args(dataset_dir=str(tmp_path), num_steps=10, output=str(out_file))
        _mod.run_inference_test(args)
        assert out_file.exists()

    def test_no_output_skips_save(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_run_inference_test(monkeypatch, tmp_path)
        args = _make_args(dataset_dir=str(tmp_path), num_steps=2, output=None)
        _mod.run_inference_test(args)

    def test_warns_on_degenerate_outputs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
        mocks = _setup_run_inference_test(monkeypatch, tmp_path)
        # Force NaN + Inf + zero-variance predictions.
        bad = torch.full((1, 6), float("nan"))
        bad[0, 0] = float("inf")
        mocks["postprocessor"].return_value = {"action": bad}
        args = _make_args(dataset_dir=str(tmp_path), num_steps=3)
        _mod.run_inference_test(args)
        out = capsys.readouterr().out
        assert "NaN" in out
        assert "Inf" in out

    def test_zero_variance_warning(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
        _setup_run_inference_test(monkeypatch, tmp_path)
        # Default postprocessor returns zeros each step → zero variance.
        args = _make_args(dataset_dir=str(tmp_path), num_steps=3)
        _mod.run_inference_test(args)
        out = capsys.readouterr().out
        assert "mode collapse" in out


class TestMain:
    def test_missing_dataset_exits(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "argv", ["test-lerobot-eval", "--dataset-dir", str(tmp_path / "missing")])
        with pytest.raises(SystemExit) as exc_info:
            _mod.main()
        assert exc_info.value.code == 1

    def test_invokes_run_inference(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_run_inference_test(monkeypatch, tmp_path)
        called = {}

        def fake_run(args):
            called["args"] = args

        monkeypatch.setattr(_mod, "run_inference_test", fake_run)
        monkeypatch.setattr(sys, "argv", ["test-lerobot-eval", "--dataset-dir", str(tmp_path), "--num-steps", "1"])
        _mod.main()
        assert called["args"].dataset_dir == str(tmp_path)
