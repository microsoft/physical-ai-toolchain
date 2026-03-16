# Dataset Curation

Contracts for dataset filtering, splitting, merging, conversion, and validation operations.

## Operations

### Filter

Select episodes from a dataset based on metadata criteria.

| Parameter    | Type    | Description                                     |
|--------------|---------|-------------------------------------------------|
| `source`     | path    | Input dataset path                              |
| `output`     | path    | Output dataset path                             |
| `criteria`   | object  | Key-value filter conditions on episode metadata |
| `min_length` | integer | Minimum episode frame count                     |
| `max_length` | integer | Maximum episode frame count                     |

Output is a new dataset containing only matching episodes with updated metadata counts.

### Split

Partition a dataset into subsets for training, validation, and testing.

| Parameter    | Type    | Description                                                                   |
|--------------|---------|-------------------------------------------------------------------------------|
| `source`     | path    | Input dataset path                                                            |
| `output_dir` | path    | Parent directory for split outputs                                            |
| `ratios`     | object  | Mapping of split name to proportion (e.g., `train: 0.8, val: 0.1, test: 0.1`) |
| `strategy`   | string  | `random`, `sequential`, or `stratified`                                       |
| `seed`       | integer | Random seed for reproducibility                                               |

Each split produces an independent dataset in `output_dir/<split_name>/`.

### Merge

Combine multiple datasets into a single collection.

| Parameter     | Type       | Description                               |
|---------------|------------|-------------------------------------------|
| `sources`     | list[path] | Input dataset paths                       |
| `output`      | path       | Output dataset path                       |
| `deduplicate` | boolean    | Remove duplicate episodes by content hash |

All source datasets must share the same format. Episode indices are renumbered sequentially.

### Convert

Transform a dataset between supported formats.

| Parameter       | Type   | Description                 |
|-----------------|--------|-----------------------------|
| `source`        | path   | Input dataset path          |
| `output`        | path   | Output dataset path         |
| `target_format` | string | `hdf5`, `lerobot`, or `raw` |

Conversion preserves all episode data, actions, and observations. Format-specific metadata is regenerated for the target format.

### Validate

Check dataset integrity and schema compliance.

| Check        | Description                                           |
|--------------|-------------------------------------------------------|
| Schema       | Metadata files conform to expected structure          |
| Completeness | All referenced episode files exist                    |
| Consistency  | Frame counts match metadata declarations              |
| Integrity    | File checksums match recorded values (when available) |

Validation produces a report with pass/fail status per check and a list of specific violations.
