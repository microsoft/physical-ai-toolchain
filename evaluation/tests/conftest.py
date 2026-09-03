"""Shared fixtures for evaluation tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Stub the cross-package training.rl.simulation_shutdown import used by sil.policy_evaluation
# while loading the lightweight checkpoint integrity module used by security tests.
if "training" not in sys.modules:
    integrity_path = Path(__file__).resolve().parents[2] / "training" / "utils" / "integrity.py"
    integrity_spec = importlib.util.spec_from_file_location("training.utils.integrity", integrity_path)
    if integrity_spec is None or integrity_spec.loader is None:
        raise RuntimeError(f"Unable to load checkpoint integrity helpers from {integrity_path}")
    integrity_module = importlib.util.module_from_spec(integrity_spec)
    integrity_spec.loader.exec_module(integrity_module)

    _training = MagicMock()
    _training.rl = MagicMock()
    _training.rl.simulation_shutdown = MagicMock()
    _training.rl.simulation_shutdown.prepare_for_shutdown = MagicMock()
    _training.utils = MagicMock()
    _training.utils.integrity = integrity_module
    sys.modules["training"] = _training
    sys.modules["training.rl"] = _training.rl
    sys.modules["training.rl.simulation_shutdown"] = _training.rl.simulation_shutdown
    sys.modules["training.utils"] = _training.utils
    sys.modules["training.utils.integrity"] = integrity_module

import numpy as np
import pytest
from sil.robot_types import IMAGE_CHANNELS, IMAGE_HEIGHT, IMAGE_WIDTH, NUM_JOINTS


@pytest.fixture
def rng() -> np.random.Generator:
    """Seeded random generator for reproducible tests."""
    return np.random.default_rng(42)


@pytest.fixture
def joint_positions() -> np.ndarray:
    """Valid joint position array of shape ``(NUM_JOINTS,)``."""
    return np.zeros(NUM_JOINTS, dtype=np.float64)


@pytest.fixture
def random_joint_positions(rng: np.random.Generator) -> np.ndarray:
    """Random joint positions in ``[-pi, pi]``."""
    return rng.uniform(-np.pi, np.pi, size=(NUM_JOINTS,))


@pytest.fixture
def color_image() -> np.ndarray:
    """Valid color image array of shape ``(IMAGE_HEIGHT, IMAGE_WIDTH, IMAGE_CHANNELS)``."""
    return np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH, IMAGE_CHANNELS), dtype=np.uint8)


@pytest.fixture
def action_arrays(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Predicted and ground truth action delta arrays of shape ``(100, NUM_JOINTS)``."""
    predicted = rng.normal(0, 0.01, size=(100, NUM_JOINTS))
    ground_truth = rng.normal(0, 0.01, size=(100, NUM_JOINTS))
    return predicted, ground_truth


@pytest.fixture
def inference_times(rng: np.random.Generator) -> np.ndarray:
    """Per-step inference times in seconds, shape ``(100,)``."""
    return rng.uniform(0.001, 0.01, size=(100,))


@pytest.fixture
def mock_azure_ml(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Inject mock Azure ML and Identity modules into ``sys.modules`` and set env vars."""
    mock_ml = MagicMock()
    mock_identity = MagicMock()

    for mod in ("azure", "azure.ai"):
        monkeypatch.setitem(sys.modules, mod, MagicMock())
    monkeypatch.setitem(sys.modules, "azure.ai.ml", mock_ml)
    monkeypatch.setitem(sys.modules, "azure.identity", mock_identity)

    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-sub-id")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "test-rg")
    monkeypatch.setenv("AZUREML_WORKSPACE_NAME", "test-ws")

    return mock_ml, mock_identity
