"""Inspect, serve, and evaluate checkpoint-bound VLA policies without ROS."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from .artifacts import file_identity, fingerprint, fresh_directory, inventory, unchanged, write_json
from .config import EvaluationConfig, load_config, require


def create_parser() -> argparse.ArgumentParser:
    """Parse host operations without importing Isaac, Rho, or OpenPI."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="operation", required=True)
    fingerprint_parser = commands.add_parser(
        "fingerprint", help="Hash a local checkpoint tree; never load tensors", allow_abbrev=False
    )
    fingerprint_parser.add_argument("--checkpoint", type=Path, required=True)
    for name in ("inspect", "serve", "run"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--config", type=Path, required=True)
        if name in ("serve", "run"):
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8000)
            command.add_argument("--socket", type=Path, help="Use a local Unix socket instead of TCP")
            command.add_argument("--device", default="cuda:0")
            command.add_argument("--output", type=Path, required=True, help="Fresh local evidence directory")
        if name == "run":
            command.add_argument("--accept-nvidia-terms", action="store_true")
            command.add_argument("--accept-nvidia-privacy", action="store_true")
    return parser


def _checkpoint(config: EvaluationConfig) -> dict[str, Any]:
    result = fingerprint(config.policy.checkpoint)
    require(
        result["sha256"] == config.policy.checkpoint_sha256, "Checkpoint content differs from configured tree SHA-256"
    )
    return result


def _source_files(config: EvaluationConfig) -> list[dict[str, Any]]:
    return [file_identity(config.path), *(file_identity(path) for path in sorted(Path(__file__).parent.glob("*.py")))]


def inspect(config: EvaluationConfig) -> dict[str, Any]:
    """Hash the checkpoint and inspect the explicit task dependencies without running models or physics."""
    from .tasks import inspect_task

    checkpoint = _checkpoint(config)
    task, records = inspect_task(config)
    unchanged(records)
    return {
        "status": "pass",
        "operation": "inspect",
        "contract": config.metadata(),
        "checkpoint": checkpoint,
        "task": task,
        "model_loaded": False,
        "simulation_started": False,
        "native_validation": "not_performed",
    }


def _output(config: EvaluationConfig, path: Path) -> Path:
    return fresh_directory(
        path,
        [
            config.path,
            config.policy.checkpoint,
            config.task.producer_root,
            config.task.expert_config,
            config.task.reference_root,
        ],
    )


def serve(args: argparse.Namespace, config: EvaluationConfig) -> dict[str, Any]:
    """Load a selected local framework checkpoint in its own process and expose the shared protocol."""
    from .backends import load_backend
    from .transport import serve_policy

    identities = _source_files(config)
    checkpoint = _checkpoint(config)
    output = _output(config, args.output)
    try:
        backend = load_backend(config, args.device)
        require(_checkpoint(config) == checkpoint, "Checkpoint changed during model loading")
        unchanged(identities)
        write_json(
            output / "server.json",
            {
                "contract": config.metadata(),
                "checkpoint": checkpoint,
                "producer": backend.provenance,
                "harness_sources": identities,
                "transport": {
                    "host": args.host,
                    "port": args.port,
                    "socket": str(args.socket) if args.socket else None,
                },
            },
        )
        serve_policy(config, backend, host=args.host, port=args.port, socket_path=args.socket)
        return {"status": "stopped", "operation": "serve", "output": str(output)}
    except (Exception, KeyboardInterrupt) as error:
        write_json(
            output / "failure.json",
            {
                "status": "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                "error": f"{type(error).__name__}: {error}",
                "config_sha256": config.sha256,
            },
        )
        raise


def run(args: argparse.Namespace, config: EvaluationConfig) -> dict[str, Any]:
    """Own one simulator application; errors retain evidence and release only this process's resources."""
    from .runner import run_evaluation
    from .tasks import _load_owner, inspect_task, load_simulation, validate_native_runtime
    from .transport import PolicyClient

    require(
        args.accept_nvidia_terms is True and args.accept_nvidia_privacy is True,
        "Native execution requires separate --accept-nvidia-terms and --accept-nvidia-privacy decisions",
    )
    inspection, identities = inspect_task(config, inspect_dependencies=False)
    validate_native_runtime()
    identities += _source_files(config)
    shutdown = _load_owner(config.task.producer_root, "producer_shutdown")
    collector = _load_owner(config.task.producer_root, "collect_privileged_demos")
    output = _output(config, args.output)
    require(shutil.disk_usage(output).free >= config.minimum_free_gib * 1024**3, "Insufficient evaluation storage")
    app, simulation, client, lock = None, None, None, None
    previous = {name: os.environ.get(name) for name in ("ACCEPT_EULA", "PRIVACY_CONSENT")}
    try:
        for name in previous:
            os.environ[name] = "Y"
        lock = shutdown.acquire_process_lock(collector._INSTANCE_LOCK_PATH, owner="VLA simulation evaluation")
        from isaaclab.app import AppLauncher

        app = AppLauncher(argparse.Namespace(headless=True, enable_cameras=True, device=args.device))
        require(app.app is not None, "Isaac application did not initialize")
        inspection, dependencies = inspect_task(config)
        identities += dependencies
        simulation = load_simulation(config, inspection, device=args.device)
        simulation.provenance["harness_sources"] = identities
        client = PolicyClient(config, host=args.host, port=args.port, socket_path=args.socket)
        result = run_evaluation(
            config,
            simulation,
            client,
            output,
            validate_inputs=lambda: unchanged(identities),
            publish_complete=False,
        )
    except (Exception, KeyboardInterrupt) as error:
        if not (output / "failure.json").exists():
            write_json(
                output / "failure.json",
                {
                    "status": "failed",
                    "config_sha256": config.sha256,
                    "error": f"{type(error).__name__}: {error}",
                    "complete": False,
                },
            )
        raise
    finally:
        primary_error = sys.exc_info()[1]
        cleanup = []
        if client is not None:
            cleanup.append(client.close)
        if app is not None:
            cleanup.append(shutdown.prepare_for_shutdown)
        if simulation is not None:
            cleanup.append(simulation.close)
        if app is not None and app.app is not None:
            cleanup.append(app.app.close)
        if lock is not None:
            cleanup.append(lambda: shutdown.release_process_lock(lock))
        try:
            failures = _cleanup(cleanup)
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        if failures:
            write_json(
                output / "shutdown-errors.json",
                {
                    "status": "failed",
                    "errors": [f"{type(error).__name__}: {error}" for error in failures],
                },
            )
            if primary_error is not None:
                primary_error.add_note(f"Additional cleanup failures: {failures}")
            else:
                raise failures[0]
    if result["status"] == "complete":
        unchanged(identities)
        result["files"] = inventory(output)
        write_json(output / "evaluation.json", result)
    return result


def _cleanup(callbacks: list[Callable[[], None]]) -> list[BaseException]:
    """Attempt every independent cleanup step and preserve failures in order."""
    failures = []
    for callback in callbacks:
        try:
            callback()
        except (Exception, KeyboardInterrupt) as error:
            failures.append(error)
    return failures


def main(argv: list[str] | None = None) -> int:
    """Return zero for completed work, one for runtime failure, two for invalid inputs, or 130 on interruption."""
    args = create_parser().parse_args(argv)
    try:
        with redirect_stdout(sys.stderr):
            if args.operation == "fingerprint":
                result = {"status": "pass", "checkpoint": fingerprint(args.checkpoint), "model_loaded": False}
            else:
                config = load_config(args.config)
                if args.operation == "inspect":
                    result = inspect(config)
                elif args.operation == "serve":
                    result = serve(args, config)
                else:
                    result = run(args, config)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0 if result["status"] in {"pass", "complete", "stopped"} else 1
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted"}))
        return 130
    except (ValueError, FileNotFoundError, FileExistsError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 2
    except Exception as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}))
        return 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        os._exit(code)
    raise SystemExit(code)
