"""Behavior tests for principal-scoped judge evidence served to saved-input readers.

Judge state is persisted through the real local job store and evidence is bound with the real
curation helpers. Only the dataset source-revision lookup is replaced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from evaluation.vlm_judge.judge import JudgeResult
from physical_ai_curation.adapters.job_storage import LocalJobStore
from physical_ai_curation.evidence import bind_job_evidence
from physical_ai_curation.models import DatasetLabelsFile
from physical_ai_curation.release.inputs import SavedSelection

from src.api.services.label_storage import LocalLabelStorage
from src.api.services.saved_input_service import (
    AzureJudgeStateReader,
    SavedInputError,
    ScopedJudgeEvidenceAuthority,
    ViewerSavedInputReader,
)

ECHO_CONFIG = {"execution": {"backend": "echo", "model_id": "echo"}, "agent": {}, "prompt_version": "test"}
PRINCIPAL = "scope-a"


class _Datasets:
    def __init__(self, revision: tuple[str, str]) -> None:
        self.revision = revision

    async def get_source_revision(self, dataset_id: str, episode_index: int) -> tuple[str, str]:
        return self.revision


def _judge_job(
    job_id: str,
    *,
    run_order: int,
    actor: str = PRINCIPAL,
    generation: int = 0,
    episode: int = 3,
    source: tuple[str, str] = ("source-id", "rev-1"),
) -> dict[str, Any]:
    result = JudgeResult(
        f"dataset/episode_{episode:06d}", "Pick", "echo", "test", 2, True, 1.0, 3, [0, 100], 1.0
    ).to_dict()
    job: dict[str, Any] = {
        "id": job_id,
        "dataset_id": "dataset",
        "actor": actor,
        "run_order": run_order,
        "generation": generation,
        "status": "succeeded",
        "result_kind": "judge",
        "config": ECHO_CONFIG,
        "config_revision": "config",
    }
    target: dict[str, Any] = {
        "episode_index": episode,
        "status": "succeeded",
        "result": result,
        "result_id": f"result-{job_id}",
        "input_key": "input",
        "input": {
            "source_id": source[0],
            "source_revision": source[1],
            "snapshot_id": "snapshot",
            "instruction": "Pick",
        },
    }
    target["evidence"] = bind_job_evidence(job, target, result).to_dict()
    job["targets"] = [target]
    return job


async def _store_with(root: Path, *jobs: dict[str, Any], generation: int = 0) -> LocalJobStore:
    store = LocalJobStore(root)
    async with store.transaction() as state:
        state["generations"]["dataset"] = generation
        for job in jobs:
            state["jobs"][job["id"]] = job
    return store


def _authority(
    store: LocalJobStore, revision: tuple[str, str] = ("source-id", "rev-1")
) -> ScopedJudgeEvidenceAuthority:
    return ScopedJudgeEvidenceAuthority(store, _Datasets(revision).get_source_revision, principal_scope_id=PRINCIPAL)


@pytest.mark.asyncio
async def test_given_no_judge_state_when_evidence_requested_then_none_is_returned(tmp_path: Path) -> None:
    authority = _authority(LocalJobStore(tmp_path))

    assert await authority.evidence("dataset", 3) is None


@pytest.mark.asyncio
async def test_given_current_principal_evidence_when_requested_then_bound_job_and_target_are_returned(
    tmp_path: Path,
) -> None:
    job = _judge_job("run-1", run_order=1)
    authority = _authority(await _store_with(tmp_path, job))

    bound = await authority.evidence("dataset", 3)

    assert bound is not None
    assert bound["job"]["id"] == "run-1"
    assert bound["target"]["episode_index"] == 3


@pytest.mark.asyncio
async def test_given_only_another_principal_evidence_when_requested_then_none_is_returned(tmp_path: Path) -> None:
    foreign = _judge_job("run-foreign", run_order=1, actor="scope-b")
    authority = _authority(await _store_with(tmp_path, foreign))

    assert await authority.evidence("dataset", 3) is None


@pytest.mark.asyncio
async def test_given_superseded_generation_when_requested_then_none_is_returned(tmp_path: Path) -> None:
    stale = _judge_job("run-old", run_order=1, generation=0)
    authority = _authority(await _store_with(tmp_path, stale, generation=1))

    assert await authority.evidence("dataset", 3) is None


@pytest.mark.asyncio
async def test_given_changed_source_revision_when_requested_then_evidence_is_refused(tmp_path: Path) -> None:
    job = _judge_job("run-1", run_order=1, source=("source-id", "rev-1"))
    authority = _authority(await _store_with(tmp_path, job), revision=("source-id", "rev-2"))

    with pytest.raises(SavedInputError, match="Judge evidence source revision changed"):
        await authority.evidence("dataset", 3)


@pytest.mark.asyncio
async def test_given_invalid_newest_evidence_when_requested_then_older_valid_evidence_is_not_substituted(
    tmp_path: Path,
) -> None:
    older = _judge_job("run-older", run_order=1)
    newer = _judge_job("run-newer", run_order=2)
    newer["targets"][0]["result"] = {**newer["targets"][0]["result"], "voc": 0.5}
    authority = _authority(await _store_with(tmp_path, older, newer))

    with pytest.raises(SavedInputError, match="Judge evidence binding is invalid"):
        await authority.evidence("dataset", 3)


@pytest.mark.asyncio
async def test_given_malformed_newest_target_when_requested_then_refusal_is_a_saved_input_error(
    tmp_path: Path,
) -> None:
    malformed = _judge_job("run-1", run_order=1)
    del malformed["targets"][0]["result"]
    authority = _authority(await _store_with(tmp_path, malformed))

    with pytest.raises(SavedInputError):
        await authority.evidence("dataset", 3)


@pytest.mark.asyncio
async def test_given_bound_evidence_when_read_through_saved_input_reader_then_resource_matches_authority(
    tmp_path: Path,
) -> None:
    job = _judge_job("run-1", run_order=1)
    authority = _authority(await _store_with(tmp_path, job))
    reader = ViewerSavedInputReader(None, None, judge_evidence=authority)  # type: ignore[arg-type]

    resource = await reader.release_resource(
        "dataset", SavedSelection(kind="evidence", episode_index=3, expected_revision="rev-1")
    )

    assert resource.value is not None
    assert resource.value["job"]["id"] == "run-1"
    assert resource.value["target"]["episode_index"] == 3
    assert resource.etag is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["evidence", "labels", "ledger"])
async def test_given_unbound_release_storage_when_read_then_refused(
    kind: Literal["evidence", "labels", "ledger"],
) -> None:
    reader = ViewerSavedInputReader(None, None)  # type: ignore[arg-type]

    with pytest.raises(SavedInputError, match=r"authority is not bound|storage is unavailable"):
        await reader.release_resource("dataset", SavedSelection(kind=kind, episode_index=3, expected_revision="rev-1"))


@pytest.mark.asyncio
async def test_given_non_release_resource_when_read_then_absent() -> None:
    reader = ViewerSavedInputReader(None, None)  # type: ignore[arg-type]

    resource = await reader.release_resource(
        "dataset", SavedSelection(kind="annotation", episode_index=3, expected_revision="rev-1")
    )

    assert resource.value is None
    assert resource.etag is None


@pytest.mark.asyncio
async def test_given_foreign_label_identity_when_read_then_refused(tmp_path: Path) -> None:
    storage = LocalLabelStorage(str(tmp_path))
    await storage.save("dataset", DatasetLabelsFile(dataset_id="another-dataset"), if_none_match=True)
    reader = ViewerSavedInputReader(None, None, storage)  # type: ignore[arg-type]

    with pytest.raises(SavedInputError, match="dataset identity mismatch"):
        await reader.release_resource(
            "dataset", SavedSelection(kind="labels", episode_index=3, expected_revision="rev-1")
        )


@pytest.mark.asyncio
async def test_given_missing_blob_provider_when_judge_state_read_then_unavailable() -> None:
    with pytest.raises(SavedInputError, match="Judge evidence storage is unavailable"):
        await AzureJudgeStateReader(None).read()


@pytest.mark.asyncio
async def test_given_blob_judge_state_when_read_then_persisted_state_is_returned(tmp_path: Path) -> None:
    state = await (await _store_with(tmp_path, _judge_job("run-1", run_order=1))).read()
    provider = MagicMock(container_name="datasets")
    client = MagicMock()
    provider._get_client = AsyncMock(return_value=client)
    blob = client.get_container_client.return_value.get_blob_client.return_value
    download = MagicMock()
    download.properties.etag = "state-revision"
    download.readall = AsyncMock(return_value=json.dumps(state).encode())
    blob.download_blob = AsyncMock(return_value=download)

    assert await AzureJudgeStateReader(provider).read() == state
    client.get_container_client.assert_called_once_with("datasets")
    client.get_container_client.return_value.get_blob_client.assert_called_once_with("_curation/judge/state.json")
