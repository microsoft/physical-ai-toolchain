"""Stream utilities for containerized training environments."""

from __future__ import annotations

import io
import os
import re
import sys

_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_TQDM_MININTERVAL_DEFAULT = "30"


class AnsiStrippingStream(io.TextIOBase):
    """Wraps a text stream for clean output in container log collectors.

    Handles two problems with tqdm/ANSI output in non-TTY environments:
    1. ANSI escape codes appear as raw text in kubectl logs / Azure ML logs
    2. tqdm uses carriage return (\\r) for in-place updates, which container
       log collectors buffer incorrectly — producing sparse, garbled output

    This stream strips ANSI codes and converts standalone \\r into \\n so each
    tqdm progress update appears as a distinct log line.
    """

    def __init__(self, wrapped: io.TextIOBase) -> None:
        self._wrapped = wrapped

    def write(self, s: str) -> int:
        cleaned = _ANSI_ESCAPE_PATTERN.sub("", s)
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        return self._wrapped.write(cleaned)

    def flush(self) -> None:
        self._wrapped.flush()

    def fileno(self) -> int:
        return self._wrapped.fileno()

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return getattr(self._wrapped, "encoding", "utf-8")


def install_ansi_stripping() -> None:
    """Replace sys.stdout with an ANSI-stripping wrapper if not already installed.

    Also sets TQDM_MININTERVAL to avoid flooding container logs with
    per-timestep progress lines.
    """
    os.environ.setdefault("TQDM_MININTERVAL", _TQDM_MININTERVAL_DEFAULT)
    if not isinstance(sys.stdout, AnsiStrippingStream):
        sys.stdout = AnsiStrippingStream(sys.stdout)
