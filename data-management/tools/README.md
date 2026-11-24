# Data Management Tools

CLI tools for dataset operations in the Physical AI Toolchain.

## 🧰 Tools

| Tool                                       | Purpose                                                                                 |
|--------------------------------------------|-----------------------------------------------------------------------------------------|
| [lerobot_datasets.py](lerobot_datasets.py) | List, import, derive, and remove LeRobot datasets in a directory of one-dataset folders |

### `lerobot_datasets.py`

The tool manages a datasets directory that holds one complete LeRobot dataset per folder, such as the local profile's datasets directory that OSMO jobs mount read-only and the dataviewer browses. On the local profile, run it through [infrastructure/local/datasets.sh](../../infrastructure/local/datasets.sh), which supplies the datasets directory. See [Datasets](../../infrastructure/local/README.md#-datasets) in the local profile guide.

| Command  | Effect                                                                                               |
|----------|------------------------------------------------------------------------------------------------------|
| `list`   | Print each dataset's episodes, frames, frame rate, camera count, and size                            |
| `import` | Copy a LeRobot dataset, or a capture directory that holds one under `lerobot/`, into a new folder    |
| `derive` | Merge the qualifying episodes of one or more sources into a new dataset, dropping the named features |
| `remove` | Delete a dataset after confirmation, or without it when passed `--yes`                               |

`derive` reads its sources and never changes them. A source qualifies through the sidecars next to it:

* `export-validation.json` must report a pass, so a failed export is skipped.
* Episodes in `ledger.json` must have the status `passed`.
* Episodes in `meta/episode_labels.json`, the dataviewer's label file, must carry the `SUCCESS` label.

A source with no sidecar is skipped unless you pass `--include-unvalidated`. Use `--drop-feature` to remove privileged simulation state, such as task phases or object poses, that a learner must not see.

`import` and `derive` copy only regular files and directories, so a dataset cannot pull other files from the host into the folder every OSMO job reads. They refuse a dataset that holds a symbolic link, such as a linked metadata file, or a special file such as a FIFO, and a capture directory whose `lerobot/` folder is a link. Nothing reaches the datasets directory when they refuse.

`derive` also refuses a capture sidecar that is a link or not a regular file. Neither command reads or copies the other files in a capture directory, so it does not check them. The source path you pass may itself be a link. To use a refused source, replace each link with the file it points to after checking that the file belongs to the dataset.

#### Derive the UR10e learner dataset

The local IL test trains on `ur10e-gear-to-bin-learner`, derived from the UR10e gear-to-bin v3 captures in a [physical-ai-toolchain-skills](https://github.com/microsoft/physical-ai-toolchain-skills) checkout. In that checkout, `<ur10e-captures>` is `workflows/isaac-sim/ur10e-gear-to-bin-v3/.isaac-sim/datasets/`, where each capture set holds a `lerobot/` dataset and the `ledger.json` that selects its passed episodes. Pass one `--source` per set and drop the simulation state the policy must not see:

```bash
infrastructure/local/datasets.sh derive ur10e-gear-to-bin-learner \
  --source <ur10e-captures>/<set-1> \
  --source <ur10e-captures>/<set-2> \
  --source <ur10e-captures>/<set-3> \
  --drop-feature observation.task_entity_pose \
  --drop-feature observation.tool_pose \
  --drop-feature observation.task_phase \
  --drop-feature observation.assistance
```

The result keeps the robot state, the actions, and the scene and tool cameras. The SO-101 learner dataset for the SmolVLA and judge tests is derived the same way; see [Datasets](../../infrastructure/local/README.md#-datasets) in the local profile guide.

`derive` runs `lerobot-edit-dataset` from the LeRobot release that training jobs install. It copies each source into a `.staging` folder beside the datasets directory, selects episodes and features from that copy, and moves the result into place only when it is complete, so readers never see a partial dataset. It processes one source at a time, and dropping features needs room for one more copy of that source. `import` stages its copy the same way.

`list`, `import`, and `remove` need only the Python standard library.

```bash
python3 data-management/tools/lerobot_datasets.py --datasets-dir /data/datasets list
```

## 📦 Planned Tools

| Tool       | Purpose                                                                 |
|------------|-------------------------------------------------------------------------|
| `filter`   | Select episodes matching criteria (task, success rate, metadata fields) |
| `split`    | Partition datasets into train/validation/test sets                      |
| `convert`  | Transform between dataset formats (HDF5, LeRobot, raw)                  |
| `validate` | Check dataset integrity, schema compliance, and completeness            |
