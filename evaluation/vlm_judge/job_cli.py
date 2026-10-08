"""Shared local CLI commands for the durable judge lifecycle."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from .jobs import JudgeJobs

_LOGGER = logging.getLogger("evaluation.vlm_judge.cli")
_TERMINAL = {"succeeded", "partial", "failed", "cancelled"}


def add_job_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--operation",
        choices=("submit", "status", "list", "cancel", "retry", "results", "approve", "approvals", "worker", "apply"),
        default="submit",
    )
    parser.add_argument("--job-dir", type=Path, help="Durable job root shared with matching workers")
    parser.add_argument("--job-id", help="Accepted durable job identifier")
    parser.add_argument("--request-id", help="Idempotency key for submission")
    parser.add_argument("--approval-id", help="Explicit saved configuration approval")
    parser.add_argument(
        "--sample-references",
        type=Path,
        help="JSON object keyed by sample ID with author, annotation revision and snapshot ID",
    )
    parser.add_argument("--mode", choices=("judge", "sample", "judge-and-label"), default="judge")
    parser.add_argument(
        "--single", action="store_true", help="Explicit one-episode judge action without dataset approval"
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Persist submission and return its ID without executing; requires a matching worker",
    )
    parser.add_argument(
        "--acknowledge-exceptions",
        action="store_true",
        help="Explicitly acknowledge sample disagreement or inconclusive evidence",
    )
    parser.add_argument("--capacity", type=int, default=1)
    parser.add_argument("--capacity-scope", default="configured-inference-device")
    parser.add_argument("--episode-index", type=int, help="Episode for saved evidence retrieval")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--page-size", type=int, default=25)


def job_exit_code(job: dict[str, Any]) -> int:
    if job["status"] == "cancelled":
        return 130
    return 1 if job["status"] in {"partial", "failed"} else 0


async def execute_job_command(
    jobs: JudgeJobs,
    args: argparse.Namespace,
    *,
    dataset_id: str,
    actor: str,
    config: dict[str, Any],
    indices: list[int] | None = None,
) -> tuple[int, dict[str, Any]]:
    operation = args.operation
    if args.offset < 0 or not 1 <= args.page_size <= 100:
        raise ValueError("Invalid result page")
    if operation in {"status", "cancel", "retry", "approve", "apply"} and not args.job_id:
        raise ValueError("This operation requires --job-id")
    if operation == "list":
        return 0, await jobs.list(dataset_id, actor, offset=args.offset, limit=args.page_size)
    if operation == "approvals":
        return 0, await jobs.approvals(dataset_id, actor, offset=args.offset, limit=args.page_size)
    if operation == "approve":
        return 0, await jobs.approve(args.job_id, actor, acknowledge_exceptions=args.acknowledge_exceptions)
    if operation == "worker":
        await jobs.run_forever()
        return 0, {}
    if operation == "results":
        if args.episode_index is None or args.episode_index < 0:
            raise ValueError("Evidence retrieval requires --episode-index")
        evidence = await jobs.results(dataset_id, actor, args.episode_index)
        items = evidence[args.offset : args.offset + args.page_size]
        for item in items:
            if item["applicability"] != "withdrawn":
                try:
                    await jobs.resolver.resolve(
                        dataset_id,
                        args.episode_index,
                        principal_scope_id=actor,
                        views=tuple(item["config"].get("views") or ()),
                        annotation_author_id=item["input"]["annotation_author_id"],
                        expected_snapshot_id=item["input"]["snapshot_id"],
                    )
                except ValueError:
                    item["applicability"] = "stale"
                else:
                    item["applicability"] = "current"
        return 0, {"items": items, "total": len(evidence)}
    if operation == "status":
        job = await jobs.get(args.job_id, actor)
        job["targets"] = job["targets"][args.offset : args.offset + args.page_size]
        return job_exit_code(job), job
    if operation == "cancel":
        job = await jobs.cancel(args.job_id, actor)
        return job_exit_code(job), job
    if operation == "retry":
        job = await jobs.retry(args.job_id, actor)
    elif operation == "apply":
        job = await jobs.request_application(args.job_id, actor)
    elif operation == "submit":
        indices = indices or []
        if args.single and (len(indices) != 1 or args.mode != "judge"):
            raise ValueError("The single action requires exactly one episode in judge mode")
        samples = {}
        if args.sample_references is not None:
            value = json.loads(args.sample_references.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Sample references must be an object keyed by episode ID")
            samples = {int(index): reference for index, reference in value.items()}
        job = await jobs.submit(
            dataset_id,
            actor,
            indices,
            config,
            idempotency_key=args.request_id or uuid4().hex,
            mode=args.mode,
            approval_id=args.approval_id,
            samples=samples,
            require_approval=not args.single,
        )
    else:
        raise ValueError("Unknown lifecycle operation")
    _LOGGER.info("Judge work accepted job=%s targets=%d", job["id"], job["total"])
    if args.detach:
        return 0, {key: value for key, value in job.items() if key != "targets"}
    previous = None
    while True:
        job = await jobs.get(job["id"], actor)
        progress = (job["status"], job["judged"], job["applied"], job["errors"], job.get("application_errors", 0))
        if progress != previous:
            _LOGGER.info(
                "Judge progress job=%s status=%s judged=%d applied=%d errors=%d application_errors=%d",
                job["id"],
                *progress,
            )
            previous = progress
        if job["status"] in _TERMINAL:
            return job_exit_code(job), job
        if not await jobs.run_once():
            await asyncio.sleep(0.25)
