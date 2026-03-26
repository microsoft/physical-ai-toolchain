# Recording Configuration

Configuration schema and validation for ROS 2 edge recording parameters.

## Status

Active — schema defined and enforced at service startup.

## Components

| Component          | Description                                               |
|--------------------|-----------------------------------------------------------|
| YAML configuration | Primary config format for recording parameters            |
| JSON Schema        | Validation schema for IDE autocomplete and startup checks |
| Pydantic models    | Python models for runtime config parsing and validation   |
| Schema generator   | Script producing JSON Schema from Pydantic models         |

## Configuration Scope

| Area             | Description                                           |
|------------------|-------------------------------------------------------|
| Topic selection  | ROS 2 topics to record with frequency downsampling    |
| Episode triggers | Time-based and event-based episode segmentation       |
| Disk monitoring  | Storage thresholds and cleanup policies               |
| Gap detection    | Data quality checks for missing frames or timing gaps |
| Compression      | Per-topic compression codec and quality settings      |

## File Locations

| File              | Path                                          | Purpose                                    |
|-------------------|-----------------------------------------------|--------------------------------------------|
| Default config    | `capture/config/recording_config.yaml`        | Reference configuration for UR10E          |
| JSON Schema       | `capture/config/recording_config.schema.json` | Validation schema                          |
| Schema generator  | `capture/config/generate_config_schema.py`    | Generates JSON Schema from Pydantic models |
| Pydantic models   | `capture/models/config_models.py`             | Python config model definitions            |
| Platform examples | `capture/config/examples/`                    | Per-platform config variations             |

## Validation

The recording service validates configuration at startup using JSON Schema. Invalid configurations produce descriptive error messages and prevent service start. The `generate_config_schema.py` script regenerates the JSON Schema from Pydantic model definitions.
