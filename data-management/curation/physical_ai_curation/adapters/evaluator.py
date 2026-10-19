"""Translate saved curation inputs into optional evaluator execution."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from physical_ai_dataset_io.lerobot import EpisodeRecord

from evaluation.vlm_judge.judge import JudgeResult
from evaluation.vlm_judge.prompts import PROMPT_VERSION
from evaluation.vlm_judge.service import JudgeService
from physical_ai_curation.evidence import EvidenceRecord, bind_evidence
from physical_ai_curation.judge_port import ExecutionOptions, JudgeInvocation, ResolvedView
from physical_ai_curation.saved_input import SavedInputSnapshot


class EvaluatorJudge:
    """Implement the curation port using only the evaluator's inference API."""

    def __init__(self, service: JudgeService) -> None:
        self.service = service

    def identity(self, process_method: str) -> dict[str, Any]:
        return json.loads(
            json.dumps(
                {
                    "model_id": "echo" if self.service.config.backend.kind == "echo" else self.service.model_id,
                    "prompt_version": PROMPT_VERSION,
                    "execution": self.service.execution_config,
                    "agent": asdict(replace(self.service.config.agent, process_method=process_method)),
                }
            )
        )

    async def evaluate(self, invocation: JudgeInvocation) -> EvidenceRecord:
        if invocation.declared_identity != self.identity(invocation.options.process_method):
            raise ValueError("Saved judge runtime configuration is unavailable")
        if len({view.name for view in invocation.views}) != len(invocation.views):
            raise ValueError("Duplicate selected view")
        execution = asyncio.create_task(
            asyncio.to_thread(
                self.service.judge_episode,
                episode_id=invocation.episode_id,
                instruction=invocation.instruction,
                video_paths={view.name: view.path for view in invocation.views},
                from_s=invocation.options.from_s,
                to_s=invocation.options.to_s,
                video_windows={view.name: view.window for view in invocation.views if view.window is not None},
                media_identity={
                    view.name: view.media_identity for view in invocation.views if view.media_identity is not None
                },
                snapshot_id=invocation.input_id,
                process_method=invocation.options.process_method,
                force=invocation.options.force,
                cache_dir=invocation.options.cache_dir,
            )
        )
        try:
            result = await asyncio.shield(execution)
        except asyncio.CancelledError:
            _ = await execution
            raise
        return bind_evidence(invocation, result.to_dict())


class TaskExecutor:
    """Bind task-findings inference to caller-owned saved input and runtime identity."""

    def __init__(self, config: dict[str, Any]) -> None:
        from evaluation.vlm_judge.task_findings import TaskFindingsService

        self.config = deepcopy(config)
        self.service = TaskFindingsService(deepcopy(config))

    async def __call__(
        self, record: EpisodeRecord, snapshot: SavedInputSnapshot, config: dict[str, Any]
    ) -> dict[str, Any]:
        declared = {key: value for key, value in config.items() if key != "force"}
        if any(
            declared != {key: value for key, value in runtime.items() if key != "force"}
            for runtime in (self.config, self.service.config)
        ):
            raise ValueError("Saved task-finding runtime configuration is unavailable")
        invocation = JudgeInvocation(
            invocation_id=snapshot.snapshot_id,
            input_id=snapshot.snapshot_id,
            episode_id=record.episode_id,
            instruction=snapshot.instruction,
            views=tuple(
                ResolvedView(
                    name=name,
                    path=record.video_paths[name],
                    window=record.video_windows.get(name),
                    media_identity=(record.media_identity or {}).get(name),
                )
                for name in declared["views"]
            ),
            options=ExecutionOptions(
                from_s=record.from_timestamp, to_s=record.to_timestamp, force=bool(config.get("force", False))
            ),
            declared_identity={
                "model_id": declared["model_id"],
                "prompt_version": declared["prompt_version"],
                "execution": declared,
                "agent": {},
            },
        )
        execution = asyncio.create_task(
            asyncio.to_thread(self.service.evaluate, record, instruction=invocation.instruction)
        )
        try:
            result = await asyncio.shield(execution)
        except asyncio.CancelledError:
            _ = await execution
            raise
        return bind_evidence(invocation, result, kind="task-findings").payload


class ServiceExecutor:
    """Bind persisted runtime identity and keep blocking inference off the event loop."""

    def __init__(
        self,
        service: JudgeService,
        *,
        prepare_media: Callable[[EpisodeRecord, SavedInputSnapshot], Awaitable[EpisodeRecord]] | None = None,
        cache_directory: Callable[[SavedInputSnapshot], Path] | None = None,
    ) -> None:
        self.service = service
        self.prepare_media = prepare_media
        self.cache_directory = cache_directory

    def configuration(self, options: dict[str, Any]) -> dict[str, Any]:
        method = options.get("process_method") or self.service.config.agent.process_method
        if method not in {"gvl", "chronological"}:
            raise ValueError("Invalid process method")
        return json.loads(
            json.dumps(
                {
                    "execution": self.service.execution_config,
                    "agent": asdict(replace(self.service.config.agent, process_method=method)),
                    "prompt_version": PROMPT_VERSION,
                    "process_method": method,
                    "views": sorted(set(options.get("views") or [])),
                    "annotation_author_id": options.get("annotation_author_id"),
                    "force": bool(options.get("force", False)),
                    **(
                        {"instruction_override": options["instruction_override"]}
                        if options.get("instruction_override")
                        else {}
                    ),
                }
            )
        )

    async def __call__(
        self,
        record: EpisodeRecord,
        snapshot: SavedInputSnapshot,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        if config != self.configuration(config):
            raise ValueError("Saved judge runtime configuration is unavailable")
        cache_dir = self.cache_directory(snapshot) if self.cache_directory else None
        cache = self.service.cache_for(cache_dir)
        cache_key = cache.key(
            episode_id=record.episode_id,
            snapshot_id=snapshot.snapshot_id,
            video_paths=record.video_paths,
            instruction=snapshot.instruction,
            judge_model=self.service.model_id,
            prompt_version=PROMPT_VERSION,
            from_s=record.from_timestamp,
            to_s=record.to_timestamp,
            video_windows=record.video_windows,
            media_identity=record.media_identity,
            agent_config=replace(self.service.config.agent, process_method=config["process_method"]),
        )
        cached = await asyncio.to_thread(cache.get, cache_key) if not config["force"] else None
        if cached is not None:
            result = JudgeResult.from_dict(cached)
            evidence = bind_evidence(self._invocation(record, snapshot, config, cache_dir), result.to_dict())
            return {**evidence.payload, "_cache_hit": True}
        if self.prepare_media is not None:
            record = await self.prepare_media(record, snapshot)
        adapter = EvaluatorJudge(self.service)
        return (await adapter.evaluate(self._invocation(record, snapshot, config, cache_dir))).payload

    def _invocation(
        self,
        record: EpisodeRecord,
        snapshot: SavedInputSnapshot,
        config: dict[str, Any],
        cache_dir: Path | None,
    ) -> JudgeInvocation:
        return JudgeInvocation(
            invocation_id=snapshot.snapshot_id,
            input_id=snapshot.snapshot_id,
            episode_id=record.episode_id,
            instruction=snapshot.instruction,
            views=tuple(
                ResolvedView(
                    name=name,
                    path=path,
                    window=(record.video_windows or {}).get(name),
                    media_identity=(record.media_identity or {}).get(name),
                )
                for name, path in record.video_paths.items()
            ),
            options=ExecutionOptions(
                process_method=config["process_method"],
                force=config["force"],
                from_s=record.from_timestamp,
                to_s=record.to_timestamp,
                cache_dir=cache_dir,
            ),
            declared_identity=EvaluatorJudge(self.service).identity(config["process_method"]),
        )
