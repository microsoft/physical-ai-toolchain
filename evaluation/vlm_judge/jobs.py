"""One durable, fenced lifecycle for single and dataset judge execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from copy import deepcopy
from typing import Any, Protocol
from uuid import uuid4

from .curation import DatasetLabelsFile, validate_task_result
from .curation_storage import CurationStorage, plan_withdrawal
from .dataset import EpisodeRecord
from .job_storage import JobStore
from .judge import JudgeResult
from .saved_input import SavedInputSnapshot

_LOGGER = logging.getLogger("evaluation.vlm_judge.jobs")
_TERMINAL = {"succeeded", "partial", "failed", "cancelled"}


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class DatasetResolver(Protocol):
    async def episode_indices(self, dataset_id: str, *, principal_scope_id: str) -> list[int]: ...

    async def validation_sample(
        self,
        dataset_id: str,
        episode_index: int,
        *,
        principal_scope_id: str,
        reference: dict[str, str],
        views: tuple[str, ...] = (),
    ) -> dict[str, Any]: ...

    async def resolve(
        self, dataset_id: str, episode_index: int, **kwargs: Any
    ) -> tuple[EpisodeRecord, SavedInputSnapshot]: ...


class JudgeJobs:
    def __init__(
        self,
        store: JobStore,
        resolver: DatasetResolver,
        infer: Callable[[EpisodeRecord, SavedInputSnapshot, dict[str, Any]], Awaitable[dict[str, Any]]],
        *,
        capacity_scope: str,
        capacity: int = 1,
        lease_seconds: int = 90,
        clock: Callable[[], float] = time.time,
        apply_result: Callable[[dict[str, Any], dict[str, Any]], Awaitable[bool]] | None = None,
        result_kind: str = "judge",
        curation_storage: CurationStorage | None = None,
    ) -> None:
        if not 1 <= capacity <= 16 or not 5 <= lease_seconds <= 600:
            raise ValueError("Invalid judge capacity or lease duration")
        self.store, self.resolver, self.infer = store, resolver, infer
        self.capacity_scope, self.capacity, self.lease_seconds = capacity_scope, capacity, lease_seconds
        self.clock = clock
        self.apply_result = apply_result
        self.curation_storage = curation_storage
        if result_kind not in {"judge", "task-findings"}:
            raise ValueError("Invalid judge result kind")
        self.result_kind = result_kind
        self.worker_id = uuid4().hex

    @staticmethod
    def _owned(state: dict[str, Any], job_id: str, actor: str) -> dict[str, Any]:
        job = state["jobs"].get(job_id)
        if job is None:
            raise KeyError("Judge job not found")
        if job["actor"] != actor:
            raise PermissionError("Judge job belongs to another principal")
        return job

    @staticmethod
    def _reset_scope(state: dict[str, Any], dataset_id: str) -> tuple[list[str], str]:
        runs = sorted(
            (job["id"], job["generation"]) for job in state["jobs"].values() if job["dataset_id"] == dataset_id
        )
        return [run[0] for run in runs], fingerprint(runs)

    async def preview_reset(
        self, dataset_id: str, actor: str, *, include_unlisted_runs: bool = False
    ) -> dict[str, Any]:
        if self.curation_storage is None:
            raise ValueError("Contribution storage is unavailable")
        async with self.store.transaction() as state:
            if state["resets"].get(dataset_id, {}).get("status") == "running":
                raise ValueError("Dataset withdrawal is running")
            resource = await self.curation_storage.load_versioned(dataset_id)
            labels = resource.value or DatasetLabelsFile(dataset_id=dataset_id)
            run_ids, scope = self._reset_scope(state, dataset_id)
            if include_unlisted_runs:
                # Machine output from runs missing in this job history, e.g. after a job directory change.
                run_ids = sorted(set(run_ids) | set(plan_withdrawal(labels, set(run_ids))[1]["unlisted_run_ids"]))
            projected, summary = plan_withdrawal(labels, set(run_ids))
            preview = {
                "id": uuid4().hex,
                "dataset_id": dataset_id,
                "actor": actor,
                "created_at": self.clock(),
                "run_ids": run_ids,
                "scope": scope,
                "generation": state["generations"].get(dataset_id, 0),
                "etag": resource.etag,
                "projected_hash": fingerprint(projected.model_dump(mode="json")),
                "summary": summary,
            }
            previews = state.setdefault("reset_previews", {})
            previews[preview["id"]] = preview
            while len(previews) > 128:
                previews.pop(next(iter(previews)))
        _LOGGER.info(
            "Withdrawal preview runs=%d removable=%d conflicts=%d",
            len(run_ids),
            summary["removable_fields"],
            summary["conflicts"],
        )
        return deepcopy(preview)

    async def confirm_reset(self, dataset_id: str, actor: str, preview_id: str) -> dict[str, Any]:
        if self.curation_storage is None:
            raise ValueError("Contribution storage is unavailable")
        async with self.store.transaction() as state:
            preview = state.get("reset_previews", {}).get(preview_id)
            if not preview or preview["dataset_id"] != dataset_id:
                raise KeyError("Withdrawal preview not found")
            if preview["actor"] != actor:
                raise PermissionError("Withdrawal preview belongs to another principal")
            existing = state["resets"].get(dataset_id)
            if existing and existing["preview_id"] == preview_id:
                return deepcopy(existing)
            if existing and existing["status"] == "running":
                raise ValueError("Dataset withdrawal is running")
            resource = await self.curation_storage.load_versioned(dataset_id)
            _, scope = self._reset_scope(state, dataset_id)
            if (
                scope != preview["scope"]
                or resource.etag != preview["etag"]
                or preview["generation"] != state["generations"].get(dataset_id, 0)
            ):
                raise ValueError("Withdrawal scope changed; create a new preview")
            state["generations"][dataset_id] = preview["generation"] + 1
            for job in state["jobs"].values():
                if job["dataset_id"] != dataset_id:
                    continue
                if job["status"] not in _TERMINAL:
                    job.update(status="cancelled", updated_at=self.clock())
                for target in job["targets"]:
                    if target["status"] in {"queued", "running"}:
                        target["status"] = "cancelled"
                    if target.get("application_status") == "queued":
                        target["application_status"] = "withdrawn"
            reset = {
                "id": uuid4().hex,
                "preview_id": preview_id,
                "dataset_id": dataset_id,
                "actor": actor,
                "status": "running",
                "generation": state["generations"][dataset_id],
                "capacity_scope": self.capacity_scope,
                "created_at": self.clock(),
                "updated_at": self.clock(),
                "run_ids": preview["run_ids"],
                "summary": preview["summary"],
                "error": None,
                "resources": [
                    {
                        "key": "labels",
                        "status": "pending",
                        "etag": resource.etag,
                        "projected_hash": preview["projected_hash"],
                    }
                ],
            }
            state["resets"][dataset_id] = reset
        _LOGGER.info("Withdrawal accepted runs=%d", len(reset["run_ids"]))
        return deepcopy(reset)

    async def reset_status(self, dataset_id: str, actor: str) -> dict[str, Any]:
        reset = (await self.store.read())["resets"].get(dataset_id)
        if reset is None:
            raise KeyError("Dataset withdrawal not found")
        if reset["actor"] != actor:
            raise PermissionError("Dataset withdrawal belongs to another principal")
        return deepcopy(reset)

    async def retry_reset(self, dataset_id: str, actor: str) -> dict[str, Any]:
        await self.reset_status(dataset_id, actor)
        retried = False
        async with self.store.transaction() as state:
            reset = state["resets"][dataset_id]
            if reset["actor"] != actor:
                raise PermissionError("Dataset withdrawal belongs to another principal")
            if reset["status"] == "conflicted":
                raise ValueError("Withdrawal conflicts require a new preview")
            if reset["status"] == "partial":
                reset.update(status="running", error=None, updated_at=self.clock())
                retried = True
            result = deepcopy(reset)
        if retried:
            _LOGGER.info("Withdrawal retry accepted")
        return result

    async def reset_once(self) -> bool:
        if self.curation_storage is None:
            return False
        pending = [
            reset
            for reset in (await self.store.read())["resets"].values()
            if reset["status"] == "running" and reset["capacity_scope"] == self.capacity_scope
        ]
        if not pending:
            return False
        dataset_id = pending[0]["dataset_id"]
        async with self.store.transaction() as state:
            reset = state["resets"][dataset_id]
            if reset["status"] != "running":
                return False
            checkpoint = reset["resources"][0]
            try:
                resource = await self.curation_storage.load_versioned(dataset_id)
                labels = resource.value or DatasetLabelsFile(dataset_id=dataset_id)
                current_hash = fingerprint(labels.model_dump(mode="json"))
                if current_hash == checkpoint["projected_hash"]:
                    checkpoint.update(status="succeeded", published_etag=resource.etag)
                elif resource.etag != checkpoint["etag"]:
                    reset.update(status="conflicted", error="RevisionConflictError")
                else:
                    projected, summary = plan_withdrawal(labels, set(reset["run_ids"]))
                    if fingerprint(projected.model_dump(mode="json")) != checkpoint["projected_hash"]:
                        raise ValueError("Withdrawal projection changed")
                    etag = await self.curation_storage.save(
                        dataset_id, projected, if_match=resource.etag, if_none_match=resource.etag is None
                    )
                    checkpoint.update(status="succeeded", published_etag=etag)
                    reset["summary"] = summary
                if checkpoint["status"] == "succeeded":
                    reset["status"] = "conflicted" if reset["summary"]["conflicts"] else "succeeded"
            except Exception as error:
                reset.update(status="partial", error=type(error).__name__)
                _LOGGER.warning("Withdrawal failed category=%s", type(error).__name__)
            reset["updated_at"] = self.clock()
        if reset["status"] == "conflicted":
            _LOGGER.warning("Withdrawal conflicted category=%s", reset.get("error") or "ContributionConflict")
        _LOGGER.info("Withdrawal checkpoint status=%s", reset["status"])
        return True

    async def submit(
        self,
        dataset_id: str,
        actor: str,
        episode_indices: list[int],
        config: dict[str, Any],
        *,
        idempotency_key: str,
        mode: str = "judge",
        approval_id: str | None = None,
        expected_snapshots: dict[int, str] | None = None,
        samples: dict[int, dict[str, str]] | None = None,
        require_approval: bool = False,
    ) -> dict[str, Any]:
        indices = sorted(set(episode_indices))
        if not actor.strip() or not dataset_id.strip() or not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("Job submission requires an actor, dataset and bounded idempotency key")
        if not indices or len(indices) > 10000 or any(type(index) is not int or index < 0 for index in indices):
            raise ValueError("Select between one and 10000 real episode IDs")
        if mode not in {"judge", "sample", "judge-and-label"}:
            raise ValueError("Invalid judge job mode")
        samples = samples or {}
        if mode == "sample" and set(samples) != set(indices):
            raise ValueError("Every sample requires explicit saved human references")
        if mode != "sample" and samples:
            raise ValueError("Validation samples must be submitted separately from targets")
        if mode == "judge-and-label" and self.apply_result is None:
            raise ValueError("Verified contribution storage is not configured")
        if mode != "sample" and (require_approval or mode == "judge-and-label") and not approval_id:
            raise ValueError("Dataset execution requires an explicit configuration approval")
        request_key = fingerprint([actor, dataset_id, idempotency_key])
        expected_snapshots = expected_snapshots or {}
        if not set(expected_snapshots).issubset(indices):
            raise ValueError("Snapshot references must belong to selected targets")
        payload_key = fingerprint([dataset_id, actor, indices, config, mode, approval_id, expected_snapshots, samples])
        execution_config = {
            **{key: value for key, value in config.items() if key != "force"},
            "result_kind": self.result_kind,
        }
        payload_key = fingerprint([payload_key, self.result_kind])

        def replay(state: dict[str, Any]) -> dict[str, Any] | None:
            for job in state["jobs"].values():
                if job["request_key"] == request_key:
                    if job["payload_key"] != payload_key:
                        raise ValueError("Idempotency key was used with another payload")
                    return deepcopy(job)
            reset = state["resets"].get(dataset_id, {})
            published = reset.get("resources", [{}])[0].get("status") == "succeeded"
            if reset.get("status") in {"running", "partial"} or (reset.get("status") == "conflicted" and not published):
                raise ValueError("Dataset withdrawal is running")
            return None

        existing = replay(await self.store.read())
        if existing is not None:
            return existing
        approval = None
        if approval_id:
            approval = await self._validate_approval(approval_id, dataset_id, actor, fingerprint(execution_config))
        targets = []
        for index in indices:
            sample = None
            if mode == "sample":
                sample = await self.resolver.validation_sample(
                    dataset_id,
                    index,
                    principal_scope_id=actor,
                    reference=samples[index],
                    views=tuple(config.get("views") or ()),
                )
            _, snapshot = await self.resolver.resolve(
                dataset_id,
                index,
                principal_scope_id=actor,
                views=tuple(config.get("views") or ()),
                annotation_author_id=samples[index]["annotation_author_id"]
                if sample
                else config.get("annotation_author_id"),
                expected_snapshot_id=samples[index]["snapshot_id"] if sample else expected_snapshots.get(index),
            )
            if approval is not None and [snapshot.source_id, snapshot.source_revision] != approval["source"]:
                raise ValueError("Target source differs from the approved sample source")
            targets.append(
                {
                    "episode_index": index,
                    "status": "queued",
                    "fence": 0,
                    "input": snapshot.model_dump(mode="json"),
                    "input_key": fingerprint([snapshot.model_dump(mode="json"), execution_config]),
                    "result": None,
                    "result_id": None,
                    "error": None,
                    "applied": False,
                    "sample": sample,
                }
            )
        async with self.store.transaction() as state:
            existing = replay(state)
            if existing is not None:
                return existing
            if approval is not None:
                self._approval_state(state, approval_id, dataset_id, actor, fingerprint(execution_config))
            configured_capacity = state["capacities"].setdefault(self.capacity_scope, self.capacity)
            if configured_capacity != self.capacity:
                raise ValueError("Replicas must agree on shared inference capacity")
            state["sequence"] += 1
            job = {
                "id": uuid4().hex,
                "dataset_id": dataset_id,
                "actor": actor,
                "request_key": request_key,
                "payload_key": payload_key,
                "run_order": state["sequence"],
                "created_at": self.clock(),
                "updated_at": self.clock(),
                "status": "queued",
                "config": deepcopy(config),
                "config_revision": fingerprint(execution_config),
                "mode": mode,
                "result_kind": self.result_kind,
                "approval_id": approval_id,
                "generation": state["generations"].get(dataset_id, 0),
                "capacity_scope": self.capacity_scope,
                "targets": targets,
                "total": len(targets),
                "judged": 0,
                "applied": 0,
                "errors": 0,
                "retry_count": 0,
            }
            state["jobs"][job["id"]] = job
            accepted = deepcopy(job)
        _LOGGER.info("Judge job accepted job=%s targets=%d", accepted["id"], len(indices))
        return accepted

    @staticmethod
    def _approval_state(
        state: dict[str, Any],
        approval_id: str,
        dataset_id: str,
        actor: str,
        config_revision: str,
    ) -> dict[str, Any]:
        approval = state["approvals"].get(approval_id)
        if (
            approval is None
            or approval["actor"] != actor
            or approval["dataset_id"] != dataset_id
            or approval["config_revision"] != config_revision
            or approval.get("withdrawn", False)
        ):
            raise ValueError("Configuration approval is unavailable or stale")
        return approval

    async def _validate_samples(self, job: dict[str, Any]) -> None:
        for target in job["targets"]:
            sample = target.get("sample")
            if sample is None:
                raise ValueError("Validation sample evidence is missing")
            current = await self.resolver.validation_sample(
                job["dataset_id"],
                target["episode_index"],
                principal_scope_id=job["actor"],
                reference=sample["reference"],
                views=tuple(job["config"].get("views") or ()),
            )
            if current != sample:
                raise ValueError("Validation sample evidence is stale")

    async def _validate_approval(
        self,
        approval_id: str,
        dataset_id: str,
        actor: str,
        config_revision: str,
    ) -> dict[str, Any]:
        state = await self.store.read()
        approval = self._approval_state(state, approval_id, dataset_id, actor, config_revision)
        sample_job = self._owned(state, approval["sample_job_id"], actor)
        await self._validate_samples(sample_job)
        return deepcopy(approval)

    async def approve(
        self,
        job_id: str,
        actor: str,
        *,
        acknowledge_exceptions: bool = False,
    ) -> dict[str, Any]:
        job = await self.get(job_id, actor)
        if job["mode"] != "sample" or job["status"] != "succeeded" or not job["targets"]:
            raise ValueError("Approval requires complete successful sample evaluation")
        if any(
            target.get("comparison") not in {"agreement", "disagreement", "inconclusive"} for target in job["targets"]
        ):
            raise ValueError("Approval requires complete sample comparisons")
        exceptions = any(target["comparison"] != "agreement" for target in job["targets"])
        if exceptions and not acknowledge_exceptions:
            raise ValueError("Disagreement or inconclusive evidence requires explicit acknowledgment")
        await self._validate_samples(job)
        sources = {(target["input"]["source_id"], target["input"]["source_revision"]) for target in job["targets"]}
        if len(sources) != 1:
            raise ValueError("Samples must share one current dataset source")
        async with self.store.transaction() as state:
            current = self._owned(state, job_id, actor)
            if current != job or job["generation"] != state["generations"].get(job["dataset_id"], 0):
                raise ValueError("Sample evaluation changed before approval")
            approval = {
                "id": uuid4().hex,
                "actor": actor,
                "dataset_id": job["dataset_id"],
                "sample_job_id": job_id,
                "config_revision": job["config_revision"],
                "generation": job["generation"],
                "source": list(next(iter(sources))),
                "acknowledge_exceptions": acknowledge_exceptions,
                "created_at": self.clock(),
            }
            state["approvals"][approval["id"]] = approval
        _LOGGER.info("Judge configuration approved sample_job=%s approval=%s", job_id, approval["id"])
        return deepcopy(approval)

    async def get(self, job_id: str, actor: str) -> dict[str, Any]:
        return deepcopy(self._owned(await self.store.read(), job_id, actor))

    async def approvals(
        self,
        dataset_id: str,
        actor: str,
        *,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        from .saved_input import SavedInputError

        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid approval page")
        state = await self.store.read()
        approvals = sorted(
            (
                item
                for item in state["approvals"].values()
                if item["actor"] == actor and item["dataset_id"] == dataset_id
            ),
            key=lambda item: (item["created_at"], item["id"]),
            reverse=True,
        )
        items = []
        for approval in approvals[offset : offset + limit]:
            current = True
            try:
                await self._validate_approval(approval["id"], dataset_id, actor, approval["config_revision"])
            except SavedInputError as error:
                if error.status_code in {401, 403}:
                    raise
                current = False
            except ValueError:
                current = False
            items.append(
                {**{key: deepcopy(value) for key, value in approval.items() if key != "actor"}, "current": current}
            )
        return {"items": items, "total": len(approvals)}

    async def list(self, dataset_id: str, actor: str, *, offset: int = 0, limit: int = 25) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid job page")
        state = await self.store.read()
        jobs = sorted(
            (job for job in state["jobs"].values() if job["dataset_id"] == dataset_id and job["actor"] == actor),
            key=lambda job: job["run_order"],
            reverse=True,
        )
        return {
            "items": [
                {key: deepcopy(value) for key, value in job.items() if key != "targets"}
                for job in jobs[offset : offset + limit]
            ],
            "total": len(jobs),
        }

    def _valid_claim(
        self, state: dict[str, Any], claim: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        job = state["jobs"].get(claim["job_id"])
        if not job or job["status"] in _TERMINAL or job["generation"] != state["generations"].get(job["dataset_id"], 0):
            return None
        target = next((item for item in job["targets"] if item["episode_index"] == claim["episode_index"]), None)
        if (
            not target
            or target["status"] != "running"
            or target["fence"] != claim["fence"]
            or target.get("owner") != claim["owner"]
            or target.get("expires_at", 0) <= self.clock()
        ):
            return None
        return job, target

    async def claim(self) -> dict[str, Any] | None:
        observed = await self.store.read()
        if not any(
            job["capacity_scope"] == self.capacity_scope
            and job.get("result_kind", "judge") == self.result_kind
            and job["status"] not in _TERMINAL
            and job["generation"] == observed["generations"].get(job["dataset_id"], 0)
            and any(
                target["status"] == "queued" or (target["status"] == "running" and target["expires_at"] <= self.clock())
                for target in job["targets"]
            )
            for job in observed["jobs"].values()
        ):
            return None
        async with self.store.transaction() as state:
            now = self.clock()
            executions = state.setdefault("executions", {})
            for key in list(executions):
                if executions[key]["expires_at"] <= now:
                    del executions[key]
            running = [
                execution for execution in executions.values() if execution["capacity_scope"] == self.capacity_scope
            ]
            if len(running) >= state["capacities"].get(self.capacity_scope, self.capacity):
                return None
            busy_inputs = {target["input_key"] for target in running}
            for job in sorted(state["jobs"].values(), key=lambda item: item["run_order"]):
                if job["status"] in _TERMINAL or job["capacity_scope"] != self.capacity_scope:
                    continue
                if job.get("result_kind", "judge") != self.result_kind:
                    continue
                if job["generation"] != state["generations"].get(job["dataset_id"], 0):
                    continue
                for target in job["targets"]:
                    expired = target["status"] == "running" and target["expires_at"] <= now
                    if (target["status"] != "queued" and not expired) or target["input_key"] in busy_inputs:
                        continue
                    target.update(
                        status="running",
                        fence=target["fence"] + 1,
                        owner=self.worker_id,
                        expires_at=now + self.lease_seconds,
                    )
                    job.update(status="running", updated_at=now)
                    claim = {
                        "job_id": job["id"],
                        "episode_index": target["episode_index"],
                        "fence": target["fence"],
                        "owner": self.worker_id,
                        "generation": job["generation"],
                    }
                    executions[fingerprint(claim)] = {
                        "capacity_scope": self.capacity_scope,
                        "input_key": target["input_key"],
                        "expires_at": now + self.lease_seconds,
                    }
                    return claim
        return None

    async def heartbeat(self, claim: dict[str, Any]) -> bool:
        async with self.store.transaction() as state:
            execution = state.setdefault("executions", {}).get(fingerprint(claim))
            if execution is None or execution["expires_at"] <= self.clock():
                return False
            execution["expires_at"] = self.clock() + self.lease_seconds
            current = self._valid_claim(state, claim)
            if current is not None:
                current[1]["expires_at"] = execution["expires_at"]
            return True

    async def release(self, claim: dict[str, Any]) -> None:
        async with self.store.transaction() as state:
            state.setdefault("executions", {}).pop(fingerprint(claim), None)

    def _summarize(self, job: dict[str, Any]) -> None:
        job["judged"] = sum(target["status"] == "succeeded" for target in job["targets"])
        job["applied"] = sum(target["applied"] for target in job["targets"])
        job["errors"] = sum(target["status"] == "failed" for target in job["targets"])
        job["application_errors"] = sum(target.get("application_status") == "failed" for target in job["targets"])
        if job["mode"] == "sample":
            job["calibration"] = {
                comparison: sum(target.get("comparison") == comparison for target in job["targets"])
                for comparison in ("agreement", "disagreement", "inconclusive", "error")
            }
        if all(target["status"] in {"succeeded", "failed", "cancelled"} for target in job["targets"]):
            if any(
                target["status"] == "cancelled" or target.get("application_status") == "cancelled"
                for target in job["targets"]
            ):
                job["status"] = "cancelled"
            elif any(target.get("application_status") == "queued" for target in job["targets"]):
                job["status"] = "queued"
            elif job["application_errors"]:
                job["status"] = "partial" if job["judged"] else "failed"
            elif job["judged"] == job["total"]:
                job["status"] = "succeeded"
            else:
                job["status"] = "partial" if job["judged"] else "failed"
        job["updated_at"] = self.clock()

    async def publish(
        self,
        claim: dict[str, Any],
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        cached: bool = False,
    ) -> bool:
        async with self.store.transaction() as state:
            state.setdefault("executions", {}).pop(fingerprint(claim), None)
            current = self._valid_claim(state, claim)
            if current is None:
                _LOGGER.warning(
                    "Judge publication fenced job=%s", str(claim["job_id"]).replace("\r", "").replace("\n", "")
                )
                return False
            job, target = current
            if error is None:
                try:
                    validated = (
                        validate_task_result(result or {})
                        if job.get("result_kind") == "task-findings"
                        else JudgeResult.from_dict(result or {}).to_dict()
                    )
                    if validated["instruction"] != target["input"]["instruction"]:
                        raise ValueError("Judge instruction changed")
                    if validated["episode_id"] != f"{job['dataset_id']}/episode_{target['episode_index']:06d}":
                        raise ValueError("Judge episode changed")
                except ValueError:
                    error = "InvalidResult"
                else:
                    target.update(
                        result=validated,
                        result_id=fingerprint([job["id"], target["input_key"]]),
                        status="succeeded",
                        cached=cached,
                    )
                    if job["mode"] == "sample":
                        outcome = validated.get("outcome_success")
                        predicted = "success" if outcome is True else "failure" if outcome is False else None
                        target["comparison"] = (
                            "inconclusive"
                            if predicted is None
                            else "agreement"
                            if predicted == target["sample"]["human_outcome"]
                            else "disagreement"
                        )
                    elif job["mode"] == "judge-and-label":
                        target.update(application_status="queued", application_error=None)
            if error is not None:
                target.update(
                    status="failed", error=error if re.fullmatch(r"[A-Za-z0-9_.]{1,80}", error) else "ExecutionError"
                )
                if job["mode"] == "sample":
                    target["comparison"] = "error"
            target.pop("owner", None)
            target.pop("expires_at", None)
            self._summarize(job)
        _LOGGER.info(
            "Judge target finished job=%s episode=%d status=%s",
            job["id"],
            int(target["episode_index"]),
            target["status"],
        )
        return True

    async def cancel(self, job_id: str, actor: str) -> dict[str, Any]:
        async with self.store.transaction() as state:
            job = self._owned(state, job_id, actor)
            if job["status"] not in _TERMINAL:
                for target in job["targets"]:
                    if target["status"] in {"queued", "running"}:
                        target.update(status="cancelled", fence=target["fence"] + 1)
                    if target.get("application_status") == "queued":
                        target["application_status"] = "cancelled"
                self._summarize(job)
            result = deepcopy(job)
        _LOGGER.info("Judge job cancelled job=%s", result["id"])
        return result

    async def retry(self, job_id: str, actor: str) -> dict[str, Any]:
        async with self.store.transaction() as state:
            job = self._owned(state, job_id, actor)
            if job["generation"] != state["generations"].get(job["dataset_id"], 0):
                raise ValueError("Withdrawn jobs cannot be retried")
            if job["status"] not in {"partial", "failed", "cancelled"}:
                raise ValueError("Only failed or cancelled targets can be retried")
            for target in job["targets"]:
                if target["status"] in {"failed", "cancelled"}:
                    target.update(status="queued", error=None, fence=target["fence"] + 1)
                if target.get("application_status") in {"failed", "cancelled"}:
                    target.update(application_status="queued", application_error=None)
            job.update(status="queued", retry_count=job["retry_count"] + 1)
            self._summarize(job)
            return deepcopy(job)

    async def request_application(
        self,
        job_id: str,
        actor: str,
        episode_indices: list[int] | None = None,
    ) -> dict[str, Any]:
        if self.apply_result is None:
            raise ValueError("Verified contribution storage is not configured")
        async with self.store.transaction() as state:
            job = self._owned(state, job_id, actor)
            if job["mode"] == "sample":
                raise ValueError("Validation samples cannot apply predicted labels")
            if job["status"] == "cancelled" or job["generation"] != state["generations"].get(job["dataset_id"], 0):
                raise ValueError("Cancelled or withdrawn results cannot be applied")
            indices = set(
                episode_indices
                if episode_indices is not None
                else [target["episode_index"] for target in job["targets"]]
            )
            targets = [target for target in job["targets"] if target["episode_index"] in indices]
            if (
                not indices
                or len(targets) != len(indices)
                or any(target["status"] != "succeeded" for target in targets)
            ):
                raise ValueError("Application requires successful saved results for every selected target")
            for target in targets:
                if not target["applied"] and target.get("application_status") != "skipped":
                    target.update(application_status="queued", application_error=None)
            self._summarize(job)
            return deepcopy(job)

    async def apply_once(self) -> bool:
        if self.apply_result is None:
            return False

        def candidates(state: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
            return [
                (job, target)
                for job in sorted(state["jobs"].values(), key=lambda item: item["run_order"])
                if job["status"] != "cancelled"
                and job["capacity_scope"] == self.capacity_scope
                and job.get("result_kind", "judge") == self.result_kind
                and job["generation"] == state["generations"].get(job["dataset_id"], 0)
                for target in job["targets"]
                if target.get("application_status") == "queued" and target["status"] == "succeeded"
            ]

        if not candidates(await self.store.read()):
            return False
        async with self.store.transaction() as state:
            pending = candidates(state)
            if not pending:
                return False
            job, target = pending[0]
            try:
                if job.get("approval_id"):
                    approval = self._approval_state(
                        state, job["approval_id"], job["dataset_id"], job["actor"], job["config_revision"]
                    )
                    await self._validate_samples(self._owned(state, approval["sample_job_id"], job["actor"]))
                await self.resolver.resolve(
                    job["dataset_id"],
                    target["episode_index"],
                    principal_scope_id=job["actor"],
                    views=tuple(job["config"].get("views") or ()),
                    annotation_author_id=target["input"]["annotation_author_id"],
                    expected_snapshot_id=target["input"]["snapshot_id"],
                )
                applied = await self.apply_result(deepcopy(job), deepcopy(target))
                target.update(
                    applied=applied, application_status="succeeded" if applied else "skipped", application_error=None
                )
            except Exception as error:
                target.update(application_status="failed", application_error=type(error).__name__)
                _LOGGER.warning("Judge application failed job=%s category=%s", job["id"], type(error).__name__)
            self._summarize(job)
        _LOGGER.info(
            "Judge application finished job=%s episode=%d status=%s",
            job["id"],
            int(target["episode_index"]),
            target["application_status"],
        )
        return True

    async def run_once(self) -> bool:
        if await self.reset_once():
            return True
        if await self.apply_once():
            return True
        claim = await self.claim()
        if claim is None:
            return False

        async def renew() -> None:
            while True:
                await asyncio.sleep(self.lease_seconds / 3)
                if not await self.heartbeat(claim):
                    return

        renewal = asyncio.create_task(renew())
        try:
            state = await self.store.read()
            current = self._valid_claim(state, claim)
            if current is None:
                return True
            job, target = current
            if job.get("approval_id"):
                await self._validate_approval(
                    job["approval_id"], job["dataset_id"], job["actor"], job["config_revision"]
                )
            record, snapshot = await self.resolver.resolve(
                job["dataset_id"],
                target["episode_index"],
                principal_scope_id=job["actor"],
                views=tuple(job["config"].get("views") or ()),
                annotation_author_id=target["input"]["annotation_author_id"],
                expected_snapshot_id=target["input"]["snapshot_id"],
            )
            prior = next(
                (
                    item["result"]
                    for previous in sorted(state["jobs"].values(), key=lambda entry: entry["run_order"], reverse=True)
                    for item in previous["targets"]
                    if item["input_key"] == target["input_key"]
                    and item["status"] == "succeeded"
                    and item["result"] is not None
                ),
                None,
            )
            cached = prior is not None and not job["config"].get("force", False)
            result = deepcopy(prior) if cached else await self.infer(record, snapshot, job["config"])
            cached = cached or result.pop("_cache_hit", False) is True
            await self.resolver.resolve(
                job["dataset_id"],
                target["episode_index"],
                principal_scope_id=job["actor"],
                views=tuple(job["config"].get("views") or ()),
                annotation_author_id=target["input"]["annotation_author_id"],
                expected_snapshot_id=target["input"]["snapshot_id"],
            )
            await self.publish(claim, result=result, cached=cached)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _LOGGER.warning("Judge target execution failed job=%s category=%s", claim["job_id"], type(error).__name__)
            await self.publish(claim, error=type(error).__name__)
        finally:
            renewal.cancel()
            try:
                with suppress(asyncio.CancelledError):
                    await renewal
            finally:
                await self.release(claim)
        return True

    async def run_forever(self) -> None:
        while True:
            try:
                if not await self.run_once():
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                _LOGGER.error("Judge worker unavailable category=%s", type(error).__name__)
                await asyncio.sleep(2)

    async def results(
        self,
        dataset_id: str,
        actor: str,
        episode_index: int,
        *,
        snapshot_id: str | None = None,
    ) -> list[dict[str, Any]]:
        state = await self.store.read()
        evidence = []
        for job in sorted(state["jobs"].values(), key=lambda item: item["run_order"], reverse=True):
            if job["dataset_id"] != dataset_id or job["actor"] != actor:
                continue
            for target in job["targets"]:
                if target["episode_index"] != episode_index or target["result"] is None:
                    continue
                withdrawn = job["generation"] != state["generations"].get(dataset_id, 0)
                applicability = (
                    "withdrawn"
                    if withdrawn
                    else "current"
                    if target["input"]["snapshot_id"] == snapshot_id
                    else "stale"
                )
                evidence.append(
                    {
                        "result_id": target["result_id"],
                        "result_kind": job.get("result_kind", "judge"),
                        "run_id": job["id"],
                        "run_order": job["run_order"],
                        "config_revision": job["config_revision"],
                        "config": deepcopy(job["config"]),
                        "input": deepcopy(target["input"]),
                        "result": deepcopy(target["result"]),
                        "applicability": applicability,
                        "applied": target["applied"],
                        "created_at": job["created_at"],
                    }
                )
        return evidence
