"""Download LeRobot dataset from Azure Blob Storage and prepare for training.

Handles blob download, dataset verification, stats patching for image/video
features, and video timestamp realignment.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

EXIT_SUCCESS = 0
EXIT_FAILURE = 1


def parse_blob_url(url: str) -> tuple[str, str, str]:
    """Parse blob URL into (account, container, prefix).

    Args:
        url: Blob URL like https://account.blob.core.windows.net/container/path

    Returns:
        Tuple of (storage_account, container, prefix)

    Raises:
        ValueError: If URL is malformed.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc.endswith(".blob.core.windows.net"):
        raise ValueError(f"Invalid blob URL: {url}")

    account = parsed.netloc.split(".")[0]
    path_parts = parsed.path.lstrip("/").split("/", 1)
    if not path_parts or not path_parts[0]:
        raise ValueError(f"Invalid blob URL (missing container): {url}")

    container = path_parts[0]
    prefix = path_parts[1] if len(path_parts) > 1 else ""

    return account, container, prefix


def download_dataset(
    *,
    storage_account: str,
    storage_container: str,
    blob_prefix: str,
    dataset_root: str,
    dataset_repo_id: str,
) -> Path:
    """Download dataset files from Azure Blob Storage.

    Args:
        storage_account: Azure Storage account name.
        storage_container: Blob container name.
        blob_prefix: Blob path prefix for dataset files.
        dataset_root: Local root directory for datasets.
        dataset_repo_id: Dataset repository identifier (e.g., user/dataset).

    Returns:
        Path to the downloaded dataset directory.
    """
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    dest_dir = Path(dataset_root) / dataset_repo_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    prefix = blob_prefix.rstrip("/") + "/"

    credential = DefaultAzureCredential(
        managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID"),
        authority=os.environ.get("AZURE_AUTHORITY_HOST"),
    )
    client = BlobServiceClient(
        account_url=f"https://{storage_account}.blob.core.windows.net",
        credential=credential,
    )
    container_client = client.get_container_client(storage_container)

    downloaded = 0
    dest_dir_resolved = dest_dir.resolve()
    for blob in container_client.list_blobs(name_starts_with=prefix):
        rel = blob.name[len(prefix) :]
        if ".cache/" in rel or rel.endswith(".lock") or rel.endswith(".metadata"):
            continue

        # Reject absolute paths and traversal segments to prevent writes outside dest_dir.
        rel_path = Path(rel)
        if rel_path.is_absolute() or any(part in ("..",) for part in rel_path.parts):
            print(f"Skipping unsafe blob name: {blob.name}")
            continue

        local_path = dest_dir / rel_path
        resolved = local_path.resolve()
        if not resolved.is_relative_to(dest_dir_resolved):
            print(f"Skipping blob outside dest_dir: {blob.name}")
            continue

        local_path.parent.mkdir(parents=True, exist_ok=True)

        with open(local_path, "wb") as f:
            stream = container_client.download_blob(blob.name)
            f.write(stream.readall())
        downloaded += 1

    print(f"Downloaded {downloaded} files to {dest_dir}")
    return dest_dir


def download_dataset_from_url(url: str, dataset_root: str, dataset_idx: int) -> Path:
    """Download dataset from a blob URL to a staging directory.

    Args:
        url: Blob URL (https://account.blob.core.windows.net/container/prefix)
        dataset_root: Root directory for datasets
        dataset_idx: Index for staging dir name (0, 1, 2, ...)

    Returns:
        Path to the downloaded dataset staging directory.
    """
    account, container, prefix = parse_blob_url(url)
    staging_base = Path(dataset_root) / ".staging"
    staging_dir = staging_base / f"{dataset_idx}"

    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    print(f"[{dataset_idx}] Downloading from {account}/{container}/{prefix}")
    return download_dataset(
        storage_account=account,
        storage_container=container,
        blob_prefix=prefix,
        dataset_root=str(staging_base),
        dataset_repo_id=f"{dataset_idx}",
    )


def merge_datasets(sources: list[Path], destination: Path) -> None:
    """Merge multiple datasets into ``destination`` using lerobot-edit-dataset.

    ``lerobot-edit-dataset`` calls ``os.makedirs(new_root)`` and writes the
    merged dataset directly into it: the directory must not pre-exist (any
    stale copy is removed first), and intermediate parents are created
    automatically. ``--new_repo_id`` does not influence the output path but is
    recorded inside the dataset's ``info.json``; a fixed placeholder is used.

    Args:
        sources: Dataset directories to merge.
        destination: Destination path for the merged dataset.

    Raises:
        RuntimeError: If lerobot fails or destination is missing afterwards.
    """
    if destination.exists():
        shutil.rmtree(destination)

    cmd = [
        "lerobot-edit-dataset",
        "--new_repo_id",
        "merged",
        "--operation.type",
        "merge",
        "--operation.repo_ids",
        json.dumps(list(map(str, range(len(sources))))),
        "--operation.roots",
        json.dumps([str(d.absolute()) for d in sources]),
        "--new_root",
        str(destination),
    ]

    print(f"Running: {shlex.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"Dataset merge failed with exit code {result.returncode}")
    if not destination.exists():
        raise RuntimeError(f"lerobot-edit-dataset did not create {destination}")


def verify_dataset(dataset_dir: Path) -> dict | None:
    """Verify dataset structure and return info.json contents.

    Args:
        dataset_dir: Path to dataset directory.

    Returns:
        Parsed info.json dict, or None if not found.
    """
    info_path = dataset_dir / "meta" / "info.json"
    if not info_path.exists():
        print("Warning: meta/info.json not found")
        return None

    with open(info_path) as f:
        info = json.load(f)

    print(
        f"Dataset: {info.get('robot_type', 'unknown')} robot, "
        f"{info.get('total_episodes', '?')} episodes, "
        f"{info.get('total_frames', '?')} frames"
    )
    return info


def patch_info_paths(dataset_dir: Path, info: dict) -> None:
    """Patch info.json path templates, split data, and reorganize videos for v0.3.x.

    v3.0 datasets store all episodes in a single parquet file with
    ``{chunk_index}/{file_index}`` path templates, and videos in per-file chunk
    directories (chunk-000/file-000.mp4, chunk-001/file-001.mp4, ...).

    LeRobot v0.3.x expects one parquet per episode and all videos grouped by
    ``episode_chunk = episode_index // chunks_size`` using ``{episode_chunk}``
    and ``{episode_index}`` placeholders.

    Args:
        dataset_dir: Path to dataset directory.
        info: Parsed info.json contents (modified in-place).
    """
    import pyarrow as pa
    import pyarrow.compute
    import pyarrow.parquet as pq

    info_path = dataset_dir / "meta" / "info.json"
    data_path = info.get("data_path", "")
    needs_conversion = "{file_index" in data_path or "{chunk_index" in data_path

    if not needs_conversion:
        return

    chunks_size = info.get("chunks_size", 1000)

    # --- Split monolithic parquet files into per-episode files ---
    data_dir = dataset_dir / "data"
    tables = []
    for fpath in sorted(data_dir.rglob("*.parquet")):
        tables.append(pq.read_table(fpath))

    if not tables:
        return

    combined = pa.concat_tables(tables)
    episodes = sorted(set(combined["episode_index"].to_pylist()))
    total_episodes = len(episodes)

    for ep_idx in episodes:
        ep_chunk = ep_idx // chunks_size
        chunk_dir = data_dir / f"chunk-{ep_chunk:03d}"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        ep_path = chunk_dir / f"episode_{ep_idx:06d}.parquet"
        mask = pa.compute.equal(combined["episode_index"], ep_idx)
        pq.write_table(combined.filter(mask), ep_path)

    for fpath in sorted(data_dir.rglob("file-*.parquet")):
        fpath.unlink()

    print(f"Split data into {total_episodes} per-episode parquet files")

    # --- Reorganize video files into correct chunk directories ---
    features = info.get("features", {})
    video_keys = [k for k, v in features.items() if v.get("dtype") in ("video", "image")]
    videos_dir = dataset_dir / "videos"

    for vk in video_keys:
        vk_dir = videos_dir / vk
        if not vk_dir.exists():
            continue

        # Collect all existing video files across all chunk dirs
        all_videos = sorted(vk_dir.rglob("*.mp4"))
        if not all_videos:
            continue

        # Move each video to the correct chunk dir with episode naming
        for video_file in all_videos:
            # Extract episode index from the filename (file-NNN.mp4 -> NNN)
            stem = video_file.stem
            try:
                ep_idx = int(stem.split("-")[-1])
            except ValueError:
                continue

            ep_chunk = ep_idx // chunks_size
            target_dir = vk_dir / f"chunk-{ep_chunk:03d}"
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / f"episode_{ep_idx:06d}.mp4"

            if video_file != target_path:
                shutil.move(str(video_file), str(target_path))

        # Clean up empty old chunk directories
        for d in sorted(vk_dir.iterdir()):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()

        print(f"Reorganized {len(all_videos)} video files for {vk}")

    # --- Update path templates and codebase version ---
    info["data_path"] = "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet"
    info["video_path"] = "videos/{video_key}/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.mp4"
    info["codebase_version"] = "v2.1"
    print(f"Patched data_path -> {info['data_path']}")
    print(f"Patched video_path -> {info['video_path']}")
    print(f"Patched codebase_version -> {info['codebase_version']}")

    with open(info_path, "w") as f:
        json.dump(info, f, indent=4)


def patch_image_stats(dataset_dir: Path, info: dict) -> None:
    """Patch stats.json with ImageNet normalization stats for video/image features.

    LeRobot's factory.py expects camera keys to exist in stats.json.

    Args:
        dataset_dir: Path to dataset directory.
        info: Parsed info.json contents.
    """
    stats_path = dataset_dir / "meta" / "stats.json"
    if not stats_path.exists():
        return

    with open(stats_path) as f:
        stats = json.load(f)

    features = info.get("features", {})
    updated = False
    for key, feat in features.items():
        if feat.get("dtype") in ("video", "image") and key not in stats:
            stats[key] = {
                "mean": [[[0.485]], [[0.456]], [[0.406]]],
                "std": [[[0.229]], [[0.224]], [[0.225]]],
                "min": [[[0.0]], [[0.0]], [[0.0]]],
                "max": [[[1.0]], [[1.0]], [[1.0]]],
            }
            updated = True
            print(f"Added ImageNet stats for feature: {key}")

    if updated:
        with open(stats_path, "w") as f:
            json.dump(stats, f, indent=4)


def fix_video_timestamps(dataset_dir: Path, info: dict) -> None:
    """Fix video timestamps in episode metadata and realign parquet data.

    Some datasets have cumulative from/to timestamps in episode metadata
    but per-episode timestamps in the actual video files (each starting at 0).
    This resets from_timestamp to 0 and to_timestamp to length/fps.
    Also realigns parquet frame timestamps to match the video's exact PTS grid.

    Args:
        dataset_dir: Path to dataset directory.
        info: Parsed info.json contents.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    fps = info["fps"]
    video_keys = [k for k, v in info.get("features", {}).items() if v.get("dtype") in ("video", "image")]

    if not video_keys:
        print("No video features, skipping timestamp fix")
        return

    # Fix episode metadata: reset from/to timestamps to per-episode
    episodes_dir = dataset_dir / "meta" / "episodes"
    for fpath in episodes_dir.rglob("*.parquet"):
        table = pq.read_table(fpath)
        columns = {c: table[c].to_pylist() for c in table.column_names}
        modified = False

        for vk in video_keys:
            from_col = f"videos/{vk}/from_timestamp"
            to_col = f"videos/{vk}/to_timestamp"
            if from_col not in columns or to_col not in columns:
                continue

            lengths = columns["length"]
            for i in range(len(lengths)):
                new_from = 0.0
                new_to = lengths[i] / fps
                if abs(columns[from_col][i] - new_from) > 0.01 or abs(columns[to_col][i] - new_to) > 0.01:
                    columns[from_col][i] = new_from
                    columns[to_col][i] = new_to
                    modified = True

        if modified:
            new_table = pa.table({c: columns[c] for c in table.column_names})
            pq.write_table(new_table, fpath)
            print(f"Fixed cumulative video timestamps in {fpath.name}")
        else:
            print("Video timestamps already per-episode, no fix needed")

    # Realign parquet data timestamps to the 1/fps grid
    data_dir = dataset_dir / "data"
    fixed_data = 0
    for fpath in data_dir.rglob("*.parquet"):
        table = pq.read_table(fpath)
        ts = table["timestamp"].to_pylist()
        if not ts:
            continue

        aligned_ts = [i / fps for i in range(len(ts))]
        max_drift = max(abs(a - b) for a, b in zip(ts, aligned_ts, strict=True))

        if max_drift > 0.02:
            col_idx = table.column_names.index("timestamp")
            new_col = pa.array(aligned_ts, type=pa.float64())
            table = table.set_column(col_idx, "timestamp", new_col)
            pq.write_table(table, fpath)
            fixed_data += 1
            rel = fpath.relative_to(dataset_dir)
            print(f"Realigned timestamps in {rel} (drift was {max_drift * 1000:.0f}ms)")

    if fixed_data:
        print(f"Realigned timestamps in {fixed_data} data files")
    else:
        print("Data timestamps already aligned, no fix needed")


def _read_episode_lengths(dataset_dir: Path, total_episodes: int) -> dict[int, int]:
    """Read episode lengths from parquet metadata files.

    Args:
        dataset_dir: Path to dataset directory.
        total_episodes: Expected number of episodes.

    Returns:
        Mapping of episode_index to frame count.
    """
    import pyarrow.parquet as pq

    lengths: dict[int, int] = {}
    episodes_dir = dataset_dir / "meta" / "episodes"
    for fpath in sorted(episodes_dir.rglob("*.parquet")):
        table = pq.read_table(fpath)
        if "length" in table.column_names:
            for row_idx in range(table.num_rows):
                ep_idx = len(lengths)
                lengths[ep_idx] = table["length"][row_idx].as_py()

    return lengths


def ensure_tasks_jsonl(dataset_dir: Path, info: dict) -> None:
    """Create meta/tasks.jsonl if missing.

    LeRobot >= 0.3.x expects this file during dataset loading. Blob-stored datasets
    converted from earlier formats may not include it.

    Args:
        dataset_dir: Path to dataset directory.
        info: Parsed info.json contents.
    """
    tasks_path = dataset_dir / "meta" / "tasks.jsonl"
    if tasks_path.exists():
        return

    total_episodes = info.get("total_episodes", 0)
    task_description = f"{info.get('robot_type', 'unknown')} manipulation task"

    with open(tasks_path, "w") as f:
        f.write(json.dumps({"task_index": 0, "task": task_description}) + "\n")
    print(f"Created {tasks_path.name} with default task: {task_description}")

    # Create episodes.jsonl mapping every episode -> task_index 0 with lengths from episode metadata
    episodes_jsonl = dataset_dir / "meta" / "episodes.jsonl"
    if not episodes_jsonl.exists() and total_episodes > 0:
        episode_lengths = _read_episode_lengths(dataset_dir, total_episodes)
        with open(episodes_jsonl, "w") as f:
            for ep in range(total_episodes):
                record = {"episode_index": ep, "tasks": [task_description], "length": episode_lengths.get(ep, 0)}
                f.write(json.dumps(record) + "\n")
        print(f"Created {episodes_jsonl.name} with {total_episodes} episodes")


def ensure_episodes_stats(dataset_dir: Path, info: dict) -> None:
    """Create meta/episodes_stats.jsonl if missing.

    LeRobot >= 0.3.x requires per-episode statistics. For v3.0 datasets that only
    have global stats (stats.json), compute per-episode stats from parquet data.

    Args:
        dataset_dir: Path to dataset directory.
        info: Parsed info.json contents.
    """
    stats_path = dataset_dir / "meta" / "episodes_stats.jsonl"
    if stats_path.exists():
        return

    import numpy as np
    import pyarrow as pa
    import pyarrow.compute
    import pyarrow.parquet as pq

    total_episodes = info.get("total_episodes", 0)
    if total_episodes == 0:
        return

    features = info.get("features", {})
    numeric_features = [
        k
        for k, v in features.items()
        if v.get("dtype") not in ("video", "image", "string")
        and k not in ("timestamp", "frame_index", "episode_index", "index", "task_index")
    ]

    # ImageNet stats for video/image features (count populated per-episode below)
    image_stats_template = {
        "mean": [[[0.485]], [[0.456]], [[0.406]]],
        "std": [[[0.229]], [[0.224]], [[0.225]]],
        "min": [[[0.0]], [[0.0]], [[0.0]]],
        "max": [[[1.0]], [[1.0]], [[1.0]]],
    }

    # Read all data from parquet files and group by episode
    data_dir = dataset_dir / "data"
    tables = []
    for fpath in sorted(data_dir.rglob("*.parquet")):
        tables.append(pq.read_table(fpath))

    if not tables:
        print("No data parquet files found, skipping episodes_stats")
        return

    combined = pa.concat_tables(tables)

    with open(stats_path, "w") as f:
        for ep in range(total_episodes):
            mask = pa.compute.equal(combined["episode_index"], ep)
            ep_table = combined.filter(mask)
            ep_stats: dict = {}

            for feat in numeric_features:
                if feat not in ep_table.column_names:
                    continue
                col = ep_table[feat]
                arr = np.array(col.to_pylist(), dtype=np.float32)
                if arr.size == 0:
                    continue
                ep_stats[feat] = {
                    "mean": arr.mean(axis=0).tolist(),
                    "std": arr.std(axis=0).tolist(),
                    "min": arr.min(axis=0).tolist(),
                    "max": arr.max(axis=0).tolist(),
                    "count": [len(arr)],
                }

            ep_count = ep_table.num_rows
            for key, feat_meta in features.items():
                if feat_meta.get("dtype") in ("video", "image"):
                    ep_stats[key] = {**image_stats_template, "count": [ep_count]}

            f.write(json.dumps({"episode_index": ep, "stats": ep_stats}) + "\n")

    print(f"Created {stats_path.name} with per-episode stats for {total_episodes} episodes")


def _verify_file_paths(dataset_dir: Path, info: dict) -> None:
    """Print diagnostic info about expected vs actual file paths.

    Supports both v2.1 (`episode_chunk`/`episode_index`) and v3.0
    (`chunk_index`/`file_index`) path templates.
    """
    total_episodes = info.get("total_episodes", 0)
    chunks_size = info.get("chunks_size", 1000)
    data_path = info.get("data_path", "")
    video_path = info.get("video_path", "")
    features = info.get("features", {})

    print(f"[verify] data_path template: {data_path}")
    print(f"[verify] video_path template: {video_path}")
    print(f"[verify] total_episodes: {total_episodes}, chunks_size: {chunks_size}")

    video_keys = [k for k, v in features.items() if v.get("dtype") in ("video", "image")]
    print(f"[verify] video_keys: {video_keys}")

    is_v30_layout = "{file_index" in data_path or "{chunk_index" in data_path

    def _format_data(ep: int) -> str | None:
        try:
            if is_v30_layout:
                return data_path.format(chunk_index=ep // chunks_size, file_index=0)
            return data_path.format(episode_chunk=ep // chunks_size, episode_index=ep)
        except (KeyError, IndexError):
            return None

    def _format_video(vk: str, ep: int) -> str | None:
        try:
            if is_v30_layout:
                return video_path.format(video_key=vk, chunk_index=ep // chunks_size, file_index=0)
            return video_path.format(video_key=vk, episode_chunk=ep // chunks_size, episode_index=ep)
        except (KeyError, IndexError):
            return None

    # Check data files
    missing_data = []
    for ep in range(min(total_episodes, 5)):
        fpath = _format_data(ep)
        if fpath is None:
            continue
        full = dataset_dir / fpath
        exists = full.is_file()
        if not exists:
            missing_data.append(fpath)
        if ep < 3:
            print(f"[verify] data ep {ep}: {fpath} -> {'OK' if exists else 'MISSING'}")
    if missing_data:
        print(f"[verify] MISSING data files (first 5): {missing_data}")

    # Check video files
    missing_video = []
    for vk in video_keys:
        for ep in range(min(total_episodes, 5)):
            fpath = _format_video(vk, ep)
            if fpath is None:
                continue
            full = dataset_dir / fpath
            exists = full.is_file()
            if not exists:
                missing_video.append(fpath)
            if ep < 3:
                print(f"[verify] video ep {ep} ({vk}): {fpath} -> {'OK' if exists else 'MISSING'}")
    if missing_video:
        print(f"[verify] MISSING video files (first 5): {missing_video}")

    # List actual files on disk
    data_dir = dataset_dir / "data"
    data_files = sorted(data_dir.rglob("*.parquet"))
    sample = [str(f.relative_to(dataset_dir)) for f in data_files[:5]]
    print(f"[verify] actual data files ({len(data_files)}): {sample}")

    videos_dir = dataset_dir / "videos"
    if videos_dir.exists():
        video_files = sorted(videos_dir.rglob("*.mp4"))
        sample = [str(f.relative_to(dataset_dir)) for f in video_files[:5]]
        print(f"[verify] actual video files ({len(video_files)}): {sample}")


def cast_bool_features_to_float(dataset_dir: Path, info: dict) -> None:
    """Cast bool feature columns to float32 in info.json and all data parquet files.

    LeRobot's normalize_processor performs ``(tensor - mean) / std`` on every
    non-image observation. Bool tensors don't support subtraction, so any
    bool-typed feature triggers ``RuntimeError: Subtraction, the - operator,
    with two bool tensors is not supported.``

    Promote bool columns to float32 (False -> 0.0, True -> 1.0) so the existing
    mean/std normalization path works without further changes.

    Args:
        dataset_dir: Path to dataset directory.
        info: Parsed info.json contents (modified in-place).
    """
    import pyarrow as pa
    import pyarrow.compute
    import pyarrow.parquet as pq

    info_path = dataset_dir / "meta" / "info.json"
    features = info.get("features", {})
    bool_keys = [k for k, v in features.items() if v.get("dtype") == "bool"]

    if not bool_keys:
        return

    print(f"Casting bool features to float32: {bool_keys}")

    data_dir = dataset_dir / "data"
    data_files = sorted(data_dir.rglob("*.parquet"))
    for fpath in data_files:
        table = pq.read_table(fpath)
        modified = False
        for key in bool_keys:
            if key not in table.column_names:
                continue
            col = table[key]
            # Columns may be List<bool> (shape > 1) or scalar bool.
            if pa.types.is_list(col.type) or pa.types.is_large_list(col.type):
                # cast inner type
                new_col = pa.compute.cast(col, pa.list_(pa.float32()))
            elif pa.types.is_boolean(col.type):
                new_col = pa.compute.cast(col, pa.float32())
            else:
                continue
            idx = table.column_names.index(key)
            table = table.set_column(idx, key, new_col)
            modified = True
        if modified:
            pq.write_table(table, fpath)

    for key in bool_keys:
        features[key]["dtype"] = "float32"

    with open(info_path, "w") as f:
        json.dump(info, f, indent=4)
    print(f"Patched {len(bool_keys)} bool features to float32 in info.json")


def _parse_env_config() -> tuple[Path, str, list[str]]:
    """Read and validate prepare_dataset environment variables.

    Returns:
        Tuple of (dataset_root, repo_id, blob_urls).

    Raises:
        ValueError: If any variable is missing or invalid.
    """
    dataset_root = Path(os.environ.get("DATASET_ROOT", "/workspace/data"))
    repo_id = os.environ.get("DATASET_REPO_ID", "")
    raw_urls = os.environ.get("BLOB_URLS", "")

    if not repo_id:
        raise ValueError("DATASET_REPO_ID is required")
    if repo_id.startswith("/") or ".." in repo_id:
        raise ValueError(f"DATASET_REPO_ID must be relative (no '/' or '..'): {repo_id!r}")
    if not (dataset_root / repo_id).resolve().is_relative_to(dataset_root.resolve()):
        raise ValueError(f"DATASET_REPO_ID escapes DATASET_ROOT: {repo_id!r}")

    try:
        urls = json.loads(raw_urls)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid BLOB_URLS JSON: {e}") from e
    if not isinstance(urls, list) or not urls:
        raise ValueError("BLOB_URLS must be a non-empty JSON array")

    return dataset_root, repo_id, urls


def _populate_staged(sources: list[Path], staged: Path) -> None:
    """Place merged or single dataset at the staged path.

    For multiple sources, lerobot-edit-dataset rewrites the dataset
    (renumbered episodes, deduplicated tasks, recomputed stats). For a single
    source the staging directory is moved into place verbatim, so the output
    shape differs subtly between the two cases.
    """
    if len(sources) > 1:
        print(f"\n--- Merging {len(sources)} datasets ---")
        merge_datasets(sources, staged)
    else:
        print("\n--- Preparing dataset ---")
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(sources[0]), str(staged))


def _postprocess_dataset(dataset_dir: Path) -> None:
    """Verify dataset and apply v2.1 conversion patches if requested."""
    info = verify_dataset(dataset_dir)
    if not info:
        return
    cast_bool_features_to_float(dataset_dir, info)
    if os.environ.get("LEROBOT_CONVERT_TO_V21", "").lower() in ("1", "true", "yes"):
        patch_info_paths(dataset_dir, info)
        patch_image_stats(dataset_dir, info)
        fix_video_timestamps(dataset_dir, info)
        ensure_tasks_jsonl(dataset_dir, info)
        ensure_episodes_stats(dataset_dir, info)
    _verify_file_paths(dataset_dir, info)


def prepare_dataset() -> Path:
    """Download blob datasets (merging if multiple) and publish atomically.

    Environment variables:
        BLOB_URLS: JSON array of blob URLs (required, non-empty).
        DATASET_REPO_ID: Dataset path relative to DATASET_ROOT (required).
        DATASET_ROOT: Local root directory (default: /workspace/data).
        LEROBOT_CONVERT_TO_V21: "1"/"true"/"yes" enables v2.1 conversion.

    Returns:
        Path to the published dataset directory.
    """
    try:
        dataset_root, repo_id, urls = _parse_env_config()
        final = dataset_root / repo_id
        if final.exists():
            raise FileExistsError(
                f"Dataset already exists at {final}. "
                "Refusing to overwrite. Remove it explicitly or change DATASET_REPO_ID."
            )
        staged = final.with_name(f".{final.name}.new")
        for stale in final.parent.glob(f".{final.name}.*"):
            if stale.is_dir():
                shutil.rmtree(stale)

        sources = []
        for idx, url in enumerate(urls):
            print(f"\n--- Downloading dataset {idx + 1}/{len(urls)} ---")
            sources.append(download_dataset_from_url(url, str(dataset_root), idx))

        _populate_staged(sources, staged)
        _postprocess_dataset(staged)
        staged.rename(final)

        print("\n--- Cleaning up staging directories ---")
        staging_base = dataset_root / ".staging"
        if staging_base.exists():
            shutil.rmtree(staging_base)

    except Exception as e:
        print(f"[ERROR] Dataset workflow failed: {e}", file=sys.stderr)
        sys.exit(EXIT_FAILURE)

    return final


if __name__ == "__main__":
    sys.exit(EXIT_SUCCESS if prepare_dataset() else EXIT_FAILURE)
