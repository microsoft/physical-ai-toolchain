#!/usr/bin/env python3
"""Fail if a production HuggingFace download omits an explicit ``revision``.

Bare ``repo_id`` calls resolve a mutable HEAD, so an upstream account or org
compromise can silently ship new weights or a poisoned dataset into evaluation
and deployed inference. This guard parses the AST of production ``.py`` and ``.sh``
files and of inline Python heredocs (``python3 << 'DELIM'``, the piped
``cat <<'DELIM' | python3`` form, and a ``cat > dl.py <<'DELIM'`` body written to a
``.py`` file) and ``python -c "..."`` one-liners embedded in
workflow YAML and shell scripts — the OSMO download paths live there, invisible to
a ``.py``-only walk — and rejects any
``from_pretrained`` / ``snapshot_download`` / ``hf_hub_download`` call that has no
explicit ``revision`` keyword. Calls reached through an import alias
(``import snapshot_download as dl``) or a simple rebinding (``grab = snapshot_download``)
are resolved and checked too. A literal ``revision=None`` or an empty string is rejected as well: it is
provably unpinned. A ``**kwargs`` splat is accepted, since it cannot be resolved
statically.

Test files and vendored code are excluded — the pin is a production requirement,
and tests deliberately exercise the unpinned/local-path forms. Multi-line calls
are handled by the AST, so this is robust where a line-oriented grep is not.

Usage:
    python scripts/security/check_hf_pins.py [ROOT ...]
"""

from __future__ import annotations

import ast
import re
import shlex
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

_GUARDED_FUNCTIONS = frozenset({"from_pretrained", "snapshot_download", "hf_hub_download"})

_DEFAULT_ROOTS = (
    "evaluation",
    "training",
    "fleet-deployment",
    "data-management/viewer/backend",
)

_EXCLUDED_PARTS = frozenset({"tests", "external", "node_modules", ".venv", ".git"})

# Heredoc opener ``<< 'DELIM'`` / ``<<-"DELIM"`` / ``<<DELIM``, matched independently of
# the command that consumes the body so both ``python3 <<'DELIM'`` and the piped
# ``cat <<'DELIM' | python3`` forms are covered. ``_python_heredocs`` scans every
# quoted-delimiter heredoc body as a candidate Python snippet regardless of the opener.
_HEREDOC_OPENER_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?(?:\s|$)")
# ``python -c`` interpreter basename, allowing version suffixes (``python3.12``), matched
# in full so the inline-``-c`` scanner sees the same interpreters as the heredoc opener.
_PYTHON_INTERPRETER_RE = re.compile(r"python(?:\d+(?:\.\d+)?)?")


class ScanError(RuntimeError):
    """Raised when a source file cannot be scanned safely."""


def _is_excluded(path: Path) -> bool:
    """Skip test suites, vendored trees, and test-named modules."""
    if any(part in _EXCLUDED_PARTS for part in path.parts):
        return True
    return path.name.startswith("test_")


def _callee_name(node: ast.Call) -> str | None:
    """Return the attribute/name of a call's callee, or None."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _assign_target_names(node: ast.Assign | ast.AnnAssign) -> Iterator[str]:
    """Yield the simple ``Name`` targets bound by an assignment."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Name):
            yield target.id


def _value_binds_guarded(value: ast.expr | None, bound: set[str]) -> bool:
    """True if ``value`` references a guarded function directly, by attribute, or via an alias."""
    if isinstance(value, ast.Attribute):
        return value.attr in _GUARDED_FUNCTIONS
    if isinstance(value, ast.Name):
        return value.id in _GUARDED_FUNCTIONS or value.id in bound
    return False


def _guarded_bound_names(tree: ast.Module) -> set[str]:
    """Collect local names bound to a guarded function via import alias or assignment.

    ``from hub import snapshot_download as dl`` and ``grab = snapshot_download`` both make an
    unpinned ``dl(...)`` / ``grab(...)`` resolve the guarded function, so calls to those names
    must be checked too. Chained rebindings are resolved to a fixpoint.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _GUARDED_FUNCTIONS:
                    bound.add(alias.asname or alias.name)

    assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))]
    changed = True
    while changed:
        changed = False
        for node in assignments:
            if not _value_binds_guarded(node.value, bound):
                continue
            for name in _assign_target_names(node):
                if name not in bound:
                    bound.add(name)
                    changed = True
    return bound


def _is_guarded_call(node: ast.Call, bound: set[str]) -> bool:
    """True if the call targets a guarded function directly, by attribute, or via an alias."""
    name = _callee_name(node)
    if name is None:
        return False
    if name in _GUARDED_FUNCTIONS:
        return True
    return isinstance(node.func, ast.Name) and name in bound


_SHA40_RE = re.compile(r"[0-9a-fA-F]{40}")


def _is_sha40(text: str) -> bool:
    """True if ``text`` is exactly a 40-character hexadecimal commit SHA."""
    return bool(_SHA40_RE.fullmatch(text))


def _is_pinned_revision(value: ast.expr) -> bool:
    """True if a ``revision`` argument is a valid immutable pin.

    A literal ``None``, a blank string, a non-string constant, or a fully
    constant f-string that does not fold to a 40-hex SHA all resolve to the
    mutable HEAD or an invalid ref and are treated as unpinned. Any non-constant
    expression (a variable, attribute, or interpolated f-string) is trusted. A
    constant string literal or a constant-folded f-string must match exactly a
    40-character hex commit SHA.
    """
    if isinstance(value, ast.Constant):
        if not isinstance(value.value, str):
            return False
        val = value.value.strip()
        return bool(val) and _is_sha40(val)
    if isinstance(value, ast.JoinedStr):
        if any(isinstance(part, ast.FormattedValue) for part in value.values):
            return True
        folded = "".join(
            part.value for part in value.values if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
        return _is_sha40(folded.strip())
    return True


def _has_revision(node: ast.Call) -> bool:
    """True if the call pins ``revision`` (or forwards an unverifiable ``**kwargs``).

    A ``**kwargs`` splat (keyword with ``arg is None``) cannot be resolved
    statically, so it is accepted rather than flagged as a false positive. A
    literal ``revision=None`` or an empty string is provably unpinned and is
    rejected; a non-constant ``revision`` expression (a variable or attribute)
    is accepted.
    """
    has_splat = False
    for keyword in node.keywords:
        if keyword.arg is None:
            has_splat = True
        elif keyword.arg == "revision":
            return _is_pinned_revision(keyword.value)
    return has_splat


def _violations_in_source(source: str, filename: str) -> list[tuple[int, int, str]]:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as error:
        line = error.lineno or 0
        raise ScanError(f"unable to parse Python source at line {line}") from error

    bound = _guarded_bound_names(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_guarded_call(node, bound) and not _has_revision(node):
            found.append((node.lineno, node.col_offset, _callee_name(node) or "?"))
    return found


def _python_heredocs(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(body_start_lineno, dedented_source)`` for each Python heredoc.

    OSMO workflow YAML and shell entry scripts embed their real download code as
    ``python3 << 'DELIM'`` heredocs (or the piped ``cat <<'DELIM' | python3`` form, or a
    ``cat > dl.py <<'DELIM'`` body written to a ``.py`` file that a later ``python dl.py``
    runs); a quoted delimiter means the body is literal Python. Line numbers are 1-based
    against the source file so AST positions map back correctly. We scan EVERY quoted
    delimiter heredoc body as a candidate Python snippet regardless of the opener command.
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        opener = lines[i].rstrip()

        # Join backslash continued opener lines
        while opener.endswith("\\") and i + 1 < len(lines):
            i += 1
            opener = opener[:-1] + " " + lines[i].lstrip()

        match = _HEREDOC_OPENER_RE.search(opener)
        if not match:
            i += 1
            continue

        delimiter = match.group(1)
        body_start = i + 1
        end = body_start
        while end < len(lines) and lines[end].strip() != delimiter:
            end += 1

        yield body_start + 1, textwrap.dedent("\n".join(lines[body_start:end]))
        i = end + 1


def _logical_shell_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield shell logical lines with backslash continuations joined."""
    start_lineno = 1
    parts: list[str] = []

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        if not parts:
            start_lineno = lineno

        if line.endswith("\\"):
            parts.append(line[:-1])
            continue

        parts.append(line)
        yield start_lineno, " ".join(parts)
        parts = []

    if parts:
        yield start_lineno, " ".join(parts)


def _python_inline_c(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(lineno, code)`` for each ``python -c "<code>"`` invocation.

    Workflow YAML embeds guarded downloads as ``python -c`` one-liners as well as
    heredocs; the quoted payload is extracted so its AST can be scanned. Line
    numbers are 1-based against the YAML file.
    """
    interpreter_re = re.compile(rf"\b{_PYTHON_INTERPRETER_RE.pattern}\b")
    for lineno, line in _logical_shell_lines(text):
        if "python" not in line or "-c" not in line:
            continue
        try:
            argv = shlex.split(line.strip())
        except ValueError as error:
            match = interpreter_re.search(line)
            if match and "-c" in line[match.end() :].split():
                raise ScanError(f"unable to parse python -c shell at line {lineno}") from error
            if any(fn in line for fn in _GUARDED_FUNCTIONS):
                raise ScanError(f"unable to parse python -c shell at line {lineno}") from error
            continue

        for index, token in enumerate(argv):
            if not _PYTHON_INTERPRETER_RE.fullmatch(Path(token).name):
                continue
            try:
                command_index = argv.index("-c", index + 1)
            except ValueError:
                break
            if command_index + 1 >= len(argv):
                raise ScanError(f"python -c missing payload at line {lineno}")
            yield lineno, argv[command_index + 1]
            break


def _violations_in_snippet(code: str, filename: str) -> list[tuple[int, int, str]]:
    """Scan an embedded Python snippet, failing closed only when a guarded call is unverifiable.

    Heredoc bodies and ``python -c`` payloads frequently contain shell ``${VAR}``
    interpolation or plain non-Python text that does not parse; such a snippet is
    skipped. But if it mentions a guarded function, an unparseable payload is a hard
    failure rather than a silent bypass. A snippet that names no guarded function
    cannot hide a guarded call, so skipping it is safe.
    """
    try:
        return _violations_in_source(code, filename)
    except ScanError:
        if any(fn in code for fn in _GUARDED_FUNCTIONS):
            raise
        return []


def _violations_in_file(path: Path) -> list[tuple[int, int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ScanError("unable to decode as UTF-8") from error

    if path.suffix in (".yaml", ".yml", ".sh"):
        found = []
        for offset, block in _python_heredocs(text):
            for lineno, col, name in _violations_in_snippet(block, str(path)):
                found.append((offset + lineno - 1, col, name))
        for lineno, code in _python_inline_c(text):
            for _code_lineno, col, name in _violations_in_snippet(code, str(path)):
                found.append((lineno, col, name))
        return found

    return _violations_in_source(text, str(path))


def _iter_source_files(root: Path) -> Iterator[Path]:
    """Yield production ``.py`` and ``.sh`` files plus workflow YAML carrying Python heredocs."""
    yield from root.rglob("*.py")
    yield from root.rglob("*.sh")
    for suffix in ("*.yaml", "*.yml"):
        for path in root.rglob(suffix):
            if "workflows" in path.parts:
                yield path


def main(argv: list[str]) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    roots = [Path(arg) for arg in argv[1:]] or [repo_root / root for root in _DEFAULT_ROOTS]

    violations = []
    scan_errors = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(set(_iter_source_files(root))):
            if _is_excluded(path):
                continue
            rel = path.relative_to(repo_root) if path.is_relative_to(repo_root) else path
            try:
                file_violations = _violations_in_file(path)
            except ScanError as error:
                scan_errors.append(f"{rel}: {error}")
                continue
            for lineno, col, name in file_violations:
                violations.append(f"{rel}:{lineno}:{col}: {name}() call missing an explicit 'revision' argument")

    if scan_errors or violations:
        print("HuggingFace revision-pin guard failed:\n", file=sys.stderr)
        if scan_errors:
            print("  Files could not be scanned:", file=sys.stderr)
            for error in scan_errors:
                print(f"    {error}", file=sys.stderr)
            print(file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        print(
            "\nPin every from_pretrained/snapshot_download/hf_hub_download to an immutable "
            "commit SHA via a 'revision=' argument. A literal 'revision=None' or an empty string is rejected as "
            "provably unpinned; pass a resolved SHA (a variable is accepted for local-path callers).",
            file=sys.stderr,
        )
        return 1

    print("HuggingFace revision-pin guard passed: all guarded calls carry a 'revision' argument.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
