"""Patch the Isaac-GR00T N1.7 trainer for controlled, deterministic resume.

Upstream N1.7 always calls ``trainer.train(resume_from_checkpoint=True)`` and,
on resume, reseeds the dataset and disables batch skipping, which makes a
resumed run diverge from an uninterrupted one. This script sets the resume flag
explicitly and restores checkpoint RNG state with Trainer batch skipping.

Only the N1.7 layout (``gr00t/experiment/experiment.py``) is patched; older
layouts such as N1.5 resume through their own finetune script and are left
unchanged. Each patch requires the exact upstream text so an Isaac-GR00T ref
change fails loudly instead of silently skipping the patch.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

EXPERIMENT_PATH = Path("gr00t/experiment/experiment.py")
TRAINER_PATH = Path("gr00t/experiment/trainer.py")

RESUME_CALL = "trainer.train(resume_from_checkpoint=True)"
RESUME_CALL_COUNT = 2

NONDETERMINISTIC_RESUME_BLOCK = """        # During resume, don't skip the data
        self.args.ignore_data_skip = True
        curr_global_step = self.state.global_step
        print(f"Current global step: {curr_global_step}")
        if curr_global_step > 0:
            new_seed = self.train_dataset.seed + curr_global_step
            self.train_dataset.reset_seed(new_seed)
            print(
                f"Resetting seed to {new_seed}. Please note that this will make the experiment non-reproducible."
            )
"""
DETERMINISTIC_RESUME_BLOCK = """        # Preserve checkpoint RNG state and let Trainer skip consumed batches.
        self.args.ignore_data_skip = False
        curr_global_step = self.state.global_step
        print(f"Current global step: {curr_global_step}")
"""


def patch_resume_calls(source: str, *, resume: bool) -> str:
    """Replace both upstream resume call sites with an explicit resume flag."""
    count = source.count(RESUME_CALL)
    if count != RESUME_CALL_COUNT:
        raise RuntimeError(f"expected {RESUME_CALL_COUNT} upstream resume call sites, found {count}")
    return source.replace(RESUME_CALL, f"trainer.train(resume_from_checkpoint={resume})")


def patch_deterministic_resume(source: str) -> str:
    """Replace the upstream reseed-on-resume block with checkpoint-preserving resume."""
    count = source.count(NONDETERMINISTIC_RESUME_BLOCK)
    if count != 1:
        raise RuntimeError(f"expected one upstream non-deterministic resume block, found {count}")
    return source.replace(NONDETERMINISTIC_RESUME_BLOCK, DETERMINISTIC_RESUME_BLOCK)


def apply(groot_root: Path, *, resume: bool) -> list[str]:
    """Apply the N1.7 patches under ``groot_root`` and return progress messages."""
    experiment = groot_root / EXPERIMENT_PATH
    if not experiment.is_file():
        return ["[setup] Isaac-GR00T checkout is not the N1.7 layout; trainer patches skipped"]

    experiment.write_text(
        patch_resume_calls(experiment.read_text(encoding="utf-8"), resume=resume),
        encoding="utf-8",
    )
    messages = [f"[setup] patched N1.7 trainer resume={resume}"]
    if resume:
        trainer = groot_root / TRAINER_PATH
        trainer.write_text(patch_deterministic_resume(trainer.read_text(encoding="utf-8")), encoding="utf-8")
        messages.append("[setup] patched N1.7 Trainer for deterministic data resume")
    return messages


def _bool_text(value: str) -> bool:
    if value in ("true", "false"):
        return value == "true"
    raise argparse.ArgumentTypeError(f"expected true or false, got {value!r}")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--groot-root", type=Path, default=Path(), help="Isaac-GR00T checkout root")
    parser.add_argument("--resume", type=_bool_text, required=True, help="true or false")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        messages = apply(args.groot_root, resume=args.resume)
    except (OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
