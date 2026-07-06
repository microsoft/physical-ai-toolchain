"""Unit tests for ``sil.policy_runner``."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from sil.hf_revision import resolve_hf_revision  # noqa: E402
from sil.policy_runner import InferenceMetrics, PolicyRunner, _resolve_device  # noqa: E402
from sil.robot_types import NUM_JOINTS, JointPositionCommand, RobotObservation  # noqa: E402


class TestResolveDevice:
    """Device resolution with CUDA / MPS / CPU fallback chain."""

    def test_cuda_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        assert _resolve_device("cuda") == "cuda"

    def test_cuda_falls_back_to_mps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
        assert _resolve_device("cuda") == "mps"

    def test_mps_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
        assert _resolve_device("mps") == "mps"

    def test_mps_falls_back_to_cpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
        assert _resolve_device("mps") == "cpu"

    def test_cpu_always_returns_cpu(self) -> None:
        assert _resolve_device("cpu") == "cpu"

    def test_all_unavailable_returns_cpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
        assert _resolve_device("cuda") == "cpu"


class TestResolveHfRevision:
    """HuggingFace revision pinning for remote policy repositories."""

    def test_remote_repo_requires_revision(self) -> None:
        with pytest.raises(ValueError, match="revision is required"):
            resolve_hf_revision("owner/policy", None)

    def test_blank_repo_requires_path(self) -> None:
        with pytest.raises(ValueError, match="repository or local path is required"):
            resolve_hf_revision(" ", None)

    def test_remote_repo_allows_explicit_revision(self) -> None:
        assert (
            resolve_hf_revision("owner/policy", "0123456789abcdef0123456789abcdef01234567")
            == "0123456789abcdef0123456789abcdef01234567"
        )

    def test_remote_repo_rejects_non_sha_revision(self) -> None:
        with pytest.raises(ValueError, match="remote HuggingFace repositories require"):
            resolve_hf_revision("owner/policy", "main")

    def test_existing_local_path_allows_missing_revision(self, tmp_path: Path) -> None:
        assert resolve_hf_revision(str(tmp_path), None) is None

    def test_relative_dir_collision_is_treated_as_remote(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # A bare "org/name" that coincidentally exists as a local directory must still
        # require a pin; snapshot_download() would otherwise fetch it from the Hub at HEAD.
        (tmp_path / "org" / "name").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="revision is required"):
            resolve_hf_revision("org/name", None)

    def test_explicit_relative_local_path_allows_missing_revision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "model").mkdir()
        monkeypatch.chdir(tmp_path)
        assert resolve_hf_revision("./model", None) is None


class TestInferenceMetrics:
    """InferenceMetrics dataclass defaults and computed properties."""

    def test_defaults_are_zero(self) -> None:
        m = InferenceMetrics()
        assert m.steps == 0
        assert m.total_inference_s == 0.0
        assert m.total_preprocess_s == 0.0
        assert m.chunk_queries == 0

    def test_avg_inference_ms(self) -> None:
        m = InferenceMetrics(steps=10, total_inference_s=0.5)
        assert m.avg_inference_ms == pytest.approx(50.0)

    def test_avg_preprocess_ms(self) -> None:
        m = InferenceMetrics(steps=10, total_preprocess_s=0.2)
        assert m.avg_preprocess_ms == pytest.approx(20.0)

    def test_zero_steps_avoids_division_by_zero(self) -> None:
        m = InferenceMetrics()
        assert m.avg_inference_ms == 0.0
        assert m.avg_preprocess_ms == 0.0


class TestPolicyRunner:
    """PolicyRunner with mock policy and processors."""

    @pytest.fixture
    def action_tensor(self) -> torch.Tensor:
        return torch.randn(1, NUM_JOINTS)

    @pytest.fixture
    def runner(self, action_tensor: torch.Tensor) -> PolicyRunner:
        policy = MagicMock()
        policy.select_action.return_value = action_tensor
        preprocessor = MagicMock(side_effect=lambda x: x)
        postprocessor = MagicMock(return_value={"action": action_tensor})
        return PolicyRunner(policy, preprocessor, postprocessor, "cpu")

    def test_device_property(self, runner: PolicyRunner) -> None:
        assert runner.device == "cpu"

    def test_reset_clears_metrics_and_policy(self, runner: PolicyRunner) -> None:
        runner._metrics.steps = 5
        runner.reset()
        assert runner.metrics.steps == 0
        runner._policy.reset.assert_called_once()

    def test_step_null_image_returns_zeros(
        self,
        runner: PolicyRunner,
        joint_positions: np.ndarray,
    ) -> None:
        obs = RobotObservation(joint_positions=joint_positions)
        cmd = runner.step(obs)
        np.testing.assert_array_equal(cmd.positions, np.zeros(NUM_JOINTS, dtype=np.float32))
        assert cmd.timestamp_s == 0.0

    def test_step_runs_full_pipeline(
        self,
        joint_positions: np.ndarray,
        color_image: np.ndarray,
        action_tensor: torch.Tensor,
    ) -> None:
        policy = MagicMock()
        policy.select_action.return_value = action_tensor
        preprocessor = MagicMock(side_effect=lambda x: x)
        postprocessor = MagicMock(return_value={"action": action_tensor})
        runner = PolicyRunner(policy, preprocessor, postprocessor, "cpu")

        obs = RobotObservation(
            joint_positions=joint_positions,
            color_image=color_image,
            timestamp_s=1.5,
        )
        cmd = runner.step(obs)

        assert isinstance(cmd, JointPositionCommand)
        assert cmd.positions.shape == (NUM_JOINTS,)
        assert cmd.timestamp_s == 1.5
        preprocessor.assert_called_once()
        policy.select_action.assert_called_once()
        postprocessor.assert_called_once()

    def test_step_increments_metrics(
        self,
        runner: PolicyRunner,
        joint_positions: np.ndarray,
        color_image: np.ndarray,
    ) -> None:
        obs = RobotObservation(joint_positions=joint_positions, color_image=color_image)
        runner.step(obs)
        runner.step(obs)
        assert runner.metrics.steps == 2
        assert runner.metrics.total_inference_s >= 0
        assert runner.metrics.total_preprocess_s >= 0


class TestPolicyRunnerFromPretrained:
    """from_pretrained classmethod with mocked lerobot imports."""

    @pytest.fixture
    def lerobot_mocks(self, monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
        mock_act = MagicMock()
        mock_pipeline = MagicMock()
        for mod in ("lerobot", "lerobot.policies", "lerobot.policies.act", "lerobot.processor"):
            monkeypatch.setitem(sys.modules, mod, MagicMock())
        monkeypatch.setitem(sys.modules, "lerobot.policies.act.modeling_act", mock_act)
        monkeypatch.setitem(sys.modules, "lerobot.processor.pipeline", mock_pipeline)
        return mock_act, mock_pipeline

    def test_loads_local_path_without_revision(
        self,
        monkeypatch: pytest.MonkeyPatch,
        lerobot_mocks: tuple[MagicMock, MagicMock],
        tmp_path: Path,
    ) -> None:
        mock_act, mock_pipeline = lerobot_mocks
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)

        runner = PolicyRunner.from_pretrained(str(tmp_path), device="cuda")

        assert runner.device == "cpu"
        mock_act.ACTPolicy.from_pretrained.assert_called_once_with(str(tmp_path), revision=None)
        mock_act.ACTPolicy.from_pretrained.return_value.to.assert_called_once_with("cpu")
        assert mock_pipeline.PolicyProcessorPipeline.from_pretrained.call_count == 2

    def test_loads_remote_repo_with_revision(
        self,
        monkeypatch: pytest.MonkeyPatch,
        lerobot_mocks: tuple[MagicMock, MagicMock],
    ) -> None:
        mock_act, mock_pipeline = lerobot_mocks
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        runner = PolicyRunner.from_pretrained("test/repo", revision="0123456789abcdef0123456789abcdef01234567")

        assert runner.device == "cuda"
        mock_act.ACTPolicy.from_pretrained.assert_called_once_with(
            "test/repo", revision="0123456789abcdef0123456789abcdef01234567"
        )
        mock_act.ACTPolicy.from_pretrained.return_value.to.assert_called_once_with("cuda")
        assert (
            mock_pipeline.PolicyProcessorPipeline.from_pretrained.call_args_list[0].kwargs["revision"]
            == "0123456789abcdef0123456789abcdef01234567"
        )

    def test_remote_repo_without_revision_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        lerobot_mocks: tuple[MagicMock, MagicMock],
    ) -> None:
        mock_act, mock_pipeline = lerobot_mocks
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

        with pytest.raises(ValueError, match="revision is required"):
            PolicyRunner.from_pretrained("test/repo")

        mock_act.ACTPolicy.from_pretrained.assert_not_called()
        mock_pipeline.PolicyProcessorPipeline.from_pretrained.assert_not_called()
