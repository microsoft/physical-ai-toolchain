"""Shared helpers for training test modules."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_THIS_DIR = str(Path(__file__).parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

_SRC = Path(__file__).resolve().parents[2] / "src"


def load_training_module(name: str, relative_path: str) -> ModuleType:
    """Load a training source module by file path without importing the package tree.

    This avoids pulling in torch, Isaac Sim, and other heavy dependencies that
    are unavailable in the lightweight test environment.
    """
    full_path = _SRC / relative_path
    spec = importlib.util.spec_from_file_location(name, full_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {name!r} from {full_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
