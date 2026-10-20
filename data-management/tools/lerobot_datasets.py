"""Manage LeRobot datasets in a local data root: list, import, derive learner datasets, and remove.

Datasets live one per directory under the datasets directory, each a complete LeRobot v3 tree with
``meta/info.json``. Derivation selects source episodes from their validation and label sidecars, drops
features from each selected source, and merges the results with ``lerobot-edit-dataset``, staging the work
outside the datasets directory so a partial dataset never appears to readers such as the dataviewer.

Import and derivation copy only regular files and directories. A source that holds a symbolic link or a special
file is refused, so a dataset cannot publish files from elsewhere on the host into the datasets directory.

Only ``derive`` needs LeRobot; the other commands use the standard library.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

INFO_FILE = Path("meta") / "info.json"
LABELS_FILE = Path("meta") / "episode_labels.json"
EXPORT_VALIDATION_FILE = "export-validation.json"
LEDGER_FILE = "ledger.json"
SUCCESS_LABEL = "SUCCESS"
STAGING_DIR_NAME = ".staging"
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
# The open never follows a link, and O_NONBLOCK keeps a FIFO from blocking it.
_NO_FOLLOW_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_COPY_CHUNK_BYTES = 1 << 20

Runner = Callable[[Sequence[str]], None]


class DatasetError(RuntimeError):
    """Raised when a dataset operation cannot proceed safely."""


@dataclass(frozen=True)
class SourceDataset:
    """A LeRobot dataset root and the directory that holds its capture sidecars."""

    root: Path
    sidecar_dir: Path


@dataclass(frozen=True)
class EpisodeSelection:
    """Episodes kept from one source, with the reason when the whole source is excluded."""

    source: SourceDataset
    episodes: tuple[int, ...]
    total_episodes: int
    excluded_reason: str = ""

    @property
    def selected(self) -> bool:
        return bool(self.episodes)


def is_dataset(path: Path) -> bool:
    """Report whether a directory holds a LeRobot dataset."""
    return (path / INFO_FILE).is_file()


def read_info(root: Path) -> dict:
    return json.loads((root / INFO_FILE).read_text(encoding="utf-8"))


def dataset_path(datasets_dir: Path, name: str) -> Path:
    """Return the directory for dataset ``name``, refusing names that escape the datasets directory.

    Raises:
        DatasetError: When the name is not a plain relative path or resolves outside the directory.
    """
    if not _NAME_PATTERN.fullmatch(name) or any(part in {".", ".."} for part in name.split("/")):
        raise DatasetError(f"Invalid dataset name '{name}': use a relative path of letters, digits, '.', '_', and '-'")
    base = datasets_dir.resolve()
    target = (datasets_dir / name).resolve()
    if target == base or not target.is_relative_to(base):
        raise DatasetError(f"Dataset '{name}' resolves outside {base}")
    return target


def find_source(path: Path) -> SourceDataset:
    """Locate the LeRobot dataset in a source directory.

    Accepts a dataset root, or a capture directory whose ``lerobot/`` subdirectory is the dataset and whose
    own files are the capture sidecars. The path itself is resolved, so a link the operator names still works;
    links inside the source are refused when it is copied.
    """
    path = path.resolve()
    if is_dataset(path):
        return SourceDataset(root=path, sidecar_dir=path)
    if is_dataset(path / "lerobot"):
        return SourceDataset(root=path / "lerobot", sidecar_dir=path)
    raise DatasetError(f"No LeRobot dataset at {path} (expected meta/info.json or lerobot/meta/info.json)")


def _open_no_follow(path: Path | str, *, dir_fd: int | None = None, shown_as: Path | None = None) -> int:
    """Open ``path`` read-only without following a link at its last component.

    Raises:
        DatasetError: When ``path`` is a symbolic link or cannot be opened.
    """
    shown = shown_as or Path(path)
    try:
        return os.open(path, _NO_FOLLOW_FLAGS, dir_fd=dir_fd)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise DatasetError(
                f"{shown} is a symbolic link; a dataset source must hold only regular files and directories. "
                "Replace each link with the file it points to after checking that the file belongs to the dataset"
            ) from error
        raise DatasetError(f"Cannot open {shown}: {error.strerror}") from error


def _list_names(directory_fd: int) -> list[str]:
    """Return the entry names of an open directory in a stable order."""
    with os.scandir(directory_fd) as entries:
        return sorted(entry.name for entry in entries)


def _copy_entry(parent_fd: int | None, name: str, source: Path, target: Path) -> None:
    descriptor = _open_no_follow(name, dir_fd=parent_fd, shown_as=source)
    try:
        mode = os.fstat(descriptor).st_mode
        if stat.S_ISDIR(mode):
            target.mkdir()
            for child in _list_names(descriptor):
                _copy_entry(descriptor, child, source / child, target / child)
        elif stat.S_ISREG(mode):
            with open(descriptor, "rb", closefd=False) as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer, _COPY_CHUNK_BYTES)
        else:
            raise DatasetError(f"{source} is not a regular file or directory; a dataset source must hold only those")
    finally:
        os.close(descriptor)


def copy_dataset_tree(source: Path, target: Path) -> None:
    """Copy the directory ``source`` to the new directory ``target`` without following links.

    Each entry is opened relative to its parent directory without following links and classified from the open
    descriptor, so a symbolic link or special file anywhere in the tree, including one swapped in while the copy
    runs, stops the copy instead of copying what it points to. Needs POSIX ``O_NOFOLLOW`` and descriptor-based
    directory listing, which Linux and macOS provide.

    Raises:
        DatasetError: When the tree holds a symbolic link or anything other than directories and regular files.
    """
    _copy_entry(None, os.fspath(source), source, target)


def _read_json_no_follow(path: Path) -> dict | None:
    """Read a JSON sidecar without following a link, or return ``None`` when it does not exist.

    Raises:
        DatasetError: When the sidecar is a symbolic link or not a regular file.
    """
    if not (path.is_symlink() or path.exists()):
        return None
    descriptor = _open_no_follow(path)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise DatasetError(f"{path} is not a regular file")
        with open(descriptor, "rb", closefd=False) as reader:
            return json.loads(reader.read().decode("utf-8"))
    finally:
        os.close(descriptor)


def select_episodes(source: SourceDataset, *, include_unvalidated: bool = False) -> EpisodeSelection:
    """Keep the episodes a source's sidecars show as valid and successful.

    * ``export-validation.json`` must report ``status: pass`` for the whole dataset.
    * ``ledger.json`` keeps only episodes whose ``status`` is ``passed``.
    * ``meta/episode_labels.json`` keeps only episodes labeled ``SUCCESS``.

    A source without a validation sidecar is excluded unless ``include_unvalidated`` is set. Sidecars are read
    without following links.
    """
    total = int(read_info(source.root).get("total_episodes", 0))
    kept = set(range(total))

    def excluded(reason: str) -> EpisodeSelection:
        return EpisodeSelection(source=source, episodes=(), total_episodes=total, excluded_reason=reason)

    validation = _read_json_no_follow(source.sidecar_dir / EXPORT_VALIDATION_FILE)
    ledger = _read_json_no_follow(source.sidecar_dir / LEDGER_FILE)
    if validation is not None and validation.get("status") != "pass":
        failed = ", ".join(validation.get("failed_groups") or []) or "unknown"
        return excluded(f"export validation {validation.get('status', 'missing')} (failed: {failed})")
    if ledger is not None:
        kept &= {int(episode["index"]) for episode in ledger.get("episodes", []) if episode.get("status") == "passed"}
    if validation is None and ledger is None and not include_unvalidated:
        return excluded(f"no {EXPORT_VALIDATION_FILE} or {LEDGER_FILE}")

    labels = _read_json_no_follow(source.root / LABELS_FILE)
    if labels is not None:
        kept &= {int(index) for index, names in labels.get("episodes", {}).items() if SUCCESS_LABEL in names}

    if not kept:
        return excluded("no passing, successful episodes")
    return EpisodeSelection(source=source, episodes=tuple(sorted(kept)), total_episodes=total)


def remove_feature_command(
    executable: Sequence[str], *, root: Path, new_root: Path, repo_id: str, new_repo_id: str, features: Sequence[str]
) -> list[str]:
    return [
        *executable,
        "--repo_id",
        repo_id,
        "--root",
        str(root),
        "--new_repo_id",
        new_repo_id,
        "--new_root",
        str(new_root),
        "--operation.type",
        "remove_feature",
        "--operation.feature_names",
        json.dumps(list(features)),
    ]


def delete_episodes_command(
    executable: Sequence[str], *, root: Path, repo_id: str, episodes: Sequence[int]
) -> list[str]:
    return [
        *executable,
        "--repo_id",
        repo_id,
        "--root",
        str(root),
        "--operation.type",
        "delete_episodes",
        "--operation.episode_indices",
        json.dumps(list(episodes)),
    ]


def merge_command(
    executable: Sequence[str], *, roots: Sequence[Path], repo_ids: Sequence[str], new_root: Path, new_repo_id: str
) -> list[str]:
    return [
        *executable,
        "--new_repo_id",
        new_repo_id,
        "--new_root",
        str(new_root),
        "--operation.type",
        "merge",
        "--operation.repo_ids",
        json.dumps(list(repo_ids)),
        "--operation.roots",
        json.dumps([str(root) for root in roots]),
    ]


def _run_checked(command: Sequence[str]) -> None:
    subprocess.run(list(command), check=True)


def _new_staging_dir(datasets_dir: Path, name: str) -> Path:
    """Create a unique working folder for ``name`` beside the datasets directory.

    Staging shares the datasets directory's filesystem, so a finished dataset moves in with one rename and
    readers never see a partial tree.
    """
    staging = datasets_dir.resolve().parent / STAGING_DIR_NAME / f"{name.replace('/', '-')}-{uuid.uuid4().hex[:8]}"
    staging.mkdir(parents=True)
    return staging


def derive(
    datasets_dir: Path,
    name: str,
    sources: Sequence[Path],
    *,
    drop_features: Sequence[str] = (),
    include_unvalidated: bool = False,
    executable: Sequence[str] = ("lerobot-edit-dataset",),
    run: Runner = _run_checked,
    log: Callable[[str], None] = print,
) -> Path:
    """Build dataset ``name`` from the passing, successful episodes of ``sources`` without ``drop_features``.

    Each source is copied into staging first, refusing links and special files, and its episodes and features
    are selected from that copy, so a source that changes during derivation cannot add unvalidated episodes or
    keep a dropped feature. ``lerobot-edit-dataset`` reads only staged copies. Sources are processed one at a
    time, and dropping features needs room for one more copy of the source being processed.

    Returns:
        The new dataset directory.

    Raises:
        DatasetError: When the target exists, a source holds a link or special file, or no source contributes
            an episode.
    """
    target = dataset_path(datasets_dir, name)
    if target.exists():
        raise DatasetError(f"Dataset '{name}' already exists at {target}; remove it first")
    found = [find_source(path) for path in sources]

    staging = _new_staging_dir(datasets_dir, name)
    try:
        parts: list[Path] = []
        part_ids: list[str] = []
        for index, source in enumerate(found):
            snapshot = staging / f"source-{index}"
            copy_dataset_tree(source.root, snapshot)
            sidecar_dir = snapshot if source.sidecar_dir == source.root else source.sidecar_dir
            selection = select_episodes(
                SourceDataset(root=snapshot, sidecar_dir=sidecar_dir), include_unvalidated=include_unvalidated
            )
            if not selection.selected:
                log(f"Skipping {source.root}: {selection.excluded_reason}")
                shutil.rmtree(snapshot)
                continue
            log(f"Using {source.root}: episodes {list(selection.episodes)} of {selection.total_episodes}")

            part, part_id = staging / f"part-{len(parts)}", f"local/part-{len(parts)}"
            features = read_info(snapshot).get("features", {})
            present = [feature for feature in drop_features if feature in features]
            if present:
                run(
                    remove_feature_command(
                        executable,
                        root=snapshot,
                        new_root=part,
                        repo_id=f"local/source-{index}",
                        new_repo_id=part_id,
                        features=present,
                    )
                )
                shutil.rmtree(snapshot)
            else:
                snapshot.rename(part)
            dropped = sorted(set(range(selection.total_episodes)) - set(selection.episodes))
            if dropped:
                run(delete_episodes_command(executable, root=part, repo_id=part_id, episodes=dropped))
            parts.append(part)
            part_ids.append(part_id)
        if not parts:
            raise DatasetError("No source has a passing, successful episode")

        result = parts[0]
        if len(parts) > 1:
            result = staging / "merged"
            run(merge_command(executable, roots=parts, repo_ids=part_ids, new_root=result, new_repo_id=f"local/{name}"))
        if not is_dataset(result):
            raise DatasetError(f"lerobot-edit-dataset did not produce a dataset at {result}")
        target.parent.mkdir(parents=True, exist_ok=True)
        result.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log(f"Created {target}")
    return target


def import_dataset(datasets_dir: Path, name: str, source: Path, *, log: Callable[[str], None] = print) -> Path:
    """Copy a LeRobot dataset into the datasets directory as ``name``.

    The copy refuses links and special files, and it is staged and moved into place only when complete.

    Raises:
        DatasetError: When the target exists, the source is not a dataset, or it holds a link or special file.
    """
    target = dataset_path(datasets_dir, name)
    if target.exists():
        raise DatasetError(f"Dataset '{name}' already exists at {target}; remove it first")
    root = find_source(source).root
    staging = _new_staging_dir(datasets_dir, name)
    try:
        staged = staging / "dataset"
        copy_dataset_tree(root, staged)
        if not is_dataset(staged):
            raise DatasetError(f"{root} lost {INFO_FILE} while it was copied")
        target.parent.mkdir(parents=True, exist_ok=True)
        staged.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log(f"Imported {root} as {target}")
    return target


def list_datasets(datasets_dir: Path) -> list[dict[str, object]]:
    """Describe every dataset under the datasets directory, nested names included."""
    rows: list[dict[str, object]] = []
    if not datasets_dir.is_dir():
        return rows
    for info_path in sorted(datasets_dir.rglob(str(INFO_FILE))):
        root = info_path.parent.parent
        if any(part.startswith(".") for part in root.relative_to(datasets_dir).parts):
            continue
        info = read_info(root)
        cameras = [key for key, value in info.get("features", {}).items() if value.get("dtype") in ("video", "image")]
        size = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        rows.append(
            {
                "name": root.relative_to(datasets_dir).as_posix(),
                "episodes": info.get("total_episodes", 0),
                "frames": info.get("total_frames", 0),
                "fps": info.get("fps", 0),
                "cameras": len(cameras),
                "size_mb": round(size / 1_000_000, 1),
            }
        )
    return rows


def remove_dataset(
    datasets_dir: Path,
    name: str,
    *,
    assume_yes: bool = False,
    confirm: Callable[[str], str] = input,
    log: Callable[[str], None] = print,
) -> bool:
    """Delete dataset ``name`` after confirmation.

    Returns:
        True when the dataset was removed, False when the confirmation was declined.

    Raises:
        DatasetError: When the name escapes the datasets directory or is not a dataset.
    """
    target = dataset_path(datasets_dir, name)
    if not is_dataset(target):
        raise DatasetError(f"No dataset named '{name}' at {target}")
    if not assume_yes and confirm(f"Remove {target}? [y/N] ").strip().lower() not in {"y", "yes"}:
        log("Left unchanged")
        return False
    shutil.rmtree(target)
    log(f"Removed {target}")
    return True


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--datasets-dir", type=Path, required=True, help="Directory that holds one dataset per folder")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="List datasets")

    import_parser = commands.add_parser("import", help="Copy a LeRobot dataset in")
    import_parser.add_argument("source", type=Path, help="Dataset root, or a capture directory with lerobot/")
    import_parser.add_argument("name", help="Dataset name under the datasets directory")

    derive_parser = commands.add_parser("derive", help="Merge passing episodes and drop features into a new dataset")
    derive_parser.add_argument("name", help="New dataset name under the datasets directory")
    derive_parser.add_argument(
        "--source", type=Path, action="append", required=True, help="Source dataset (repeatable)"
    )
    derive_parser.add_argument("--drop-feature", action="append", default=[], help="Feature to remove (repeatable)")
    derive_parser.add_argument(
        "--include-unvalidated", action="store_true", help="Use sources that have no validation sidecar"
    )

    remove_parser = commands.add_parser("remove", help="Delete a dataset after confirmation")
    remove_parser.add_argument("name", help="Dataset name under the datasets directory")
    remove_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    datasets_dir: Path = args.datasets_dir
    try:
        if args.command == "list":
            rows = list_datasets(datasets_dir)
            if not rows:
                print(f"No datasets in {datasets_dir}")
            for row in rows:
                print(
                    f"{row['name']}\tepisodes={row['episodes']}\tframes={row['frames']}\tfps={row['fps']}"
                    f"\tcameras={row['cameras']}\tsize={row['size_mb']}MB"
                )
        elif args.command == "import":
            import_dataset(datasets_dir, args.name, args.source)
        elif args.command == "derive":
            if shutil.which("lerobot-edit-dataset") is None:
                raise DatasetError("lerobot-edit-dataset is not on PATH; run derive from the LeRobot environment")
            derive(
                datasets_dir,
                args.name,
                args.source,
                drop_features=args.drop_feature,
                include_unvalidated=args.include_unvalidated,
            )
        elif args.command == "remove":
            remove_dataset(datasets_dir, args.name, assume_yes=args.yes)
    except (DatasetError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
