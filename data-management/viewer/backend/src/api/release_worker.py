"""Standalone release worker: performs one bounded queue pass over accepted release jobs."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

from physical_ai_curation.release.runner import run_queue

from .services.release_service import (
    ReleaseApiError,
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
    parser.add_argument("--once", action="store_true", required=True, help="Process one bounded queue pass and exit.")
    parser.add_argument("--limit", type=int, default=1, help="Maximum jobs to settle in this pass (1-100).")
    parser.add_argument("--worker-id", default="release-worker", help="Lease owner identifier for this worker.")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run the worker; return a process exit code. Logs only safe, versioned summaries."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = _parse_args(argv)

    try:
        settings = load_release_api_settings(os.environ)
    except ValueError:
        logger.error("release worker configuration is invalid")
        return EXIT_NOT_CONFIGURED
    if not settings.enabled:
        logger.error("release worker is disabled: RELEASE_JOB_ROOT is not set")
        return EXIT_NOT_CONFIGURED

    planner = get_release_planner() or ViewerReleasePlanner(settings)

    store = open_release_job_store(settings)
    try:
        outcomes = asyncio.run(run_queue(store, planner.execution, worker_id=args.worker_id, limit=args.limit))
    except ReleaseApiError:
        logger.error("release worker pass failed")
        return EXIT_NOT_CONFIGURED

    logger.info(json.dumps({"schema_version": OUTCOME_SCHEMA, "outcomes": list(outcomes)}, sort_keys=True))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
