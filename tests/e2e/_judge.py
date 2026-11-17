"""Submit the VLM-as-a-judge OSMO workflow and read its verdicts for the e2e tests.

The judge writes ``judgments.jsonl`` to its task output, which ``--output-dataset`` publishes as an
OSMO dataset version; the tests download that dataset and check every verdict against the judge's
output schema: an outcome (true, false, or null for an inconclusive tie), a finite confidence, and
per-frame progress.
"""

from __future__ import annotations

import json
import math
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tests.e2e._common import format_command_failure, log_e2e, run_command
from tests.e2e._osmo import OSMOWorkflow, _osmo_workflow_from_submission
from tests.e2e._profile import PlatformProfile

JUDGE_TASK_NAME = "vlm-judge"
JUDGMENTS_FILE = "judgments.jsonl"
DATASET_BUCKET = "training"


@dataclass(frozen=True)
class JudgeVerdict:
    episode_id: str
    success: bool | None
    confidence: float
    progress: tuple[float, ...]


def parse_judgments(text: str) -> list[JudgeVerdict]:
    """Parse ``judgments.jsonl``, asserting every record carries an outcome, a confidence, and progress.

    A ``null`` outcome is an inconclusive verdict, where the judge's outcome votes tie.
    """
    verdicts = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        success = record.get("outcome_success")
        confidence = record.get("outcome_confidence")
        progress = record.get("progress_per_frame")
        if "outcome_success" not in record or not (success is None or isinstance(success, bool)):
            raise AssertionError(f"Verdict {number} has no boolean or inconclusive outcome_success: {record}")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence):
            raise AssertionError(f"Verdict {number} has no finite outcome_confidence: {record}")
        if (
            not isinstance(progress, list)
            or not progress
            or not all(
                not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
                for value in progress
            )
        ):
            raise AssertionError(f"Verdict {number} has no finite per-frame progress: {record}")
        verdicts.append(
            JudgeVerdict(
                episode_id=str(record.get("episode_id")),
                success=success,
                confidence=float(confidence),
                progress=tuple(float(value) for value in progress),
            )
        )
    if not verdicts:
        raise AssertionError(f"{JUDGMENTS_FILE} holds no verdicts")
    return verdicts


def submit_osmo_judge(
    repo_root: Path,
    profile: PlatformProfile,
    *,
    input_args: Sequence[str],
    output_dataset: str,
    instruction: str | None = None,
) -> OSMOWorkflow:
    """Submit the judge over ``input_args`` and publish its verdicts as ``output_dataset``."""
    instruction_args = ["--instruction", instruction] if instruction else []
    log_e2e(
        f"Submitting OSMO VLM judge workflow for input={' '.join(input_args)}, output_dataset={output_dataset}, "
        f"profile={profile.name}"
    )
    result = run_command(
        [
            str(repo_root / "evaluation/vlm_judge/scripts/submit-osmo-judge.sh"),
            *profile.profile_args(),
            *input_args,
            *instruction_args,
            "--output-dataset",
            output_dataset,
            "--dataset-bucket",
            DATASET_BUCKET,
            "--",
            "--format-type",
            "json",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"OSMO VLM judge e2e submission failed\n\n{format_command_failure(result)}")
    # The judge writes its verdicts to an OSMO dataset, not to a tracking experiment.
    return _osmo_workflow_from_submission(result, "", "OSMO VLM judge")


def read_judgments(repo_root: Path, dataset: str) -> list[JudgeVerdict]:
    """Download the verdicts the judge published as ``dataset`` and parse them."""
    with tempfile.TemporaryDirectory(prefix="e2e-judgments-") as download_root:
        result = run_command(
            ["osmo", "dataset", "download", f"{DATASET_BUCKET}/{dataset}", download_root],
            cwd=repo_root,
        )
        if result.returncode != 0:
            raise AssertionError(f"Failed to download judge verdicts {dataset}\n\n{format_command_failure(result)}")
        files = sorted(Path(download_root).rglob(JUDGMENTS_FILE))
        if not files:
            raise AssertionError(f"OSMO dataset {dataset} holds no {JUDGMENTS_FILE}")
        return parse_judgments(files[0].read_text(encoding="utf-8"))


def delete_osmo_dataset(repo_root: Path, dataset: str) -> None:
    """Delete every version of an OSMO dataset the test published; a dataset never created is fine."""
    result = run_command(
        ["osmo", "dataset", "delete", f"{DATASET_BUCKET}/{dataset}", "--all", "--force"],
        cwd=repo_root,
    )
    if result.returncode != 0 and "not found" not in (result.stdout + result.stderr).lower():
        raise AssertionError(f"Failed to delete OSMO dataset {dataset}\n\n{format_command_failure(result)}")
    log_e2e(f"Deleted OSMO dataset {DATASET_BUCKET}/{dataset}")
