"""Unit tests for ROS 2 edge recording configuration validation models.

Tests cover:
- Topic name validation and uniqueness
- Trigger configuration discriminated union
- Disk threshold ordering constraints
- Position trigger array length matching
- Extra field rejection (typo detection)
- Field constraints and validators
"""

import pytest
from pydantic import ValidationError

from common.config_models import (
    DiskThresholds,
    GapDetectionConfig,
    GpioTriggerConfig,
    PositionTriggerConfig,
    RecordingConfig,
    TopicConfig,
    VrTriggerConfig,
)


class TestTopicConfig:
    """Tests for TopicConfig validation."""

    def test_valid_topic_config(self):
        """Valid topic config: name starts with /, frequency in range, compression valid."""
        topic = TopicConfig(name="/joint_states", frequency_hz=100.0, compression="lz4")
        assert topic.name == "/joint_states"
        assert topic.frequency_hz == 100.0
        assert topic.compression == "lz4"

    def test_topic_name_without_slash(self):
        """Topic name must start with /."""
        with pytest.raises(ValidationError, match="Topic name must start with /"):
            TopicConfig(name="joint_states", frequency_hz=100.0)

    def test_frequency_zero(self):
        """Frequency must be > 0."""
        with pytest.raises(ValidationError, match="greater than 0"):
            TopicConfig(name="/joint_states", frequency_hz=0.0)

    def test_frequency_negative(self):
        """Negative frequency is invalid."""
        with pytest.raises(ValidationError, match="greater than 0"):
            TopicConfig(name="/joint_states", frequency_hz=-10.0)

    def test_frequency_exceeds_maximum(self):
        """Frequency must be <= 1000 Hz."""
        with pytest.raises(ValidationError, match="less than or equal to 1000"):
            TopicConfig(name="/joint_states", frequency_hz=1001.0)

    def test_frequency_boundary_1000hz(self):
        """Frequency at 1000 Hz boundary is valid."""
        topic = TopicConfig(name="/joint_states", frequency_hz=1000.0)
        assert topic.frequency_hz == 1000.0

    def test_invalid_compression_algorithm(self):
        """Compression must be one of: none, lz4, zstd."""
        with pytest.raises(ValidationError, match="Input should be 'none', 'lz4' or 'zstd'"):
            TopicConfig(name="/joint_states", frequency_hz=100.0, compression="gzip")

    def test_default_compression_none(self):
        """Default compression is 'none'."""
        topic = TopicConfig(name="/joint_states", frequency_hz=100.0)
        assert topic.compression == "none"


class TestGpioTriggerConfig:
    """Tests for GPIO trigger configuration."""

    def test_valid_gpio_trigger(self):
        """Valid GPIO trigger with pin in range."""
        trigger = GpioTriggerConfig(pin=17, active_high=True)
        assert trigger.type == "gpio"
        assert trigger.pin == 17
        assert trigger.active_high is True

    def test_gpio_pin_zero(self):
        """GPIO pin 0 is valid."""
        trigger = GpioTriggerConfig(pin=0)
        assert trigger.pin == 0

    def test_gpio_pin_maximum(self):
        """GPIO pin 27 is valid."""
        trigger = GpioTriggerConfig(pin=27)
        assert trigger.pin == 27

    def test_gpio_pin_negative(self):
        """Negative GPIO pin is invalid."""
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            GpioTriggerConfig(pin=-1)

    def test_gpio_pin_exceeds_maximum(self):
        """GPIO pin > 27 is invalid."""
        with pytest.raises(ValidationError, match="less than or equal to 27"):
            GpioTriggerConfig(pin=28)

    def test_gpio_default_active_high(self):
        """Default active_high is True."""
        trigger = GpioTriggerConfig(pin=17)
        assert trigger.active_high is True


class TestPositionTriggerConfig:
    """Tests for position-based trigger configuration."""

    def test_valid_position_trigger(self):
        """Valid position trigger with matching array lengths."""
        trigger = PositionTriggerConfig(
            joint_indices=[0, 1, 2],
            tolerances=[0.01, 0.01, 0.01],
        )
        assert trigger.type == "position"
        assert trigger.joint_indices == [0, 1, 2]
        assert trigger.tolerances == [0.01, 0.01, 0.01]

    def test_position_trigger_mismatched_lengths(self):
        """joint_indices and tolerances must have same length."""
        with pytest.raises(ValidationError, match=r"Tolerance count .* must match joint index count"):
            PositionTriggerConfig(
                joint_indices=[0, 1, 2],
                tolerances=[0.01, 0.01],
            )

    def test_position_trigger_empty_arrays(self):
        """Empty arrays are invalid (min_length=1)."""
        with pytest.raises(ValidationError, match="at least 1 item"):
            PositionTriggerConfig(joint_indices=[], tolerances=[])

    def test_position_trigger_single_joint(self):
        """Single joint with matching tolerance is valid."""
        trigger = PositionTriggerConfig(joint_indices=[3], tolerances=[0.02])
        assert len(trigger.joint_indices) == 1
        assert len(trigger.tolerances) == 1


class TestVrTriggerConfig:
    """Tests for VR controller trigger configuration."""

    def test_valid_vr_trigger(self):
        """Valid VR trigger with controller and button."""
        trigger = VrTriggerConfig(controller="left", button="trigger")
        assert trigger.type == "vr"
        assert trigger.controller == "left"
        assert trigger.button == "trigger"

    def test_vr_invalid_controller(self):
        """Controller must be 'left' or 'right'."""
        with pytest.raises(ValidationError, match="Input should be 'left' or 'right'"):
            VrTriggerConfig(controller="center", button="trigger")

    def test_vr_invalid_button(self):
        """Button must be one of: trigger, grip, primary, secondary."""
        with pytest.raises(ValidationError, match="Input should be 'trigger', 'grip', 'primary' or 'secondary'"):
            VrTriggerConfig(controller="left", button="menu")

    def test_vr_all_button_types(self):
        """All valid button types work."""
        for button in ["trigger", "grip", "primary", "secondary"]:
            trigger = VrTriggerConfig(controller="right", button=button)
            assert trigger.button == button


class TestDiskThresholds:
    """Tests for disk usage threshold validation."""

    def test_valid_disk_thresholds(self):
        """Valid thresholds with warning < critical."""
        thresholds = DiskThresholds(warning_percent=75, critical_percent=90)
        assert thresholds.warning_percent == 75
        assert thresholds.critical_percent == 90

    def test_disk_thresholds_defaults(self):
        """Default values: warning=80, critical=95."""
        thresholds = DiskThresholds()
        assert thresholds.warning_percent == 80
        assert thresholds.critical_percent == 95

    def test_disk_thresholds_warning_equals_critical(self):
        """Warning threshold must be less than critical."""
        with pytest.raises(ValidationError, match=r"Warning threshold .* must be less than critical"):
            DiskThresholds(warning_percent=90, critical_percent=90)

    def test_disk_thresholds_warning_exceeds_critical(self):
        """Warning threshold cannot exceed critical."""
        with pytest.raises(ValidationError, match=r"Warning threshold .* must be less than critical"):
            DiskThresholds(warning_percent=95, critical_percent=80)

    def test_disk_thresholds_boundary_values(self):
        """Boundary: warning=0, critical=100."""
        thresholds = DiskThresholds(warning_percent=0, critical_percent=100)
        assert thresholds.warning_percent == 0
        assert thresholds.critical_percent == 100

    def test_disk_thresholds_negative_percent(self):
        """Negative percentages are invalid."""
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            DiskThresholds(warning_percent=-1, critical_percent=90)

    def test_disk_thresholds_exceeds_100(self):
        """Percentages > 100 are invalid."""
        with pytest.raises(ValidationError, match="less than or equal to 100"):
            DiskThresholds(warning_percent=80, critical_percent=101)


class TestGapDetectionConfig:
    """Tests for gap detection configuration."""

    def test_valid_gap_detection(self):
        """Valid gap detection config."""
        config = GapDetectionConfig(threshold_ms=100.0, severity="error")
        assert config.threshold_ms == 100.0
        assert config.severity == "error"

    def test_gap_detection_defaults(self):
        """Default values: threshold_ms=100.0, severity='warning'."""
        config = GapDetectionConfig()
        assert config.threshold_ms == 100.0
        assert config.severity == "warning"

    def test_gap_detection_threshold_zero(self):
        """Threshold must be > 0."""
        with pytest.raises(ValidationError, match="greater than 0"):
            GapDetectionConfig(threshold_ms=0.0)

    def test_gap_detection_threshold_negative(self):
        """Negative threshold is invalid."""
        with pytest.raises(ValidationError, match="greater than 0"):
            GapDetectionConfig(threshold_ms=-10.0)

    def test_gap_detection_invalid_severity(self):
        """Severity must be one of: warning, error, critical."""
        with pytest.raises(ValidationError, match="Input should be 'warning', 'error' or 'critical'"):
            GapDetectionConfig(severity="info")


class TestRecordingConfig:
    """Tests for root RecordingConfig validation."""

    def test_valid_recording_config_gpio(self, tmp_path):
        """Valid configuration with GPIO trigger."""
        output_dir = tmp_path / "recordings"
        output_dir.mkdir()
        config = RecordingConfig(
            topics=[
                TopicConfig(name="/joint_states", frequency_hz=100.0),
                TopicConfig(name="/camera/image", frequency_hz=30.0),
            ],
            trigger={"type": "gpio", "pin": 17},
            output_dir=output_dir,
        )
        assert len(config.topics) == 2
        assert config.trigger.type == "gpio"
        assert config.trigger.pin == 17

    def test_valid_recording_config_position(self, tmp_path):
        """Valid configuration with position trigger."""
        output_dir = tmp_path / "recordings"
        output_dir.mkdir()
        config = RecordingConfig(
            topics=[TopicConfig(name="/joint_states", frequency_hz=50.0)],
            trigger={"type": "position", "joint_indices": [0, 1], "tolerances": [0.01, 0.01]},
            output_dir=output_dir,
        )
        assert config.trigger.type == "position"
        assert config.trigger.joint_indices == [0, 1]

    def test_valid_recording_config_vr(self, tmp_path):
        """Valid configuration with VR trigger."""
        output_dir = tmp_path / "recordings"
        output_dir.mkdir()
        config = RecordingConfig(
            topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
            trigger={"type": "vr", "controller": "left", "button": "trigger"},
            output_dir=output_dir,
        )
        assert config.trigger.type == "vr"
        assert config.trigger.controller == "left"

    def test_recording_config_duplicate_topics(self, tmp_path):
        """Duplicate topic names are rejected."""
        output_dir = tmp_path / "recordings"
        output_dir.mkdir()
        with pytest.raises(ValidationError, match="Duplicate topic names found"):
            RecordingConfig(
                topics=[
                    TopicConfig(name="/joint_states", frequency_hz=100.0),
                    TopicConfig(name="/camera/image", frequency_hz=30.0),
                    TopicConfig(name="/joint_states", frequency_hz=50.0),
                ],
                trigger={"type": "gpio", "pin": 17},
                output_dir=output_dir,
            )

    def test_recording_config_extra_field_rejected(self):
        """Extra fields are rejected (typo detection)."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            RecordingConfig(
                topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
                trigger={"type": "gpio", "pin": 17},
                unknown_field="value",
            )

    def test_recording_config_default_disk_thresholds(self, tmp_path):
        """Default disk thresholds are applied."""
        output_dir = tmp_path / "recordings"
        output_dir.mkdir()
        config = RecordingConfig(
            topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
            trigger={"type": "gpio", "pin": 17},
            output_dir=output_dir,
        )
        assert config.disk_thresholds.warning_percent == 80
        assert config.disk_thresholds.critical_percent == 95

    def test_recording_config_default_gap_detection(self, tmp_path):
        """Default gap detection config is applied."""
        output_dir = tmp_path / "recordings"
        output_dir.mkdir()
        config = RecordingConfig(
            topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
            trigger={"type": "gpio", "pin": 17},
            output_dir=output_dir,
        )
        assert config.gap_detection.threshold_ms == 100.0
        assert config.gap_detection.severity == "warning"

    def test_recording_config_custom_output_dir(self, tmp_path):
        """Custom output directory is preserved."""

        custom_dir = tmp_path / "custom" / "path"
        custom_dir.mkdir(parents=True)
        config = RecordingConfig(
            topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
            trigger={"type": "gpio", "pin": 17},
            output_dir=custom_dir,
        )
        assert config.output_dir == custom_dir

    def test_recording_config_discriminated_union_invalid_type(self):
        """Invalid trigger type is rejected by discriminated union."""
        with pytest.raises(ValidationError, match="does not match any of the expected tags"):
            RecordingConfig(
                topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
                trigger={"type": "invalid"},
            )

    def test_recording_config_discriminated_union_missing_required_field(self):
        """Missing required field for trigger type is rejected."""
        with pytest.raises(ValidationError, match="Field required"):
            RecordingConfig(
                topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
                trigger={"type": "gpio"},
            )

    def test_recording_config_minimum_one_topic(self, tmp_path):
        """At least one topic is required."""
        output_dir = tmp_path / "recordings"
        output_dir.mkdir()
        config = RecordingConfig(
            topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
            trigger={"type": "gpio", "pin": 17},
            output_dir=output_dir,
        )
        assert len(config.topics) >= 1


class TestJSONSchemaGeneration:
    """Tests for JSON Schema generation from pydantic models."""

    def test_json_schema_generation(self):
        """Pydantic models produce valid JSON Schema."""
        schema = RecordingConfig.model_json_schema()

        assert schema["type"] == "object"
        assert "properties" in schema
        assert "topics" in schema["properties"]
        assert "trigger" in schema["properties"]

    def test_schema_discriminated_union_structure(self):
        """Trigger uses discriminated union in generated schema."""
        schema = RecordingConfig.model_json_schema()

        assert "discriminator" in schema["properties"]["trigger"]
        trigger_discriminator = schema["properties"]["trigger"]["discriminator"]
        assert trigger_discriminator["propertyName"] == "type"

    def test_schema_field_descriptions_present(self):
        """All fields have descriptions in generated schema."""
        schema = TopicConfig.model_json_schema()

        for field in ["name", "frequency_hz", "compression"]:
            assert "description" in schema["properties"][field]
            assert len(schema["properties"][field]["description"]) > 0

    def test_schema_contains_all_models(self):
        """Generated schema includes all model definitions."""
        schema = RecordingConfig.model_json_schema()

        assert "$defs" in schema or "definitions" in schema
        defs_key = "$defs" if "$defs" in schema else "definitions"

        expected_models = [
            "TopicConfig",
            "GpioTriggerConfig",
            "PositionTriggerConfig",
            "VrTriggerConfig",
            "DiskThresholds",
            "GapDetectionConfig",
        ]

        for model_name in expected_models:
            assert model_name in schema[defs_key]


class TestOutputDirValidation:
    """Tests for output_dir path validation."""

    def test_output_dir_must_be_absolute(self, tmp_path):
        """Relative paths are rejected."""
        with pytest.raises(ValidationError, match="absolute path"):
            RecordingConfig(
                topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
                trigger={"type": "gpio", "pin": 17},
                output_dir="relative/path",
            )

    def test_output_dir_must_exist(self, tmp_path):
        """Non-existent directory is rejected."""
        non_existent = tmp_path / "does_not_exist"
        with pytest.raises(ValidationError, match="does not exist"):
            RecordingConfig(
                topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
                trigger={"type": "gpio", "pin": 17},
                output_dir=non_existent,
            )

    def test_output_dir_must_be_directory(self, tmp_path):
        """File path is rejected."""
        file_path = tmp_path / "file.txt"
        file_path.touch()
        with pytest.raises(ValidationError, match="not a directory"):
            RecordingConfig(
                topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
                trigger={"type": "gpio", "pin": 17},
                output_dir=file_path,
            )

    def test_output_dir_must_be_writable(self, tmp_path):
        """Non-writable directory is rejected."""
        read_only_dir = tmp_path / "read_only"
        read_only_dir.mkdir()
        read_only_dir.chmod(0o444)

        try:
            with pytest.raises(ValidationError, match="not writable"):
                RecordingConfig(
                    topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
                    trigger={"type": "gpio", "pin": 17},
                    output_dir=read_only_dir,
                )
        finally:
            read_only_dir.chmod(0o755)

    def test_output_dir_valid(self, tmp_path):
        """Valid writable directory is accepted."""
        valid_dir = tmp_path / "recordings"
        valid_dir.mkdir()

        config = RecordingConfig(
            topics=[TopicConfig(name="/joint_states", frequency_hz=100.0)],
            trigger={"type": "gpio", "pin": 17},
            output_dir=valid_dir,
        )
        assert config.output_dir == valid_dir
