---
title: Dataset Curation
description: General dataset filtering, splitting, merging, conversion and validation contracts.
ms.date: 2026-11-03
ms.topic: reference
---

Contracts for dataset filtering, splitting, merging, conversion, and validation operations.

Curated release publication is a separate workflow with a narrower supported
route matrix. Its saved-selection, provenance, immutable package, verification and
recoverable-job contracts are defined in the
[Curated Dataset Release specification](curated-dataset-release.specification.md).
The operations below do not imply that releases support arbitrary merging,
splitting or format conversion.

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
