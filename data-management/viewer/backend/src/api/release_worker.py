"""Standalone release worker: performs one bounded queue pass over accepted release jobs."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from dotenv import load_dotenv
from physical_ai_curation.release.jobs import ReleaseJobStore
from physical_ai_curation.release.runner import run_queue

from .logging_config import configure_logging
from .services.release_service import (
    ReleaseApiError,
    ReleaseService,
    get_release_planner,
    load_release_api_settings,
    open_release_job_store,
)
from .services.viewer_release_planner import ViewerReleasePlanner

logger = logging.getLogger("release_worker")

EXIT_OK = 0
EXIT_NOT_CONFIGURED = 2
OUTCOME_SCHEMA = "curated-release-worker-outcome/v1"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one bounded pass over accepted curated release jobs.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Process one bounded queue pass and exit.")
    mode.add_argument("--watch", action="store_true", help="Continuously process bounded queue passes.")
    parser.add_argument("--limit", type=int, default=1, help="Maximum jobs to settle in this pass (1-100).")
    parser.add_argument("--worker-id", default="release-worker", help="Lease owner identifier for this worker.")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    return args


def _worker_seconds(name: str, default: str) -> float:
    value = float(os.environ.get(name, default))
    if not math.isfinite(value) or not 0.1 <= value <= 300:
        raise ValueError(f"{name} must be between 0.1 and 300 seconds")
    return value


async def _supervise_worker(poll_seconds: float, shutdown_seconds: float) -> None:
    while True:
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "src.api.release_worker",
                "--watch",
                cwd=Path(__file__).resolve().parents[2],
            )
            code = await process.wait()
            logger.error("release worker exited with code %s; restarting", code)
        except OSError:
            logger.error("release worker could not start; retrying")
        finally:
            if process is not None and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=shutdown_seconds)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        await asyncio.sleep(poll_seconds)


def start_release_worker() -> asyncio.Task[None] | None:
    """Supervise a configured worker after dotenv loading; the lifespan owns cancellation."""
    if not load_release_api_settings(os.environ).enabled:
        return None
    poll_seconds = _worker_seconds("RELEASE_WORKER_POLL_SECONDS", "3")
    shutdown_seconds = _worker_seconds("RELEASE_WORKER_SHUTDOWN_SECONDS", "10")
    return asyncio.create_task(_supervise_worker(poll_seconds, shutdown_seconds))


async def _watch_queue(
    store: ReleaseJobStore,
    planner: ViewerReleasePlanner,
    args: argparse.Namespace,
    poll_seconds: float,
    registration: Callable[[str], Awaitable[bool]] | None = None,
) -> None:
    while True:
        outcomes = await run_queue(
            store, planner.execution, worker_id=args.worker_id, limit=args.limit, registration=registration
        )
        if outcomes:
            logger.info(json.dumps({"schema_version": OUTCOME_SCHEMA, "outcomes": list(outcomes)}, sort_keys=True))
        await asyncio.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    """Run the worker; return a process exit code. Logs only safe, versioned summaries."""
    args = _parse_args(argv)
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    configure_logging()

    try:
        settings = load_release_api_settings(os.environ)
        poll_seconds = _worker_seconds("RELEASE_WORKER_POLL_SECONDS", "3") if args.watch else 3
    except ValueError:
        logger.error("release worker configuration is invalid")
        return EXIT_NOT_CONFIGURED
    if not settings.enabled:
        logger.error("release worker is disabled: RELEASE_JOB_ROOT is not set")
        return EXIT_NOT_CONFIGURED

    planner = get_release_planner() or ViewerReleasePlanner(settings)

    store = open_release_job_store(settings)
    service = ReleaseService(settings, planner=planner)
    try:
        if args.watch:
            asyncio.run(_watch_queue(store, planner, args, poll_seconds, service.register_pending))
            return EXIT_OK
        outcomes = asyncio.run(
            run_queue(
                store,
                planner.execution,
                worker_id=args.worker_id,
                limit=args.limit,
                registration=service.register_pending,
            )
        )
    except ReleaseApiError:
        logger.error("release worker pass failed")
        return EXIT_NOT_CONFIGURED

    logger.info(json.dumps({"schema_version": OUTCOME_SCHEMA, "outcomes": list(outcomes)}, sort_keys=True))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
