"""Tests for data-management/tools/lerobot_datasets.py."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import tempfile
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import lerobot_datasets as ld
import pytest

_CANARY = b"private-host-file-canary"


def _make_dataset(
    root: Path,
    *,
    episodes: int = 1,
    features: Sequence[str] = ("observation.state", "action"),
    labels: dict[str, list[str]] | None = None,
) -> Path:
    (root / "meta").mkdir(parents=True)
    info = {
        "codebase_version": "v3.0",
        "total_episodes": episodes,
        "total_frames": episodes * 10,
        "fps": 15,
        "features": {name: {"dtype": "float32"} for name in features}
        | {"observation.images.wrist": {"dtype": "video"}},
    }
    (root / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    if labels is not None:
        (root / "meta" / "episode_labels.json").write_text(json.dumps({"episodes": labels}), encoding="utf-8")
    return root


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


class _FakeEditDataset:
    """Stands in for lerobot-edit-dataset by copying trees the way each operation would produce them."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> None:
        command = list(command)
        self.commands.append(command)
        operation = command[command.index("--operation.type") + 1]
        if operation in {"remove_feature", "merge"}:
            new_root = Path(command[command.index("--new_root") + 1])
            source = (
                Path(command[command.index("--root") + 1])
                if operation == "remove_feature"
                else Path(json.loads(command[command.index("--operation.roots") + 1])[0])
            )
            shutil.copytree(source, new_root)


@pytest.fixture
def datasets_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data-root" / "datasets"
    path.mkdir(parents=True)
    return path


class TestDatasetPath:
    @pytest.mark.parametrize("name", ["so101-learner", "so101/pick.v2", "ur10e_gear"])
    def test_accepts_relative_names(self, datasets_dir: Path, name: str) -> None:
        assert ld.dataset_path(datasets_dir, name) == (datasets_dir / name).resolve()

    @pytest.mark.parametrize("name", ["", "../outside", "a/../b", "./a", "/abs", "a//b", "spaces here", "a/."])
    def test_rejects_unsafe_names(self, datasets_dir: Path, name: str) -> None:
        with pytest.raises(ld.DatasetError, match="Invalid dataset name"):
            ld.dataset_path(datasets_dir, name)

    def test_rejects_symlink_escape(self, datasets_dir: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (datasets_dir / "link").symlink_to(outside)
        with pytest.raises(ld.DatasetError, match="resolves outside"):
            ld.dataset_path(datasets_dir, "link")


class TestFindSource:
    def test_dataset_root(self, tmp_path: Path) -> None:
        root = _make_dataset(tmp_path / "ds")
        assert ld.find_source(root) == ld.SourceDataset(root=root, sidecar_dir=root)

    def test_capture_directory_with_lerobot_subdir(self, tmp_path: Path) -> None:
        capture = tmp_path / "capture"
        root = _make_dataset(capture / "lerobot")
        assert ld.find_source(capture) == ld.SourceDataset(root=root, sidecar_dir=capture)

    def test_missing_dataset(self, tmp_path: Path) -> None:
        with pytest.raises(ld.DatasetError, match="No LeRobot dataset"):
            ld.find_source(tmp_path)


class TestSelectEpisodes:
    def test_passing_export_validation_keeps_all(self, tmp_path: Path) -> None:
        root = _make_dataset(tmp_path / "ds", episodes=2)
        _write_json(root / ld.EXPORT_VALIDATION_FILE, {"status": "pass", "failed_groups": []})
        assert ld.select_episodes(ld.find_source(root)).episodes == (0, 1)

    def test_failed_export_validation_excludes_source(self, tmp_path: Path) -> None:
        root = _make_dataset(tmp_path / "ds")
        _write_json(root / ld.EXPORT_VALIDATION_FILE, {"status": "fail", "failed_groups": ["export"]})
        selection = ld.select_episodes(ld.find_source(root))
        assert not selection.selected
        assert "failed: export" in selection.excluded_reason

    def test_ledger_keeps_passed_episodes(self, tmp_path: Path) -> None:
        capture = tmp_path / "capture"
        _make_dataset(capture / "lerobot", episodes=3)
        _write_json(
            capture / ld.LEDGER_FILE,
            {
                "episodes": [
                    {"index": 0, "status": "passed"},
                    {"index": 1, "status": "failed"},
                    {"index": 2, "status": "passed"},
                ]
            },
        )
        assert ld.select_episodes(ld.find_source(capture)).episodes == (0, 2)

    def test_labels_keep_only_successes(self, tmp_path: Path) -> None:
        root = _make_dataset(tmp_path / "ds", episodes=3, labels={"0": ["SUCCESS"], "1": ["FAILURE"], "2": ["PARTIAL"]})
        _write_json(root / ld.EXPORT_VALIDATION_FILE, {"status": "pass"})
        assert ld.select_episodes(ld.find_source(root)).episodes == (0,)

    def test_unvalidated_source_needs_opt_in(self, tmp_path: Path) -> None:
        source = ld.find_source(_make_dataset(tmp_path / "ds", episodes=2))
        assert not ld.select_episodes(source).selected
        assert ld.select_episodes(source, include_unvalidated=True).episodes == (0, 1)


class TestCommands:
    def test_remove_feature(self, tmp_path: Path) -> None:
        command = ld.remove_feature_command(
            ["lerobot-edit-dataset"],
            root=tmp_path / "src",
            new_root=tmp_path / "out",
            repo_id="local/src",
            new_repo_id="local/out",
            features=["observation.task_phase", "observation.assistance"],
        )
        assert command[command.index("--operation.type") + 1] == "remove_feature"
        assert json.loads(command[command.index("--operation.feature_names") + 1]) == [
            "observation.task_phase",
            "observation.assistance",
        ]
        assert command[command.index("--root") + 1] == str(tmp_path / "src")
        assert command[command.index("--new_root") + 1] == str(tmp_path / "out")

    def test_delete_episodes(self, tmp_path: Path) -> None:
        command = ld.delete_episodes_command(["edit"], root=tmp_path, repo_id="local/x", episodes=[1, 3])
        assert command[command.index("--operation.type") + 1] == "delete_episodes"
        assert json.loads(command[command.index("--operation.episode_indices") + 1]) == [1, 3]

    def test_merge(self, tmp_path: Path) -> None:
        command = ld.merge_command(
            ["edit"],
            roots=[tmp_path / "a", tmp_path / "b"],
            repo_ids=["local/a", "local/b"],
            new_root=tmp_path / "m",
            new_repo_id="local/m",
        )
        assert json.loads(command[command.index("--operation.roots") + 1]) == [str(tmp_path / "a"), str(tmp_path / "b")]
        assert json.loads(command[command.index("--operation.repo_ids") + 1]) == ["local/a", "local/b"]


class TestDerive:
    def _sources(self, tmp_path: Path) -> list[Path]:
        passing = _make_dataset(
            tmp_path / "src" / "pass", features=("observation.state", "action", "observation.task_phase")
        )
        _write_json(passing / ld.EXPORT_VALIDATION_FILE, {"status": "pass"})
        failing = _make_dataset(tmp_path / "src" / "fail")
        _write_json(failing / ld.EXPORT_VALIDATION_FILE, {"status": "fail", "failed_groups": ["export"]})
        capture = tmp_path / "src" / "capture"
        _make_dataset(
            capture / "lerobot", episodes=2, features=("observation.state", "action", "observation.task_phase")
        )
        _write_json(
            capture / ld.LEDGER_FILE, {"episodes": [{"index": 0, "status": "passed"}, {"index": 1, "status": "failed"}]}
        )
        return [passing, failing, capture]

    def test_merges_passing_sources_without_dropped_features(self, datasets_dir: Path, tmp_path: Path) -> None:
        fake = _FakeEditDataset()
        logs: list[str] = []
        target = ld.derive(
            datasets_dir,
            "learner",
            self._sources(tmp_path),
            drop_features=["observation.task_phase", "observation.tool_pose"],
            run=fake,
            log=logs.append,
        )

        assert target == datasets_dir.resolve() / "learner"
        assert ld.is_dataset(target)
        operations = [command[command.index("--operation.type") + 1] for command in fake.commands]
        assert operations == ["remove_feature", "remove_feature", "delete_episodes", "merge"]
        removed = json.loads(fake.commands[0][fake.commands[0].index("--operation.feature_names") + 1])
        assert removed == ["observation.task_phase"]
        deleted = json.loads(fake.commands[2][fake.commands[2].index("--operation.episode_indices") + 1])
        assert deleted == [1]
        assert any("Skipping" in line and "export validation fail" in line for line in logs)
        assert not (datasets_dir.parent / ld.STAGING_DIR_NAME).exists() or not any(
            (datasets_dir.parent / ld.STAGING_DIR_NAME).iterdir()
        )

    def test_single_source_without_dropped_features_copies(self, datasets_dir: Path, tmp_path: Path) -> None:
        source = _make_dataset(tmp_path / "src" / "only")
        _write_json(source / ld.EXPORT_VALIDATION_FILE, {"status": "pass"})
        fake = _FakeEditDataset()

        target = ld.derive(datasets_dir, "copy", [source], run=fake, log=lambda _: None)

        assert fake.commands == []
        assert ld.is_dataset(target)
        assert not any((datasets_dir.parent / ld.STAGING_DIR_NAME).iterdir())

    def test_refuses_existing_target(self, datasets_dir: Path, tmp_path: Path) -> None:
        _make_dataset(datasets_dir / "learner")
        with pytest.raises(ld.DatasetError, match="already exists"):
            ld.derive(datasets_dir, "learner", self._sources(tmp_path), run=_FakeEditDataset(), log=lambda _: None)

    def test_requires_a_passing_source(self, datasets_dir: Path, tmp_path: Path) -> None:
        failing = _make_dataset(tmp_path / "src" / "fail")
        _write_json(failing / ld.EXPORT_VALIDATION_FILE, {"status": "fail"})
        with pytest.raises(ld.DatasetError, match="No source"):
            ld.derive(datasets_dir, "learner", [failing], run=_FakeEditDataset(), log=lambda _: None)

    def test_failed_edit_leaves_no_partial_dataset(self, datasets_dir: Path, tmp_path: Path) -> None:
        def failing_run(command: Sequence[str]) -> None:
            raise ld.subprocess.CalledProcessError(1, list(command))

        with pytest.raises(ld.subprocess.CalledProcessError):
            ld.derive(
                datasets_dir,
                "learner",
                self._sources(tmp_path),
                drop_features=["observation.task_phase"],
                run=failing_run,
                log=lambda _: None,
            )
        assert not (datasets_dir / "learner").exists()
        assert not any((datasets_dir.parent / ld.STAGING_DIR_NAME).iterdir())


class TestImportListRemove:
    def test_import_copies_dataset_root(self, datasets_dir: Path, tmp_path: Path) -> None:
        capture = tmp_path / "capture"
        _make_dataset(capture / "lerobot", episodes=2)
        target = ld.import_dataset(datasets_dir, "ur10e/nominal", capture, log=lambda _: None)
        assert ld.is_dataset(target)
        assert ld.list_datasets(datasets_dir) == [
            {"name": "ur10e/nominal", "episodes": 2, "frames": 20, "fps": 15, "cameras": 1, "size_mb": 0.0}
        ]

    def test_list_skips_hidden_directories(self, datasets_dir: Path) -> None:
        _make_dataset(datasets_dir / ".partial")
        _make_dataset(datasets_dir / "visible")
        assert [row["name"] for row in ld.list_datasets(datasets_dir)] == ["visible"]

    def test_remove_requires_confirmation(self, datasets_dir: Path) -> None:
        _make_dataset(datasets_dir / "old")
        assert ld.remove_dataset(datasets_dir, "old", confirm=lambda _: "n", log=lambda _: None) is False
        assert (datasets_dir / "old").exists()
        assert ld.remove_dataset(datasets_dir, "old", confirm=lambda _: "yes", log=lambda _: None) is True
        assert not (datasets_dir / "old").exists()

    def test_remove_refuses_non_datasets_and_escapes(self, datasets_dir: Path) -> None:
        (datasets_dir / "notes").mkdir()
        with pytest.raises(ld.DatasetError, match="No dataset named"):
            ld.remove_dataset(datasets_dir, "notes", assume_yes=True)
        with pytest.raises(ld.DatasetError, match="Invalid dataset name"):
            ld.remove_dataset(datasets_dir, "..", assume_yes=True)

    def test_cli_lists_and_reports_errors(self, datasets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _make_dataset(datasets_dir / "so101-learner")
        assert ld.main(["--datasets-dir", str(datasets_dir), "list"]) == 0
        assert "so101-learner\tepisodes=1" in capsys.readouterr().out
        assert ld.main(["--datasets-dir", str(datasets_dir), "remove", "../x", "--yes"]) == 1
        assert "Invalid dataset name" in capsys.readouterr().err


@pytest.fixture
def private_dir(tmp_path: Path) -> Path:
    """A host directory outside every source, holding a file that must never be published."""
    path = tmp_path / "private"
    path.mkdir()
    (path / "canary.txt").write_bytes(_CANARY)
    _write_json(path / "info.json", {"codebase_version": "v3.0", "total_episodes": 1, "features": {}})
    return path


@pytest.fixture
def short_tmp() -> Iterator[Path]:
    """A temporary directory with a short path, because macOS limits UNIX socket paths to about 104 bytes."""
    path = Path(tempfile.mkdtemp(prefix="ld-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _assert_nothing_published(datasets_dir: Path, name: str) -> None:
    assert not (datasets_dir / name).exists()
    staging = datasets_dir.parent / ld.STAGING_DIR_NAME
    assert not staging.exists() or not any(staging.iterdir())
    published = [path for path in datasets_dir.rglob("*") if path.is_file() and not path.is_symlink()]
    assert all(_CANARY not in path.read_bytes() for path in published)


def _passing_source(root: Path, **kwargs: object) -> Path:
    _make_dataset(root, **kwargs)
    _write_json(root / ld.EXPORT_VALIDATION_FILE, {"status": "pass"})
    return root


def _add_file_link(source: Path, private_dir: Path) -> str:
    (source / "data").mkdir()
    (source / "data" / "leak.txt").symlink_to(private_dir / "canary.txt")
    return "leak.txt"


def _add_directory_link(source: Path, private_dir: Path) -> str:
    (source / "videos").mkdir()
    (source / "videos" / "leak").symlink_to(private_dir, target_is_directory=True)
    return "leak"


def _add_internal_link(source: Path, private_dir: Path) -> str:
    (source / "meta" / "copy.json").symlink_to("info.json")
    return "copy.json"


def _add_metadata_link(source: Path, private_dir: Path) -> str:
    (source / "meta" / "info.json").unlink()
    (source / "meta" / "info.json").symlink_to(private_dir / "info.json")
    return "info.json"


def _add_fifo(source: Path, private_dir: Path) -> str:
    os.mkfifo(source / "pipe")
    return "pipe"


class TestLinkRefusal:
    @pytest.mark.parametrize(
        "add_entry",
        [_add_file_link, _add_directory_link, _add_internal_link, _add_metadata_link, _add_fifo],
        ids=["file-link", "directory-link", "internal-link", "metadata-link", "fifo"],
    )
    def test_import_refuses_links_and_special_files(
        self, datasets_dir: Path, tmp_path: Path, private_dir: Path, add_entry: Callable[[Path, Path], str]
    ) -> None:
        source = _make_dataset(tmp_path / "src")
        entry = add_entry(source, private_dir)

        with pytest.raises(ld.DatasetError, match=re.escape(entry)):
            ld.import_dataset(datasets_dir, "leaky", source, log=lambda _: None)

        _assert_nothing_published(datasets_dir, "leaky")

    def test_import_refuses_a_linked_lerobot_directory(
        self, datasets_dir: Path, tmp_path: Path, private_dir: Path
    ) -> None:
        elsewhere = _make_dataset(tmp_path / "elsewhere")
        (elsewhere / "secret.txt").write_bytes(_CANARY)
        capture = tmp_path / "capture"
        capture.mkdir()
        (capture / "lerobot").symlink_to(elsewhere, target_is_directory=True)

        with pytest.raises(ld.DatasetError, match="lerobot is a symbolic link"):
            ld.import_dataset(datasets_dir, "leaky", capture, log=lambda _: None)

        _assert_nothing_published(datasets_dir, "leaky")

    def test_import_refuses_a_socket(self, datasets_dir: Path, short_tmp: Path) -> None:
        source = _make_dataset(short_tmp / "src")
        server = socket.socket(socket.AF_UNIX)
        try:
            try:
                server.bind(str(source / "sock"))
            except OSError as error:
                pytest.skip(f"Cannot bind a UNIX socket here: {error}")

            with pytest.raises(ld.DatasetError, match="sock"):
                ld.import_dataset(datasets_dir, "leaky", source, log=lambda _: None)
        finally:
            server.close()

        _assert_nothing_published(datasets_dir, "leaky")

    def test_import_refuses_an_entry_swapped_for_a_link_while_copying(
        self, datasets_dir: Path, tmp_path: Path, private_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _make_dataset(tmp_path / "src")
        frame = source / "data" / "frame.bin"
        frame.parent.mkdir()
        frame.write_bytes(b"frame")
        list_names = ld._list_names

        def swap_after_listing(directory_fd: int) -> list[str]:
            names = list_names(directory_fd)
            if "frame.bin" in names and not frame.is_symlink():
                frame.unlink()
                frame.symlink_to(private_dir / "canary.txt")
            return names

        monkeypatch.setattr(ld, "_list_names", swap_after_listing)

        with pytest.raises(ld.DatasetError, match=re.escape("frame.bin is a symbolic link")):
            ld.import_dataset(datasets_dir, "leaky", source, log=lambda _: None)

        _assert_nothing_published(datasets_dir, "leaky")

    def test_import_follows_a_link_the_operator_names(self, datasets_dir: Path, tmp_path: Path) -> None:
        source = _make_dataset(tmp_path / "src")
        named = tmp_path / "named"
        named.symlink_to(source, target_is_directory=True)

        target = ld.import_dataset(datasets_dir, "named", named, log=lambda _: None)

        assert ld.is_dataset(target)
        assert not any((datasets_dir.parent / ld.STAGING_DIR_NAME).iterdir())

    @pytest.mark.parametrize("drop", [[], ["observation.state"]], ids=["copy", "drop-feature"])
    @pytest.mark.parametrize(
        "add_entry",
        [_add_file_link, _add_directory_link, _add_internal_link, _add_metadata_link, _add_fifo],
        ids=["file-link", "directory-link", "internal-link", "metadata-link", "fifo"],
    )
    def test_derive_refuses_links_and_special_files(
        self,
        datasets_dir: Path,
        tmp_path: Path,
        private_dir: Path,
        add_entry: Callable[[Path, Path], str],
        drop: list[str],
    ) -> None:
        source = _passing_source(tmp_path / "src")
        entry = add_entry(source, private_dir)
        fake = _FakeEditDataset()

        with pytest.raises(ld.DatasetError, match=re.escape(entry)):
            ld.derive(datasets_dir, "leaky", [source], drop_features=drop, run=fake, log=lambda _: None)

        assert fake.commands == []
        _assert_nothing_published(datasets_dir, "leaky")

    @pytest.mark.parametrize("drop", [[], ["observation.state"]], ids=["copy", "drop-feature"])
    def test_derive_refuses_a_linked_lerobot_directory(
        self, datasets_dir: Path, tmp_path: Path, drop: list[str]
    ) -> None:
        elsewhere = _passing_source(tmp_path / "elsewhere")
        (elsewhere / "secret.txt").write_bytes(_CANARY)
        capture = tmp_path / "capture"
        capture.mkdir()
        (capture / "lerobot").symlink_to(elsewhere, target_is_directory=True)
        _write_json(capture / ld.LEDGER_FILE, {"episodes": [{"index": 0, "status": "passed"}]})

        with pytest.raises(ld.DatasetError, match="lerobot is a symbolic link"):
            ld.derive(datasets_dir, "leaky", [capture], drop_features=drop, run=_FakeEditDataset(), log=lambda _: None)

        _assert_nothing_published(datasets_dir, "leaky")

    @pytest.mark.parametrize("drop", [[], ["observation.state"]], ids=["copy", "drop-feature"])
    def test_derive_refuses_a_socket(self, datasets_dir: Path, short_tmp: Path, drop: list[str]) -> None:
        source = _passing_source(short_tmp / "src")
        server = socket.socket(socket.AF_UNIX)
        try:
            try:
                server.bind(str(source / "sock"))
            except OSError as error:
                pytest.skip(f"Cannot bind a UNIX socket here: {error}")

            with pytest.raises(ld.DatasetError, match="sock"):
                ld.derive(
                    datasets_dir, "leaky", [source], drop_features=drop, run=_FakeEditDataset(), log=lambda _: None
                )
        finally:
            server.close()

        _assert_nothing_published(datasets_dir, "leaky")

    def test_derive_refuses_a_link_in_a_source_it_would_skip(
        self, datasets_dir: Path, tmp_path: Path, private_dir: Path
    ) -> None:
        good = _passing_source(tmp_path / "src" / "good")
        failing = _make_dataset(tmp_path / "src" / "failing")
        _write_json(failing / ld.EXPORT_VALIDATION_FILE, {"status": "fail"})
        _add_directory_link(failing, private_dir)

        with pytest.raises(ld.DatasetError, match="leak is a symbolic link"):
            ld.derive(datasets_dir, "leaky", [good, failing], run=_FakeEditDataset(), log=lambda _: None)

        _assert_nothing_published(datasets_dir, "leaky")

    def test_derive_refuses_a_linked_capture_sidecar(self, datasets_dir: Path, tmp_path: Path) -> None:
        capture = tmp_path / "capture"
        _make_dataset(capture / "lerobot")
        outside = tmp_path / "outside-ledger.json"
        _write_json(outside, {"episodes": [{"index": 0, "status": "passed"}]})
        (capture / ld.LEDGER_FILE).symlink_to(outside)

        with pytest.raises(ld.DatasetError, match=re.escape("ledger.json is a symbolic link")):
            ld.derive(datasets_dir, "leaky", [capture], run=_FakeEditDataset(), log=lambda _: None)

        _assert_nothing_published(datasets_dir, "leaky")

    def test_derive_edits_and_selects_from_staged_copies(
        self, datasets_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _passing_source(tmp_path / "src", features=("observation.state", "action", "observation.task_phase"))
        fake = _FakeEditDataset()
        staging = (datasets_dir.parent / ld.STAGING_DIR_NAME).resolve()
        selected_roots: list[Path] = []
        select_episodes = ld.select_episodes

        def record_selection(selected: ld.SourceDataset, **kwargs: bool) -> ld.EpisodeSelection:
            selected_roots.append(selected.root)
            return select_episodes(selected, **kwargs)

        monkeypatch.setattr(ld, "select_episodes", record_selection)

        target = ld.derive(
            datasets_dir, "learner", [source], drop_features=["observation.task_phase"], run=fake, log=lambda _: None
        )

        edited_root = Path(fake.commands[0][fake.commands[0].index("--root") + 1])
        assert edited_root.is_relative_to(staging)
        assert all(root.is_relative_to(staging) for root in selected_roots)
        assert ld.is_dataset(target)
        assert not any(staging.iterdir())

    def test_derive_refuses_a_sidecar_that_is_not_a_regular_file(self, datasets_dir: Path, tmp_path: Path) -> None:
        capture = tmp_path / "capture"
        _make_dataset(capture / "lerobot")
        os.mkfifo(capture / ld.EXPORT_VALIDATION_FILE)

        with pytest.raises(ld.DatasetError, match=re.escape("export-validation.json is not a regular file")):
            ld.derive(datasets_dir, "leaky", [capture], run=_FakeEditDataset(), log=lambda _: None)

        _assert_nothing_published(datasets_dir, "leaky")

    def test_import_refuses_a_source_that_loses_its_metadata_while_copying(
        self, datasets_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _make_dataset(tmp_path / "src")
        monkeypatch.setattr(ld, "copy_dataset_tree", lambda _source, target: target.mkdir())

        with pytest.raises(ld.DatasetError, match=re.escape("lost meta/info.json while it was copied")):
            ld.import_dataset(datasets_dir, "partial", source, log=lambda _: None)

        _assert_nothing_published(datasets_dir, "partial")
