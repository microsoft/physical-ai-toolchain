"""Hypothesis property-based tests for stream.AnsiStrippingStream."""

import io

from hypothesis import given
from hypothesis import strategies as st

from .conftest import load_training_module

stream_module = load_training_module("stream_under_test", "training/stream.py")
AnsiStrippingStream = stream_module.AnsiStrippingStream
_ANSI_ESCAPE_PATTERN = stream_module._ANSI_ESCAPE_PATTERN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class CaptureStream(io.TextIOBase):
    """In-memory text stream that records everything written."""

    def __init__(self) -> None:
        self.parts: list[str] = []

    def write(self, s: str) -> int:
        self.parts.append(s)
        return len(s)

    def flush(self) -> None:
        pass

    @property
    def value(self) -> str:
        return "".join(self.parts)


def _make_stream() -> tuple[AnsiStrippingStream, CaptureStream]:
    inner = CaptureStream()
    return AnsiStrippingStream(inner), inner


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_CSI_PARAMS = st.text(alphabet="0123456789;", min_size=0, max_size=8)
_CSI_FINAL = st.sampled_from(list("ABCDEFGHJKSTfmnsulh"))  # cspell:disable-line

ansi_codes = st.builds(lambda p, f: f"\x1b[{p}{f}", _CSI_PARAMS, _CSI_FINAL)
printable_text = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z", "S"), exclude_characters="\x1b\r"),
    min_size=0,
    max_size=120,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@given(text=printable_text)
def test_plain_text_passes_through(text):
    """Text without ANSI codes or \\r is written unchanged."""
    stream, inner = _make_stream()
    stream.write(text)
    assert inner.value == text


@given(code=ansi_codes)
def test_ansi_codes_are_stripped(code):
    """Every generated CSI sequence is completely removed."""
    stream, inner = _make_stream()
    stream.write(code)
    assert inner.value == ""


@given(code=ansi_codes, text=printable_text)
def test_ansi_interleaved_with_text(code, text):
    """Only the visible text survives when mixed with ANSI codes."""
    stream, inner = _make_stream()
    stream.write(code + text + code)
    assert inner.value == text


@given(text=printable_text)
def test_cr_lf_preserved(text):
    """\\r\\n pairs are normalized to \\n, not doubled."""
    stream, inner = _make_stream()
    stream.write(text + "\r\n")
    assert inner.value == text + "\n"


@given(text=printable_text)
def test_bare_cr_becomes_lf(text):
    """A standalone \\r is converted to \\n."""
    stream, inner = _make_stream()
    stream.write(text + "\r")
    assert inner.value == text + "\n"


@given(text=printable_text)
def test_output_never_contains_ansi(text):
    """No matter the input, the output is free of ANSI escape sequences."""
    stream, inner = _make_stream()
    stream.write(text)
    assert _ANSI_ESCAPE_PATTERN.search(inner.value) is None


@given(text=printable_text)
def test_output_never_contains_bare_cr(text):
    """Output never contains a \\r that is not part of \\r\\n."""
    stream, inner = _make_stream()
    stream.write(text)
    output = inner.value
    assert "\r" not in output


def test_isatty_returns_false():
    """AnsiStrippingStream always reports non-TTY."""
    stream, _ = _make_stream()
    assert stream.isatty() is False


def test_encoding_falls_back_to_utf8():
    """When the wrapped stream has no encoding attr, default to utf-8."""

    class NoEncodingStream:
        def write(self, s: str) -> int:
            return len(s)

        def flush(self) -> None:
            pass

    stream = AnsiStrippingStream(NoEncodingStream())
    assert stream.encoding == "utf-8"


@given(encoding=st.sampled_from(["utf-8", "ascii", "latin-1", "utf-16"]))
def test_encoding_delegates_to_wrapped(encoding):
    """Encoding property mirrors the wrapped stream's encoding."""

    class EncodedStream:
        def __init__(self, enc: str) -> None:
            self.encoding = enc

        def write(self, s: str) -> int:
            return len(s)

        def flush(self) -> None:
            pass

    stream = AnsiStrippingStream(EncodedStream(encoding))
    assert stream.encoding == encoding
