"""Supervised Linux process trees: a guardian reaps each tree and issues proof of the stop."""

from __future__ import annotations

import contextlib
import ctypes
import json
import math
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, Literal, NoReturn, Self

from physical_ai_curation.release.errors import ReleaseVerificationError

MIN_MEMORY_BYTES = 64 * 1024 * 1024
DEFAULT_FILE_BYTES = 1 << 40
DEFAULT_GRACE_SECONDS = 5.0
DEFAULT_LIVENESS_SECONDS = 30.0
DEFAULT_RECOVERY_SECONDS = 30.0
MAX_GRACE_SECONDS = 3600.0
MAX_LIVENESS_SECONDS = 86400.0
KILL_TIMEOUT_SECONDS = 10.0
START_TIMEOUT_SECONDS = 30.0
POLL_SECONDS = 0.01
MAX_ARGV_BYTES = 32 * 1024
MAX_FRAME_BYTES = 64 * 1024
MAX_PROOF_BYTES = 16 * 1024
MAX_INHERITED_FDS = 8
_PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])
_PR_SET_CHILD_SUBREAPER = 36
_TERMINATION_SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP")
_EXIT_FAILED = 1
_EXIT_UNRESOLVED = 3
_PROTOCOL_ERROR = "supervision protocol was violated"
_CONTROL_LOST = "supervised control channel was lost"
_UNRESOLVED = "stop proof is unresolved; supervised effects are not confirmed stopped"
# Applies limits in the wrapper and holds exec until the guardian opens the start gate.
_WRAPPER = (
    "import os, resource, signal, sys\n"
    "memory, file_bytes, gate = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])\n"
    "resource.setrlimit(resource.RLIMIT_AS, (memory, memory))\n"
    "resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))\n"
    "for name in ('SIGXFSZ', 'SIGINT', 'SIGTERM', 'SIGHUP'):\n"
    "    if hasattr(signal, name):\n"
    "        signal.signal(getattr(signal, name), signal.SIG_DFL)\n"
    "if os.read(gate, 1) != b'1':\n"
    "    sys.exit(70)\n"
    "os.close(gate)\n"
    "os.execvp(sys.argv[4], sys.argv[4:])\n"
)
_GUARDIAN_BOOT = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from physical_ai_curation.release.supervision import run_guardian\n"
    "raise SystemExit(run_guardian(int(sys.argv[2])))\n"
)


@dataclass(frozen=True)
class ProcessIdentity:
    """Kernel identity of one process: pid, start tick, and boot, which together resist pid reuse."""

    pid: int
    start: int
    boot_id: str

    @classmethod
    def of(cls, pid: int) -> Self:
        return cls(pid=pid, start=_proc_stat(pid).start, boot_id=_boot_id())

    def to_dict(self) -> dict[str, Any]:
        return {"pid": self.pid, "start": self.start, "boot_id": self.boot_id}

    def has_exited(self) -> bool:
        """Use kernel identity and pidfd evidence, never PID existence alone."""
        return _identity_exited(self, 0.0)

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _mapping(value, {"pid", "start", "boot_id"})
        boot_id = fields["boot_id"]
        if not isinstance(boot_id, str) or not boot_id:
            raise ReleaseVerificationError(_PROTOCOL_ERROR)
        return cls(pid=_index(fields["pid"]), start=_index(fields["start"]), boot_id=boot_id)


@dataclass(frozen=True)
class SupervisedRuntime:
    """Identities a parent persists before effects start so a restarted parent can recover the stop proof."""

    guardian: ProcessIdentity
    leader: ProcessIdentity
    proof_path: Path | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "guardian": self.guardian.to_dict(),
            "leader": self.leader.to_dict(),
            "proof_path": None if self.proof_path is None else str(self.proof_path),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _mapping(value, {"guardian", "leader", "proof_path"})
        proof_path = _optional_str(fields["proof_path"])
        return cls(
            guardian=ProcessIdentity.from_dict(fields["guardian"]),
            leader=ProcessIdentity.from_dict(fields["leader"]),
            proof_path=None if proof_path is None else Path(proof_path),
        )


@dataclass(frozen=True)
class StopProof:
    """Guardian-issued evidence that the leader and every reachable descendant were reaped."""

    guardian: ProcessIdentity
    leader: ProcessIdentity
    leader_exit_code: int
    members_reaped: int
    state: Literal["stopped"] = "stopped"

    @classmethod
    def from_payload(cls, value: object, runtime: SupervisedRuntime) -> Self:
        try:
            fields = _mapping(value, {"v", "state", "guardian", "leader", "leader_exit_code", "members_reaped"})
            if fields["v"] != 1 or fields["state"] != "stopped":
                raise ReleaseVerificationError(_UNRESOLVED)
            guardian = ProcessIdentity.from_dict(fields["guardian"])
            leader = ProcessIdentity.from_dict(fields["leader"])
            exit_code = _exact_int(fields["leader_exit_code"])
            reaped = _index(fields["members_reaped"])
        except ReleaseVerificationError:
            raise ReleaseVerificationError(_UNRESOLVED) from None
        if guardian != runtime.guardian or leader != runtime.leader:
            raise ReleaseVerificationError(_UNRESOLVED)
        return cls(guardian=guardian, leader=leader, leader_exit_code=exit_code, members_reaped=reaped)


def bounded_seconds(value: object, *, low: float, high: float, name: str) -> float:
    """Return a finite duration within [low, high]; reject booleans and non-numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ReleaseVerificationError(f"{name} is outside its validated range")
    if not low <= value <= high:
        raise ReleaseVerificationError(f"{name} is outside its validated range")
    return float(value)


def recover_stop_proof(runtime: SupervisedRuntime, *, timeout_seconds: float = DEFAULT_RECOVERY_SECONDS) -> StopProof:
    """Return the guardian's stop proof, waiting for the guardian to finish; never infer a stop without proof."""
    timeout = bounded_seconds(timeout_seconds, low=0.0, high=MAX_LIVENESS_SECONDS, name="stop proof recovery window")
    proof = _read_proof(runtime)
    if proof is not None:
        return proof
    if not _identity_exited(runtime.guardian, timeout):
        raise ReleaseVerificationError(_UNRESOLVED)
    proof = _read_proof(runtime)
    if proof is None:
        raise ReleaseVerificationError(_UNRESOLVED)
    return proof


@dataclass(frozen=True)
class _ProcStat:
    state: str
    ppid: int
    pgrp: int
    start: int


class _Channel:
    """Length-prefixed JSON frames with a fixed size cap, read only after the socket reports readiness."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = bytearray()
        self._queue: deque[dict[str, Any]] = deque()
        self.closed = False

    def fileno(self) -> int:
        return self._sock.fileno()

    def send(self, message: Mapping[str, Any]) -> None:
        body = json.dumps(message, separators=(",", ":"), sort_keys=True).encode()
        if len(body) > MAX_FRAME_BYTES:
            raise ReleaseVerificationError(_PROTOCOL_ERROR)
        try:
            self._sock.sendall(struct.pack(">I", len(body)) + body)
        except OSError:
            self.closed = True
            raise ReleaseVerificationError(_CONTROL_LOST) from None

    def fill(self, timeout: float) -> None:
        if self.closed:
            return
        ready, _, _ = select.select([self._sock], [], [], max(timeout, 0.0))
        if not ready:
            return
        try:
            chunk = self._sock.recv(MAX_FRAME_BYTES)
        except OSError:
            chunk = b""
        if not chunk:
            self.closed = True
            return
        self._buffer.extend(chunk)
        while len(self._buffer) >= 4:
            (size,) = struct.unpack(">I", self._buffer[:4])
            if size > MAX_FRAME_BYTES:
                raise ReleaseVerificationError(_PROTOCOL_ERROR)
            if len(self._buffer) < 4 + size:
                return
            body = bytes(self._buffer[4 : 4 + size])
            del self._buffer[: 4 + size]
            self._queue.append(_decode(body))

    def pending(self) -> bool:
        return bool(self._queue)

    def pop(self) -> dict[str, Any]:
        return self._queue.popleft()

    def next_message(self, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while not self._queue:
            remaining = deadline - time.monotonic()
            if self.closed or remaining <= 0:
                return None
            self.fill(remaining)
        return self._queue.popleft()

    def close(self) -> None:
        self.closed = True
        self._sock.close()


def _decode(body: bytes) -> dict[str, Any]:
    try:
        message = json.loads(body)
    except ValueError:
        raise ReleaseVerificationError(_PROTOCOL_ERROR) from None
    if not isinstance(message, dict) or not isinstance(message.get("op"), str):
        raise ReleaseVerificationError(_PROTOCOL_ERROR)
    return message


def _mapping(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ReleaseVerificationError(_PROTOCOL_ERROR)
    return value


def _exact_int(value: object) -> int:
    if type(value) is not int:
        raise ReleaseVerificationError(_PROTOCOL_ERROR)
    return value


def _index(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ReleaseVerificationError(_PROTOCOL_ERROR)
    return value


def _optional_str(value: object) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ReleaseVerificationError(_PROTOCOL_ERROR)
    return value


def _require_linux() -> None:
    if (
        not sys.platform.startswith("linux")
        or not hasattr(os, "pidfd_open")
        or not hasattr(signal, "pidfd_send_signal")
    ):
        raise ReleaseVerificationError("supervised release processes require Linux pidfds and procfs")


def _proc_stat(pid: int) -> _ProcStat:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return _ProcStat(state=fields[0], ppid=int(fields[1]), pgrp=int(fields[2]), start=int(fields[19]))


def _boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _signal_group(group: int, signum: int) -> None:
    try:
        os.killpg(group, signum)
    except ProcessLookupError:
        return


def _signal_verified(pid: int, start: int, signum: int) -> None:
    """Signal a descendant only after a pidfd proves it is still the process that was observed."""
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        if _proc_stat(pid).start == start:
            signal.pidfd_send_signal(fd, signum)
    except (FileNotFoundError, ProcessLookupError):
        return
    finally:
        os.close(fd)


def _identity_exited(identity: ProcessIdentity, timeout_seconds: float) -> bool:
    """Wait up to the timeout for the identified process; a reused pid or a previous boot counts as exited."""
    if identity.boot_id != _boot_id():
        return True
    try:
        fd = os.pidfd_open(identity.pid)
    except ProcessLookupError:
        return True
    try:
        try:
            current = _proc_stat(identity.pid).start
        except FileNotFoundError:
            return True
        if current != identity.start:
            return True
        return bool(select.select([fd], [], [], max(timeout_seconds, 0.0))[0])
    finally:
        os.close(fd)


def _leader_exited(pid: int) -> bool:
    return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def _read_proof(runtime: SupervisedRuntime) -> StopProof | None:
    path = runtime.proof_path
    if path is None:
        raise ReleaseVerificationError(_UNRESOLVED)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise ReleaseVerificationError(_UNRESOLVED) from None
    try:
        with os.fdopen(fd, "rb") as handle:
            import stat

            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROOF_BYTES:
                raise ReleaseVerificationError(_UNRESOLVED)
            data = handle.read(MAX_PROOF_BYTES + 1)
    except OSError:
        raise ReleaseVerificationError(_UNRESOLVED) from None
    if len(data) > MAX_PROOF_BYTES:
        raise ReleaseVerificationError(_UNRESOLVED)
    try:
        payload = json.loads(data)
    except ValueError:
        raise ReleaseVerificationError(_UNRESOLVED) from None
    return StopProof.from_payload(payload, runtime)


# Guardian side: runs in its own session, ignores termination signals, and owns the tree until it reports proof.


@dataclass(frozen=True)
class _GuardianConfig:
    argv: tuple[str, ...]
    memory: int
    file_bytes: int
    cwd: str | None
    grace: float
    liveness: float
    output_fd: int
    inherit_fds: tuple[int, ...]
    proof_path: str | None

    @classmethod
    def from_message(cls, message: Mapping[str, Any]) -> Self:
        argv = message["argv"]
        inherited = message["inherit_fds"]
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise ReleaseVerificationError(_PROTOCOL_ERROR)
        if not isinstance(inherited, list):
            raise ReleaseVerificationError(_PROTOCOL_ERROR)
        return cls(
            argv=tuple(argv),
            memory=_exact_int(message["memory"]),
            file_bytes=_exact_int(message["file"]),
            cwd=_optional_str(message["cwd"]),
            grace=bounded_seconds(message["grace"], low=0.0, high=MAX_GRACE_SECONDS, name="grace"),
            liveness=bounded_seconds(message["liveness"], low=0.001, high=MAX_LIVENESS_SECONDS, name="liveness"),
            output_fd=_index(message["output_fd"]),
            inherit_fds=tuple(_index(item) for item in inherited),
            proof_path=_optional_str(message["proof_path"]),
        )


@dataclass
class _Tree:
    pid: int
    pidfd: int
    gate: int | None
    stdin: BinaryIO | None
    identity: ProcessIdentity
    reaped: int = 0

    def reap(self, pid: int) -> None:
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if waited:
            self.reaped += 1

    def release_gate(self) -> None:
        if self.gate is None:
            return
        try:
            os.write(self.gate, b"1")
        finally:
            self.close_gate()

    def close_gate(self) -> None:
        if self.gate is not None:
            os.close(self.gate)
            self.gate = None

    def close(self) -> None:
        self.close_gate()
        if self.stdin is not None:
            self.stdin.close()
        os.close(self.pidfd)


def _spawn(config: _GuardianConfig) -> _Tree:
    gate_read, gate_write = os.pipe()
    try:
        leader = subprocess.Popen(
            [sys.executable, "-c", _WRAPPER, str(config.memory), str(config.file_bytes), str(gate_read), *config.argv],
            stdin=subprocess.PIPE,
            stdout=config.output_fd,
            stderr=subprocess.DEVNULL,
            cwd=config.cwd,
            close_fds=True,
            pass_fds=(gate_read, *config.inherit_fds),
            start_new_session=True,
        )
    except BaseException:
        os.close(gate_write)
        raise
    finally:
        os.close(gate_read)
        os.close(config.output_fd)
    return _Tree(
        pid=leader.pid,
        pidfd=os.pidfd_open(leader.pid),
        gate=gate_write,
        stdin=leader.stdin,
        identity=ProcessIdentity(leader.pid, _proc_stat(leader.pid).start, _boot_id()),
    )


def _await_config(channel: _Channel) -> dict[str, Any]:
    message = channel.next_message(START_TIMEOUT_SECONDS)
    if message is None or message["op"] != "config":
        raise ReleaseVerificationError(_PROTOCOL_ERROR)
    return message


def _send_quiet(channel: _Channel, message: Mapping[str, Any]) -> None:
    try:
        channel.send(message)
    except ReleaseVerificationError:
        return


def _set_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise ReleaseVerificationError("guardian could not become a child subreaper")


def _report_exit(channel: _Channel, tree: _Tree) -> None:
    info = os.waitid(os.P_PID, tree.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if info is None:
        return
    code = info.si_status if info.si_code == os.CLD_EXITED else -info.si_status
    _send_quiet(channel, {"op": "leader_exited", "code": code})


def _supervise(channel: _Channel, tree: _Tree, liveness: float) -> None:
    """Hold the gate until go, then keep the tree while pings arrive; return on stop, loss, or silence."""
    running = False
    watching = True
    last_ping = time.monotonic()
    while not channel.closed:
        timeout: float | None = None
        if running:
            timeout = liveness - (time.monotonic() - last_ping)
            if timeout <= 0:
                return
        watched = [channel.fileno(), tree.pidfd] if watching else [channel.fileno()]
        readable, _, _ = select.select(watched, [], [], timeout)
        if watching and tree.pidfd in readable:
            watching = False
            _report_exit(channel, tree)
        if channel.fileno() in readable:
            channel.fill(0)
        while channel.pending():
            message = channel.pop()
            op = message["op"]
            if op == "go" and not running:
                tree.release_gate()
                running = True
                last_ping = time.monotonic()
            elif op == "ping":
                if running:
                    last_ping = time.monotonic()
            else:
                return


def _tree_members(leader: int, guardian: int) -> dict[int, _ProcStat]:
    """Return live and zombie descendants: members of the leader's group or reparented under the guardian."""
    table: dict[int, _ProcStat] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid in (guardian, leader):
            continue
        try:
            table[pid] = _proc_stat(pid)
        except (OSError, ValueError, IndexError):
            continue
    members: dict[int, _ProcStat] = {}
    reachable = {leader}
    changed = True
    while changed:
        changed = False
        for pid, stat in table.items():
            if pid in members:
                continue
            if stat.pgrp == leader or stat.ppid in reachable or stat.ppid == guardian:
                members[pid] = stat
                reachable.add(pid)
                changed = True
    return members


def _drain(tree: _Tree, guardian: int, signum: int, deadline: float) -> bool:
    """Signal and reap the tree until only the unreaped leader remains; report whether it drained by the deadline."""
    while True:
        _signal_group(tree.pid, signum)
        members = _tree_members(tree.pid, guardian)
        for pid, stat in members.items():
            if stat.state == "Z":
                if stat.ppid == guardian:
                    tree.reap(pid)
            else:
                _signal_verified(pid, stat.start, signum)
        if not members and _leader_exited(tree.pid):
            return True
        if time.monotonic() >= deadline:
            return False
        select.select([], [], [], POLL_SECONDS)


def _terminate(tree: _Tree, guardian: int, grace_seconds: float) -> int | None:
    tree.close_gate()
    _drain(tree, guardian, signal.SIGTERM, time.monotonic() + grace_seconds)
    if not _drain(tree, guardian, signal.SIGKILL, time.monotonic() + KILL_TIMEOUT_SECONDS):
        return None
    _, status = os.waitpid(tree.pid, 0)
    return os.waitstatus_to_exitcode(status)


def _write_proof(path: Path, payload: Mapping[str, Any]) -> None:
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def run_guardian(fd: int) -> int:
    """Guardian entry point: own one tree, then report proof of its stop or report it unresolved."""
    channel = _Channel(socket.socket(fileno=fd))
    for name in _TERMINATION_SIGNALS:
        signal.signal(getattr(signal, name), signal.SIG_IGN)
    try:
        _set_subreaper()
        config = _GuardianConfig.from_message(_await_config(channel))
        tree = _spawn(config)
    except Exception:
        _send_quiet(channel, {"op": "failed"})
        channel.close()
        return _EXIT_FAILED
    guardian = ProcessIdentity.of(os.getpid())
    _send_quiet(channel, {"op": "spawned", "guardian": guardian.to_dict(), "leader": tree.identity.to_dict()})
    with contextlib.suppress(Exception):
        _supervise(channel, tree, config.liveness)
    payload: dict[str, Any] | None = None
    try:
        code = _terminate(tree, guardian.pid, config.grace)
        if code is None:
            raise ReleaseVerificationError(_UNRESOLVED)
        payload = {
            "v": 1,
            "state": "stopped",
            "guardian": guardian.to_dict(),
            "leader": tree.identity.to_dict(),
            "leader_exit_code": code,
            "members_reaped": tree.reaped,
        }
        if config.proof_path is not None:
            _write_proof(Path(config.proof_path), payload)
    except Exception:
        _send_quiet(channel, {"op": "unresolved"})
        channel.close()
        return _EXIT_UNRESOLVED
    finally:
        tree.close()
    _send_quiet(channel, {"op": "stopped", "proof": payload})
    channel.close()
    return 0


class ProcessSupervisor:
    """Run one trusted argv under a guardian that reaps its whole tree on stop or on parent loss."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        memory_bytes: int,
        file_bytes: int = DEFAULT_FILE_BYTES,
        cwd: Path | None = None,
        grace_seconds: float = DEFAULT_GRACE_SECONDS,
        liveness_seconds: float = DEFAULT_LIVENESS_SECONDS,
        inherit_fds: Sequence[int] = (),
        proof_path: Path | None = None,
        on_started: Callable[[SupervisedRuntime], None] | None = None,
    ) -> None:
        _require_linux()
        if not argv or not all(isinstance(item, str) and item and "\x00" not in item for item in argv):
            raise ReleaseVerificationError("supervised argv must be a nonempty sequence of strings")
        if sum(len(item.encode()) for item in argv) > MAX_ARGV_BYTES:
            raise ReleaseVerificationError("supervised argv exceeds its size limit")
        if type(memory_bytes) is not int or memory_bytes < MIN_MEMORY_BYTES:
            raise ReleaseVerificationError("supervised memory limit is below the minimum")
        if type(file_bytes) is not int or file_bytes <= 0:
            raise ReleaseVerificationError("supervised file size limit must be positive")
        if (
            len(inherit_fds) > MAX_INHERITED_FDS
            or len(set(inherit_fds)) != len(inherit_fds)
            or not all(type(fd) is int and fd >= 0 for fd in inherit_fds)
        ):
            raise ReleaseVerificationError("inherited descriptors are outside their validated range")
        self._argv = tuple(argv)
        self._memory_bytes = memory_bytes
        self._file_bytes = file_bytes
        self._cwd = cwd
        self._grace_seconds = bounded_seconds(
            grace_seconds, low=0.0, high=MAX_GRACE_SECONDS, name="supervised grace period"
        )
        self._liveness_seconds = bounded_seconds(
            liveness_seconds, low=0.001, high=MAX_LIVENESS_SECONDS, name="supervised liveness window"
        )
        self._inherit_fds = tuple(inherit_fds)
        self._proof_path = proof_path
        self._on_started = on_started
        self._guardian: subprocess.Popen[bytes] | None = None
        self._channel: _Channel | None = None
        self._runtime: SupervisedRuntime | None = None
        self._output: BinaryIO | None = None
        self._exit_code: int | None = None
        self._reply: dict[str, Any] | None = None
        self._proof: StopProof | None = None

    @property
    def runtime(self) -> SupervisedRuntime:
        if self._runtime is None:
            raise ReleaseVerificationError("supervised process was not started")
        return self._runtime

    @property
    def output(self) -> BinaryIO:
        if self._output is None:
            raise ReleaseVerificationError("supervised process was not started")
        return self._output

    def start(self) -> SupervisedRuntime:
        """Launch the guardian and tree; effects run only after the registration callback succeeds."""
        if self._guardian is not None:
            raise ReleaseVerificationError("supervised process already started")
        parent, child = socket.socketpair()
        output_read, output_write = os.pipe()
        config = {
            "op": "config",
            "argv": list(self._argv),
            "memory": self._memory_bytes,
            "file": self._file_bytes,
            "cwd": None if self._cwd is None else str(self._cwd),
            "grace": self._grace_seconds,
            "liveness": self._liveness_seconds,
            "output_fd": output_write,
            "inherit_fds": list(self._inherit_fds),
            "proof_path": None if self._proof_path is None else str(self._proof_path),
        }
        try:
            self._guardian = subprocess.Popen(
                [sys.executable, "-c", _GUARDIAN_BOOT, _PACKAGE_ROOT, str(child.fileno())],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(child.fileno(), output_write, *self._inherit_fds),
                close_fds=True,
                start_new_session=True,
            )
        except OSError:
            parent.close()
            os.close(output_read)
            raise ReleaseVerificationError("supervised guardian could not start") from None
        finally:
            child.close()
            os.close(output_write)
        self._channel = _Channel(parent)
        try:
            runtime = self._handshake(config)
        except BaseException:
            os.close(output_read)
            self._teardown(self._runtime)
            raise
        self._output = os.fdopen(output_read, "rb")
        return runtime

    def wait_for_exit(self, timeout_seconds: float) -> bool:
        """Wait up to the timeout for the leader to exit; raise if the guardian stopped or was lost."""
        timeout = bounded_seconds(timeout_seconds, low=0.0, high=MAX_LIVENESS_SECONDS, name="exit wait window")
        channel = self._require_channel()
        deadline = time.monotonic() + timeout
        while True:
            if self._exit_code is not None:
                return True
            if self._reply is not None or channel.closed:
                raise ReleaseVerificationError(_CONTROL_LOST)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            channel.fill(remaining)
            while channel.pending():
                self._dispatch(channel.pop())

    def ping(self) -> None:
        """Renew the guardian's liveness window; silence beyond it stops the tree."""
        self._send({"op": "ping"})

    def stop(self) -> StopProof:
        """Stop the tree and return its proof, raising when the stop cannot be proven.

        Repeat calls return the same proof.
        """
        if self._proof is not None:
            return self._proof
        runtime = self._runtime
        if runtime is None:
            raise ReleaseVerificationError("supervised process was not started")
        try:
            proof = self._finish(runtime)
        finally:
            self._teardown(runtime)
        self._proof = proof
        return proof

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self._runtime is not None and self._proof is None:
                self.stop()
        finally:
            if self._output is not None:
                self._output.close()

    def _stop_budget(self) -> float:
        return self._grace_seconds + KILL_TIMEOUT_SECONDS + 5.0

    def _require_channel(self) -> _Channel:
        if self._channel is None:
            raise ReleaseVerificationError("supervised process was not started")
        return self._channel

    def _send(self, message: Mapping[str, Any]) -> None:
        self._require_channel().send(message)

    def _expect(self, ops: set[str], deadline: float) -> dict[str, Any] | None:
        channel = self._require_channel()
        while True:
            message = channel.next_message(deadline - time.monotonic())
            if message is None:
                return None
            if message["op"] in ops:
                return message
            self._dispatch(message)

    def _dispatch(self, message: dict[str, Any]) -> None:
        op = message["op"]
        if op == "leader_exited":
            if self._exit_code is None:
                self._exit_code = _exact_int(message.get("code"))
        elif op in {"stopped", "unresolved"}:
            self._reply = message
        else:
            raise ReleaseVerificationError(_PROTOCOL_ERROR)

    def _handshake(self, config: Mapping[str, Any]) -> SupervisedRuntime:
        self._send(config)
        message = self._expect({"spawned", "failed"}, time.monotonic() + START_TIMEOUT_SECONDS)
        if message is None:
            self._abort_start("supervised start failed", spawned=True)
        if message["op"] == "failed":
            self._teardown(None)
            raise ReleaseVerificationError("supervised start failed")
        try:
            runtime = SupervisedRuntime(
                guardian=ProcessIdentity.from_dict(message.get("guardian")),
                leader=ProcessIdentity.from_dict(message.get("leader")),
                proof_path=self._proof_path,
            )
        except ReleaseVerificationError:
            self._abort_start("supervised start protocol failed", spawned=True)
        self._runtime = runtime
        if self._on_started is not None:
            try:
                self._on_started(runtime)
            except Exception:
                self._abort_start("registration failed", spawned=True)
        self._send({"op": "go"})
        return runtime

    def _abort_start(self, detail: str, *, spawned: bool) -> NoReturn:
        with contextlib.suppress(ReleaseVerificationError):
            self._send({"op": "abort"})
        reply = self._expect({"stopped", "unresolved"}, time.monotonic() + self._stop_budget())
        self._teardown(self._runtime)
        if spawned and (reply is None or reply["op"] != "stopped"):
            raise ReleaseVerificationError(f"{detail}; {_UNRESOLVED}")
        raise ReleaseVerificationError(detail)

    def _request_stop(self) -> dict[str, Any] | None:
        try:
            self._send({"op": "stop"})
        except ReleaseVerificationError:
            return None
        return self._expect({"stopped", "unresolved"}, time.monotonic() + self._stop_budget())

    def _finish(self, runtime: SupervisedRuntime) -> StopProof:
        reply = self._reply if self._reply is not None else self._request_stop()
        if reply is not None and reply["op"] == "unresolved":
            raise ReleaseVerificationError(_UNRESOLVED)
        if reply is not None:
            return StopProof.from_payload(reply.get("proof"), runtime)
        return recover_stop_proof(runtime, timeout_seconds=self._stop_budget())

    def _teardown(self, runtime: SupervisedRuntime | None) -> None:
        if self._channel is not None:
            self._channel.close()
        guardian = self._guardian
        if guardian is None:
            return
        if runtime is None or _identity_exited(runtime.guardian, self._stop_budget()):
            guardian.wait()
