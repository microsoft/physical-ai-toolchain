"""Apply verified saved evidence through curation-owned storage."""

from __future__ import annotations

from typing import Any

from physical_ai_curation.evidence import read_job_evidence
from physical_ai_curation.models import ContributionLedger, DatasetLabelsFile, MachineOrigin
from physical_ai_curation.storage import CurationStorage


async def apply_judge_result(storage: CurationStorage, job: dict[str, Any], target: dict[str, Any]) -> bool:
    """Apply typed machine proposals from a verified durable result."""
    from physical_ai_curation.models import EpisodeAnalysisRecord

    result = read_job_evidence(job, target).payload
    task_result = None
    if job.get("result_kind") == "task-findings":
        task_result = result
    else:
        if result["outcome_success"] is None:
            return False
    resource = await storage.load_versioned(job["dataset_id"])
    labels = resource.value or DatasetLabelsFile(dataset_id=job["dataset_id"])
    origin = MachineOrigin(
        run_id=job["id"],
        result_id=target["result_id"],
        run_order=job["run_order"],
        source_revision=target["input"]["source_revision"],
        input_revision=target["input"]["snapshot_id"],
        config_revision=job["config_revision"],
    )
    if task_result is not None:
        labels.apply_analysis(
            target["episode_index"],
            EpisodeAnalysisRecord(
                **task_result["findings"], instruction=task_result["instruction"], source=task_result["judge_model"]
            ),
            author_id=job["actor"],
            machine_origin=origin,
        )
        await storage.save(job["dataset_id"], labels, if_match=resource.etag, if_none_match=resource.etag is None)
        return True
    key = str(target["episode_index"])
    outcome = "SUCCESS" if result["outcome_success"] else "FAILURE"
    previous = labels.episodes.get(key, [])
    ledger = labels.provenance.setdefault(key, ContributionLedger())
    ledger.record_changes(
        {f"labels/{label}": label in previous for label in ("SUCCESS", "FAILURE", "PARTIAL")},
        {f"labels/{label}": label == outcome for label in ("SUCCESS", "FAILURE", "PARTIAL")},
        author_id=job["actor"],
        machine_origin=origin,
    )
    labels.materialize_episode(target["episode_index"], author_id=job["actor"])
    if outcome not in labels.available_labels:
        labels.available_labels.append(outcome)
    await storage.save(job["dataset_id"], labels, if_match=resource.etag, if_none_match=resource.etag is None)
    return True
