---
description: Shared LeRobot metadata parsing and confined local media paths for curation and evaluation.
---

# Dataset I/O

`physical-ai-dataset-io` provides LeRobot metadata parsing and local media path
validation for curation and evaluation. It does not load models, decode video,
read curation annotations or mutate datasets.

The base package has no runtime dependencies and supports v2.1 and v3 JSONL
metadata. The optional `parquet` extra supplies PyArrow and pandas for v3
Parquet metadata. Curation exposes the same profile through its `parquet` extra;
existing evaluator environments supply their own pinned data dependencies.

Repository consumers map `physical-ai-dataset-io==0.1.0` to this directory in
their `tool.uv.sources` configuration. Use the consumer's committed lockfile
when restoring an environment.

```python
from pathlib import Path

from physical_ai_dataset_io.lerobot import iter_episodes
from physical_ai_dataset_io.media import validate_media_root, validate_video_path

root = validate_media_root(Path("/data/example-dataset"))
episodes = iter_episodes(
    root,
    views=("observation.images.front",),
    video_validator=lambda path: validate_video_path(path, root),
)
for episode in episodes:
    print(episode.episode_index, episode.video_windows)
```

`lerobot` owns `DatasetSpec`, `EpisodeRecord`, `load_dataset_spec` and
`iter_episodes`. `media` owns `MediaInputError`, `validate_media_root` and
`validate_video_path`. Import these modules directly; the former evaluator
dataset module is not a compatibility entrypoint.

The package preserves metadata parsing, per-view windows and path checks.
`local_media_identity` supplies a shared path/stat fingerprint for CLI and viewer
saved snapshots. It is a freshness token, not an immutable content digest.
Path validation confines regular MP4 files to a configured root but
does not protect against a trusted local writer replacing a file afterward.
