"""Executable simulator for the operator subprocess protocol."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

from pydantic import ValidationError

from .models import OperatorMode
from .protocol import (
    WorkerAcknowledgement,
    WorkerCommand,
    WorkerProtocolError,
    WorkerReady,
)


def _emit(message: str) -> None:
    print(message, flush=True)


def main() -> None:
    """Run the deterministic JSON-line simulator."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--behavior",
        choices=(
            "normal",
            "crash_on_start",
            "crash_after_ready",
            "slow_start",
            "ignore_commands",
            "ignore_commands_and_sigterm",
            "wrong_ack_version",
        ),
        default="normal",
    )
    args = parser.parse_args()
    if args.behavior == "crash_on_start":
        raise SystemExit(2)
    if args.behavior == "slow_start":
        time.sleep(0.2)
    if args.behavior == "ignore_commands_and_sigterm":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    session_id = os.environ["OPERATOR_SESSION_ID"]
    mode = OperatorMode(os.environ["OPERATOR_SESSION_MODE"])
    _emit(WorkerReady(session_id=session_id, mode=mode).model_dump_json())
    if args.behavior == "crash_after_ready":
        time.sleep(0.05)
        raise SystemExit(3)

    handled: dict[str, tuple[tuple[str, str], WorkerAcknowledgement]] = {}
    for line in sys.stdin:
        try:
            command = WorkerCommand.model_validate_json(line)
        except ValidationError:
            raise SystemExit(4) from None
        if command.session_id != session_id:
            _emit(
                WorkerProtocolError(
                    session_id=session_id,
                    command_id=command.command_id,
                    message="worker protocol session mismatch",
                ).model_dump_json()
            )
            continue
        payload = (command.session_id, command.action)
        if previous := handled.get(command.command_id):
            if previous[0] != payload:
                _emit(
                    WorkerProtocolError(
                        session_id=session_id,
                        command_id=command.command_id,
                        message="worker command_id payload conflict",
                    ).model_dump_json()
                )
            else:
                _emit(previous[1].model_dump_json())
            continue
        if args.behavior in {"ignore_commands", "ignore_commands_and_sigterm"}:
            continue
        acknowledgement = WorkerAcknowledgement(
            session_id=session_id,
            command_id=command.command_id,
            action=command.action,
            cleanup_complete=command.action in {"finish", "cancel"},
        )
        handled[command.command_id] = (payload, acknowledgement)
        if args.behavior == "wrong_ack_version":
            data = acknowledgement.model_dump()
            data["version"] = 999
            _emit(json.dumps(data))
        else:
            _emit(acknowledgement.model_dump_json())
        if command.action in {"finish", "cancel"}:
            return


if __name__ == "__main__":
    main()
