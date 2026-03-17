# Per-Pool Configuration Overrides

Place JSON files named `{pool_id}.json` in this directory to override
pool configuration values generated from terraform state.

Files are merged with the generated pool config using `jq` deep merge (`*` operator).
Only fields present in the override file are replaced; all other fields retain their
generated values.

## Example

For a pool named `gpupool`, create `gpupool.json`:

```json
{
  "description": "GPU pool with custom settings",
  "platforms": {
    "gpupool_platform": {
      "privileged_allowed": false
    }
  }
}
```
