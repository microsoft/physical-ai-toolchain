from __future__ import annotations

from pathlib import Path

import pytest
from conftest import load_training_module

_MOD = load_training_module("groot_patch_n17_trainer", "training/vla/scripts/groot/patch_n17_trainer.py")

_EXPERIMENT = """def run(trainer, resume_path):
    if resume_path:
        trainer.train(resume_from_checkpoint=True)
    else:
        trainer.train(resume_from_checkpoint=True)
"""
_TRAINER = (
    "class Gr00tTrainer:\n    def train(self):\n"
    + _MOD.NONDETERMINISTIC_RESUME_BLOCK
    + "        return super().train()\n"
)


def _write_n17_checkout(root: Path) -> None:
    experiment = root / _MOD.EXPERIMENT_PATH
    experiment.parent.mkdir(parents=True)
    experiment.write_text(_EXPERIMENT, encoding="utf-8")
    (root / _MOD.TRAINER_PATH).write_text(_TRAINER, encoding="utf-8")


def test_patch_resume_calls_disables_resume_when_not_requested() -> None:
    patched = _MOD.patch_resume_calls(_EXPERIMENT, resume=False)

    assert _MOD.RESUME_CALL not in patched
    assert patched.count("trainer.train(resume_from_checkpoint=False)") == 2


def test_patch_resume_calls_keeps_resume_when_requested() -> None:
    assert _MOD.patch_resume_calls(_EXPERIMENT, resume=True).count(_MOD.RESUME_CALL) == 2


def test_patch_resume_calls_rejects_changed_upstream() -> None:
    with pytest.raises(RuntimeError, match="expected 2 upstream resume call sites, found 1"):
        _MOD.patch_resume_calls("trainer.train(resume_from_checkpoint=True)\n", resume=True)


def test_patch_deterministic_resume_replaces_reseed_block() -> None:
    patched = _MOD.patch_deterministic_resume(_TRAINER)

    assert "reset_seed" not in patched
    assert "self.args.ignore_data_skip = False" in patched


def test_patch_deterministic_resume_rejects_missing_block() -> None:
    with pytest.raises(RuntimeError, match="found 0"):
        _MOD.patch_deterministic_resume("class Gr00tTrainer:\n    pass\n")


def test_apply_with_resume_patches_both_files(tmp_path: Path) -> None:
    _write_n17_checkout(tmp_path)

    messages = _MOD.apply(tmp_path, resume=True)

    assert messages == [
        "[setup] patched N1.7 trainer resume=True",
        "[setup] patched N1.7 Trainer for deterministic data resume",
    ]
    assert "reset_seed" not in (tmp_path / _MOD.TRAINER_PATH).read_text(encoding="utf-8")


def test_apply_without_resume_leaves_trainer_unchanged(tmp_path: Path) -> None:
    _write_n17_checkout(tmp_path)

    messages = _MOD.apply(tmp_path, resume=False)

    assert messages == ["[setup] patched N1.7 trainer resume=False"]
    assert (tmp_path / _MOD.TRAINER_PATH).read_text(encoding="utf-8") == _TRAINER


def test_apply_skips_n15_layout(tmp_path: Path) -> None:
    finetune = tmp_path / "scripts" / "gr00t_finetune.py"
    finetune.parent.mkdir()
    finetune.write_text("print('n1.5')\n", encoding="utf-8")

    messages = _MOD.apply(tmp_path, resume=True)

    assert messages == ["[setup] Isaac-GR00T checkout is not the N1.7 layout; trainer patches skipped"]
    assert finetune.read_text(encoding="utf-8") == "print('n1.5')\n"


def test_main_returns_error_for_changed_upstream(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_n17_checkout(tmp_path)
    (tmp_path / _MOD.TRAINER_PATH).write_text("class Gr00tTrainer:\n    pass\n", encoding="utf-8")

    assert _MOD.main(["--groot-root", str(tmp_path), "--resume", "true"]) == 1
    assert "non-deterministic resume block" in capsys.readouterr().err


def test_main_rejects_non_boolean_resume() -> None:
    with pytest.raises(SystemExit):
        _MOD.main(["--resume", "yes"])
