# Azure Blob Storage Folder Structure

Standardized folder structure for robotics data stored in Azure Blob Storage, including raw ROS bags, converted datasets, validation reports, and model checkpoints.

## Container and Folder Organization

**Default Container:** `ml-workspace`

### Folder Structure

| Folder Prefix  | Purpose                             | Lifecycle Policy            | Example Path                                          |
|----------------|-------------------------------------|-----------------------------|-------------------------------------------------------|
| `raw/`         | Raw ROS bag files from edge devices | Auto-delete after 30 days   | `raw/robot-01/2026-03-05/episode-001.mcap`            |
| `converted/`   | LeRobot datasets in v0.3.x format   | Tier to cool after 90 days  | `converted/pick-place-v1/meta/info.json`              |
| `reports/`     | Validation reports and metrics      | Cool (30d) → Archive (180d) | `reports/pick-place-v1/2026-03-05/eval_results.json`  |
| `checkpoints/` | Model checkpoints                   | Retained indefinitely (Hot) | `checkpoints/act-policy/20260305_143022_step_1000.pt` |

## Naming Conventions

**General Rules:**

- Lowercase only (no uppercase characters)
- Hyphens for separators (no underscores or spaces)
- Dates: ISO 8601 format `YYYY-MM-DD`
- Timestamps: `YYYYMMDD_HHMMSS` (UTC, compact format)

### Device Identifiers

**Pattern:** `{robot-type}-{instance-number}`

**Examples:** `robot-01`, `ur10e-arm`, `mobile-manipulator-03`

### Dataset Identifiers

**Pattern:** `{task-description}-v{version-number}`

**Examples:** `pick-place-v1`, `navigation-rough-terrain-v2`, `manipulation-assembly-v3`

### File Naming

**ROS Bags:** `{episode-or-sequence-id}.mcap`
**Datasets:** Follow LeRobot v0.3.x conventions (`episode_{NNNNNN}.parquet`, `chunk-{NNN}/`)
**Reports:** `{metric-type}_results.json` or `ep{NNN}_predictions.npz`
**Checkpoints:** `{timestamp}_step_{N}.pt` or `{timestamp}.{onnx|jit}`

## Path Patterns

### Raw ROS Bags

**Pattern:** `raw/{device-id}/{YYYY-MM-DD}/{filename}.mcap`

**Examples:**

```text
raw/robot-01/2026-03-05/episode-001.mcap
raw/ur10e-arm/2026-03-04/pick-task-001.mcap
raw/mobile-manipulator-03/2026-03-01/navigation-001.mcap
```

### Converted LeRobot Datasets

**Pattern:** `converted/{dataset-id}/meta/info.json`

**Structure:**

```text
converted/{dataset-id}/
├── meta/
│   ├── info.json
│   └── stats.json
├── data/
│   └── chunk-{NNN}/
│       └── episode_{NNNNNN}.parquet
└── videos/
    └── {feature-key}/
        └── chunk-{NNN}/
            └── episode_{NNNN}.mp4
```

**Example:**

```text
converted/pick-place-v1/meta/info.json
converted/pick-place-v1/data/chunk-000/episode_000000.parquet
converted/pick-place-v1/videos/observation.image/chunk-000/episode_0000.mp4
```

### Validation Reports

**Pattern:** `reports/{dataset-id}/{YYYY-MM-DD}/{filename}.json`

**Examples:**

```text
reports/pick-place-v1/2026-03-05/eval_results.json
reports/pick-place-v1/2026-03-05/ep000_predictions.npz
reports/navigation-v2/2026-03-04/mse_results.json
```

### Model Checkpoints

**Pattern:** `checkpoints/{model-name}/{timestamp}_step_{N}.{ext}`

**Examples:**

```text
checkpoints/act-policy/20260305_143022_step_1000.pt
checkpoints/diffusion-policy/20260304_091500_step_5000.pt
checkpoints/velocity-anymal/20260301_120000.onnx
```

## Lifecycle Management Policies

Lifecycle policies automatically manage blob storage costs by tiering and deleting data based on age.

### Policy Details

| Folder Prefix  | Action          | Timing                | Configurable                              |
|----------------|-----------------|-----------------------|-------------------------------------------|
| `raw/`         | Delete          | After 30 days         | Yes (`raw_bags_retention_days`)           |
| `converted/`   | Tier to Cool    | After 90 days         | Yes (`converted_datasets_cool_tier_days`) |
| `reports/`     | Tier to Cool    | After 30 days         | Yes (`reports_cool_tier_days`)            |
| `reports/`     | Tier to Archive | After 180 days        | Yes (`reports_archive_tier_days`)         |
| `checkpoints/` | None            | Retained indefinitely | N/A                                       |

### Configuration

Lifecycle policies are defined in Terraform variables:

**File:** `deploy/001-iac/terraform.tfvars`

```hcl
should_enable_raw_bags_lifecycle_policy          = true
raw_bags_retention_days                          = 30
should_enable_converted_datasets_lifecycle_policy = true
converted_datasets_cool_tier_days                = 90
should_enable_reports_lifecycle_policy           = true
reports_cool_tier_days                           = 30
reports_archive_tier_days                        = 180
```

**Disable a policy:** Set `should_enable_*_lifecycle_policy = false`
**Disable an action:** Set days to `-1` (e.g., `raw_bags_retention_days = -1`)

### Policy Activation

Policies take up to 24 hours to activate after Terraform deployment. Verify activation in Azure Portal → Storage Account → Lifecycle management.

### Access Tiers

| Tier        | Access Latency      | Cost (Storage) | Cost (Access) | Use Case                                    |
|-------------|---------------------|----------------|---------------|---------------------------------------------|
| **Hot**     | Immediate           | High           | Low           | Active training data, recent checkpoints    |
| **Cool**    | Immediate           | Medium         | Medium        | Archived datasets, older validation reports |
| **Archive** | Hours (rehydration) | Low            | High          | Long-term compliance, historical reports    |

### Rehydration

Archived blobs cannot be accessed directly. To access an archived blob:

1. Rehydrate to Hot or Cool tier in Azure Portal → Blob → Change tier
2. Wait for rehydration (up to 15 hours with Standard priority)
3. Access data after rehydration completes

Lifecycle policies do NOT automatically rehydrate blobs.

## Enforcement and Validation

### Naming Convention Enforcement

**Current State:** Naming conventions are documented but not enforced by Azure.

**Recommended Approach:**

- Document conventions clearly (this file)
- Provide path validation helpers in upload scripts
- Use PR reviews to catch non-compliant uploads

**Future Enhancement:** Implement Azure Functions or Logic Apps to validate blob names on upload and reject non-compliant uploads.

### Path Validation Examples

**Python helper function:**

```python
import re
from pathlib import Path

def validate_blob_path(blob_name: str, data_type: str) -> bool:
    """Validate blob path follows naming conventions."""
    patterns = {
        "raw": r"^raw/[a-z0-9-]+/\d{4}-\d{2}-\d{2}/[a-z0-9-]+\.(mcap|bag)$",
        "converted": r"^converted/[a-z0-9-]+(-v\d+)?/(meta|data|videos)/.+$",
        "reports": r"^reports/[a-z0-9-]+/\d{4}-\d{2}-\d{2}/[a-z0-9-_]+\.(json|npz|mp4)$",
        "checkpoints": r"^checkpoints/[a-z0-9-]+/\d{8}_\d{6}(_step_\d+)?\.(pt|onnx|jit)$",
    }

    if data_type not in patterns:
        raise ValueError(f"Unknown data type: {data_type}")

    return bool(re.match(patterns[data_type], blob_name))

# Example usage
assert validate_blob_path("raw/robot-01/2026-03-05/episode-001.mcap", "raw")
assert not validate_blob_path("raw/Robot-01/2026-03-05/Episode 001.mcap", "raw")  # Uppercase and spaces
```

## Migration from Existing Patterns

### Current Checkpoint Pattern

**Existing:** `checkpoints/{model-name}/{timestamp}_step_{N}.pt`
**Status:** ✅ Already compliant with naming conventions

No migration needed.

### Current Inference Outputs

**Existing:** `inference_outputs/{task}/{timestamp}/{models,videos}/`
**Proposed:** `reports/{dataset-id}/{YYYY-MM-DD}/{filename}`

**Migration Strategy:**

- Keep `inference_outputs/` for backward compatibility (no lifecycle policy)
- Update future uploads to use `reports/` pattern
- Optional: One-time migration script to restructure existing inference outputs

### Current LeRobot Datasets

**Existing:** Ad-hoc blob prefixes specified at download time
**Proposed:** `converted/{dataset-id}/meta/info.json`

**Migration Strategy:**

- Document `converted/` prefix as standard for new uploads
- Update dataset upload scripts (out of scope for Issue #238)
- Existing datasets can remain at current paths (lifecycle policy applies to ALL paths matching `converted/`)

## Operations and Monitoring

### Verify Lifecycle Policy Status

**Azure Portal:**

1. Navigate to Storage Account (e.g., `st<resource-prefix><environment><instance>`)
2. Settings → Lifecycle management
3. Verify rules: `delete-raw-bags`, `tier-converted-datasets-to-cool`, `tier-reports-to-cool-then-archive`

**Azure CLI:**

```bash
az storage account management-policy show \
  --account-name st<resource-prefix><environment><instance> \
  --resource-group rg-<resource-prefix>-<environment>-<instance>
```

### Monitor Storage Costs

**Cost Analysis:**

- Azure Portal → Cost Management → Cost analysis
- Filter by Resource: Storage Account name
- Group by Meter: `Blob Storage - Hot`, `Blob Storage - Cool`, `Blob Storage - Archive`

**Expected Cost Reduction:**

- Raw bags: Deleted after 30 days (storage costs eliminated)
- Converted datasets: Cool tier saves ~50% storage cost vs. Hot
- Reports: Archive tier saves ~80% storage cost vs. Hot (after 180 days)

### Troubleshooting

**Policy not applying:**

- Wait 24 hours after Terraform deployment
- Verify `enabled = true` in Terraform state
- Check blob modification time (policy uses `days_since_modification_greater_than`)

**Blobs not tiering as expected:**

- Confirm blob is `blockBlob` type (lifecycle policies don't apply to page/append blobs)
- Verify prefix match is case-sensitive: `raw/` ≠ `Raw/`
- Check Azure Activity Log for lifecycle policy execution events

**Cannot access archived blob:**

- Rehydrate to Hot or Cool tier via Azure Portal or CLI
- Wait for rehydration to complete (up to 15 hours)
- Use High priority rehydration for faster access (additional cost)

## References

- [Azure Blob Storage Lifecycle Management](https://learn.microsoft.com/azure/storage/blobs/lifecycle-management-overview)
- [Terraform azurerm_storage_management_policy](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_management_policy)
- [LeRobot Dataset Format v0.3.x](https://github.com/huggingface/lerobot/tree/main/src/lerobot/datasets)
- [ROS 2 MCAP Format](https://mcap.dev/)
