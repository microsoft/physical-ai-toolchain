from __future__ import annotations

import os
import stat
import sys
import threading
from collections.abc import Callable, Iterator, KeysView, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import filelock
import pytest
import watchfiles
import yaml

from remoter import rmtconfig

# Tests below patch rmtconfig.filelock.FileLock, so keep a reference to the real class.
_FileLock = filelock.FileLock


def _lock_is_free(lock_path: str) -> bool:
    """Try the lock from another thread, the way a separate process would."""

    def attempt() -> bool:
        lock = _FileLock(lock_path)
        try:
            lock.acquire(blocking=False)
        except filelock.Timeout:
            return False
        lock.release()
        return True

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(attempt).result()


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda path: rmtconfig.read_config_file(path), id="read"),
        pytest.param(lambda path: rmtconfig.write_config_file(path, {"key": 1}), id="write"),
    ],
)
def test_lock_taken_over_on_release_stays_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: Callable[[str], object]
) -> None:
    path = str(tmp_path / "config.yaml")
    next_holder = _FileLock(path + ".lock")

    class HandOffLock(_FileLock):
        def __exit__(self, *exc_info: object) -> None:
            super().__exit__(*exc_info)
            # Another process takes the lock the moment it is released.
            next_holder.acquire()

    monkeypatch.setattr(rmtconfig.filelock, "FileLock", HandOffLock)
    try:
        operation(path)
        assert not _lock_is_free(path + ".lock")
    finally:
        next_holder.release()


def test_concurrent_updates_keep_both_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = str(tmp_path / "config.yaml")
    rmtconfig.write_config_file(path, {"base": 0})
    a_has_read = threading.Event()
    resume_a = threading.Event()

    class UpdateA(Mapping[str, int]):
        """Pauses while being merged, after update A has read the file and before it writes it."""

        def __init__(self, data: dict[str, int]) -> None:
            self._data = data

        def __getitem__(self, key: str) -> int:
            return self._data[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self._data)

        def __len__(self) -> int:
            return len(self._data)

        def keys(self) -> KeysView[str]:
            a_has_read.set()
            resume_a.wait(timeout=10)
            return self._data.keys()

    class ContentionAwareLock(_FileLock):
        def acquire(self, *args: object, **kwargs: object) -> filelock.AcquireReturnProxy:
            try:
                return super().acquire(blocking=False)
            except filelock.Timeout:
                # Update B is waiting for the lock update A holds, so A has to finish first.
                resume_a.set()
            return super().acquire(*args, **kwargs)

    monkeypatch.setattr(rmtconfig.filelock, "FileLock", ContentionAwareLock)
    update_a = threading.Thread(target=rmtconfig.update_config_file, args=(path, UpdateA({"a": 1})))
    update_a.start()
    assert a_has_read.wait(timeout=10)

    rmtconfig.update_config_file(path, {"b": 2})
    resume_a.set()
    update_a.join(timeout=10)

    assert not update_a.is_alive()
    assert rmtconfig.read_config_file(path) == {"base": 0, "a": 1, "b": 2}


def test_unparsable_file_raises_and_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_bytes(b"key: [unclosed\n")

    with pytest.raises(yaml.YAMLError):
        rmtconfig.read_config_file(str(path))
    with pytest.raises(yaml.YAMLError):
        rmtconfig.update_config_file(str(path), {"other": 1})
    assert path.read_bytes() == b"key: [unclosed\n"


def test_missing_file_reads_as_empty_and_update_creates_it(tmp_path: Path) -> None:
    path = str(tmp_path / "config.yaml")

    assert rmtconfig.read_config_file(path) == {}
    rmtconfig.update_config_file(path, {"key": 1})
    assert rmtconfig.read_config_file(path) == {"key": 1}


def test_failed_write_leaves_the_previous_file_in_place(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    rmtconfig.write_config_file(str(path), {"key": "value"})
    before = path.read_bytes()

    with pytest.raises(yaml.YAMLError):
        rmtconfig.write_config_file(str(path), {"key": object()})

    assert path.read_bytes() == before
    assert {entry.name for entry in tmp_path.iterdir()} <= {"config.yaml", "config.yaml.lock"}


def test_write_replaces_the_file_with_a_complete_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.yaml"
    rmtconfig.write_config_file(str(path), {"version": 1})
    replaced: list[str] = []
    real_replace = os.replace

    def checked_replace(src: str, dst: str) -> None:
        # Until the rename, readers still see the old file, and the new one is already complete.
        assert Path(dst) == path
        assert Path(src).parent == path.parent
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == {"version": 1}
        assert yaml.safe_load(Path(src).read_text(encoding="utf-8")) == {"version": 2}
        replaced.append(src)
        real_replace(src, dst)

    monkeypatch.setattr(rmtconfig.os, "replace", checked_replace)
    rmtconfig.write_config_file(str(path), {"version": 2})

    assert len(replaced) == 1
    assert rmtconfig.read_config_file(str(path)) == {"version": 2}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_write_keeps_the_file_permissions(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    rmtconfig.write_config_file(str(path), {"version": 1})
    path.chmod(0o600)

    rmtconfig.update_config_file(str(path), {"version": 2})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("order", [("deleted", "added"), ("added", "deleted")], ids=["deleted-first", "added-first"])
def test_watcher_reloads_a_file_replaced_by_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, order: tuple[str, str]
) -> None:
    # watchfiles on Windows reports os.replace as the file being deleted and added in one batch.
    path = str(tmp_path / "config.yaml")
    rmtconfig.write_config_file(path, {"version": 1})

    def replace_events(watched: str) -> Iterator[list[tuple[watchfiles.Change, str]]]:
        rmtconfig.write_config_file(watched, {"version": 2})
        yield [(watchfiles.Change[name], watched) for name in order]

    monkeypatch.setattr(rmtconfig.watchfiles, "watch", replace_events)
    seen: list[dict] = []
    config = rmtconfig.Config(path, seen.append)

    assert config.stopevent.wait(timeout=10)
    assert seen == [{"version": 1}, {"version": 2}]
    assert config.get_config() == {"version": 2}
