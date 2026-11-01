"""
End-to-end test for the VLM-as-a-judge OSMO workflow on LeRobot dataset episodes.

Runs under the local platform profile. Judges every episode of a dataset in the data root
(``so101-learner``, or the one ``E2E_JUDGE_DATASET`` names) with the pinned Qwen3-VL-4B model on
one GPU, publishes the verdicts as an OSMO dataset, downloads them, and requires a schema-valid
verdict (outcome, confidence, and per-frame progress) for every episode. Verdicts are
informational: the test records the verdict on the episode the capture labeled a success rather
than asserting it. The RL lifecycle test judges rollout videos the same way.

```shell
PLATFORM_PROFILE=local uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_vlm_judge.py
```
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.e2e._common import e2e_name, log_e2e
from tests.e2e._judge import (
    JUDGE_TASK_NAME,
    JudgeVerdict,
    delete_osmo_dataset,
    parse_judgments,
    read_judgments,
    submit_osmo_judge,
)
from tests.e2e._lerobot_dataset import data_root_dataset_source
from tests.e2e._osmo import assert_workflow_task_succeeded, monitor_osmo_workflow
from tests.e2e._profile import LOCAL_PROFILE, PlatformProfile, profile_selected

_DATASET = "so101-learner"
_DATASET_VARIABLE = "E2E_JUDGE_DATASET"
# The sim workspace capture recorded episode 0 of so101-learner as a successful pick and place.
_LABELED_SUCCESS_EPISODE = 0
# The first run downloads the pinned model into the shared cache, which takes longer than judging.
_JUDGE_TIMEOUT_MINUTES = 45


def _verdict(verdicts: list[JudgeVerdict], episode_index: int) -> JudgeVerdict | None:
    suffix = f"episode_{episode_index:06d}"
    return next((verdict for verdict in verdicts if verdict.episode_id.endswith(suffix)), None)


def test_parse_judgments_reads_every_verdict() -> None:
    lines = [
        {
            "episode_id": "pick/episode_000000",
            "outcome_success": True,
            "outcome_confidence": 0.9,
            "progress_per_frame": [0.1, 1.0],
        },
        {
            "episode_id": "pick/episode_000001",
            "outcome_success": False,
            "outcome_confidence": 0.6,
            "progress_per_frame": [0.2],
        },
    ]

    verdicts = parse_judgments("\n".join(json.dumps(line) for line in lines) + "\n")

    assert [verdict.success for verdict in verdicts] == [True, False]
    assert _verdict(verdicts, 1) == JudgeVerdict("pick/episode_000001", False, 0.6, (0.2,))
    assert _verdict(verdicts, 2) is None


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({"outcome_success": "yes", "outcome_confidence": 0.9, "progress_per_frame": [1.0]}, "outcome_success"),
        ({"outcome_success": True, "outcome_confidence": float("nan"), "progress_per_frame": [1.0]}, "confidence"),
        ({"outcome_success": True, "outcome_confidence": 0.9, "progress_per_frame": []}, "progress"),
    ],
)
def test_parse_judgments_rejects_incomplete_verdicts(record: dict[str, object], message: str) -> None:
    with pytest.raises(AssertionError, match=message):
        parse_judgments(json.dumps(record))


def test_parse_judgments_rejects_an_empty_file() -> None:
    with pytest.raises(AssertionError, match="no verdicts"):
        parse_judgments("\n")


def test_parse_judgments_reads_an_inconclusive_verdict() -> None:
    record = {
        "episode_id": "pick/episode_000002",
        "outcome_success": None,
        "outcome_confidence": 0.5,
        "progress_per_frame": [0.3, 0.6],
    }

    verdicts = parse_judgments(json.dumps(record))

    assert verdicts == [JudgeVerdict("pick/episode_000002", None, 0.5, (0.3, 0.6))]


def test_parse_judgments_rejects_a_verdict_without_an_outcome() -> None:
    record = {"episode_id": "pick/episode_000003", "outcome_confidence": 0.9, "progress_per_frame": [1.0]}

    with pytest.raises(AssertionError, match="outcome_success"):
        parse_judgments(json.dumps(record))


@pytest.mark.e2e
@pytest.mark.skipif(
    not profile_selected(LOCAL_PROFILE),
    reason="The judge validation runs under the local platform profile on a dataset in the data root",
)
@pytest.mark.usefixtures("ensure_gpu_nodes_available")
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_osmo_vlm_judge_dataset_e2e(
    request: pytest.FixtureRequest,
    repo_root: Path,
    platform_profile: PlatformProfile,
) -> None:
    name = os.environ.get(_DATASET_VARIABLE, "").strip() or _DATASET
    data_root_dataset_source(platform_profile.datasets_host_dir, name)
    info = json.loads((Path(platform_profile.datasets_host_dir) / name / "meta" / "info.json").read_text())
    episodes = int(info["total_episodes"])
    log_e2e(f"Starting OSMO VLM judge e2e test over {episodes} episodes of {name}")

    output_dataset = e2e_name("judge-verdicts")
    request.addfinalizer(lambda: delete_osmo_dataset(repo_root, output_dataset))
    workflow = submit_osmo_judge(
        repo_root, platform_profile, input_args=("--local-dataset", name), output_dataset=output_dataset
    )
    monitor_osmo_workflow(
        request, workflow, repo_root, JUDGE_TASK_NAME, phase="VLM judge", timeout_minutes=_JUDGE_TIMEOUT_MINUTES
    )
    assert_workflow_task_succeeded(workflow, repo_root, JUDGE_TASK_NAME)

    verdicts = read_judgments(repo_root, output_dataset)
    if len(verdicts) != episodes:
        raise AssertionError(f"The judge wrote {len(verdicts)} verdicts for {episodes} episodes of {name}")
    for verdict in verdicts:
        log_e2e(f"Verdict {verdict.episode_id}: success={verdict.success} confidence={verdict.confidence:.3f}")
    if name == _DATASET:
        labeled = _verdict(verdicts, _LABELED_SUCCESS_EPISODE)
        assert labeled is not None, f"No verdict for the labeled success episode {_LABELED_SUCCESS_EPISODE}"
        log_e2e(
            f"Labeled success episode {_LABELED_SUCCESS_EPISODE}: judged success={labeled.success} "
            f"confidence={labeled.confidence:.3f}"
        )
    log_e2e("OSMO VLM judge e2e test finished successfully")
