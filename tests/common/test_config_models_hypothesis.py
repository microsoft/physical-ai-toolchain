"""Hypothesis property-based tests for ROS 2 edge recording configuration models."""

import tempfile

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
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


@given(
    name=st.from_regex(r"/[a-z][a-z0-9_/]*", fullmatch=True),
    frequency_hz=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False),
    compression=st.sampled_from(["none", "lz4", "zstd"]),
)
def test_topic_config_roundtrip(name: str, frequency_hz: float, compression: str):
    """Valid TopicConfig survives model_dump → model_validate roundtrip."""
    original = TopicConfig(name=name, frequency_hz=frequency_hz, compression=compression)
    rebuilt = TopicConfig.model_validate(original.model_dump())
    assert rebuilt.name == original.name
    assert rebuilt.frequency_hz == original.frequency_hz
    assert rebuilt.compression == original.compression


@given(name=st.text(min_size=1).filter(lambda s: not s.startswith("/")))
def test_topic_config_rejects_invalid_name(name: str):
    """Names not starting with / are rejected."""
    with pytest.raises(ValidationError, match="Topic name must start with /"):
        TopicConfig(name=name, frequency_hz=100.0)


@given(pin=st.integers(0, 27), active_high=st.booleans())
def test_gpio_trigger_roundtrip(pin: int, active_high: bool):
    """Valid GpioTriggerConfig survives model_dump → model_validate roundtrip."""
    original = GpioTriggerConfig(pin=pin, active_high=active_high)
    rebuilt = GpioTriggerConfig.model_validate(original.model_dump())
    assert rebuilt.pin == original.pin
    assert rebuilt.active_high == original.active_high
    assert rebuilt.type == "gpio"


@st.composite
def matched_position_lists(draw):
    """Generate joint_indices and tolerances lists of equal length."""
    length = draw(st.integers(min_value=1, max_value=10))
    joint_indices = draw(st.lists(st.integers(0, 20), min_size=length, max_size=length))
    tolerances = draw(
        st.lists(
            st.floats(min_value=0.001, max_value=10.0, allow_nan=False, allow_infinity=False),
            min_size=length,
            max_size=length,
        )
    )
    return joint_indices, tolerances


@given(data=matched_position_lists())
def test_position_trigger_matching_lengths(data):
    """PositionTriggerConfig with matched list lengths survives roundtrip."""
    joint_indices, tolerances = data
    original = PositionTriggerConfig(joint_indices=joint_indices, tolerances=tolerances)
    rebuilt = PositionTriggerConfig.model_validate(original.model_dump())
    assert rebuilt.joint_indices == original.joint_indices
    assert rebuilt.tolerances == original.tolerances


@given(
    indices_len=st.integers(min_value=1, max_value=10),
    tolerances_len=st.integers(min_value=1, max_value=10),
)
def test_position_trigger_mismatched_lengths_rejected(indices_len: int, tolerances_len: int):
    """Mismatched joint_indices and tolerances lengths raise ValidationError."""
    assume(indices_len != tolerances_len)
    with pytest.raises(ValidationError, match="must match joint index count"):
        PositionTriggerConfig(
            joint_indices=list(range(indices_len)),
            tolerances=[0.1] * tolerances_len,
        )


@given(
    data=st.lists(st.integers(min_value=0, max_value=100), min_size=2, max_size=2, unique=True),
)
def test_disk_thresholds_ordering(data: list[int]):
    """DiskThresholds with warning < critical survives roundtrip."""
    low, high = sorted(data)
    assume(low < high)
    original = DiskThresholds(warning_percent=low, critical_percent=high)
    rebuilt = DiskThresholds.model_validate(original.model_dump())
    assert rebuilt.warning_percent == original.warning_percent
    assert rebuilt.critical_percent == original.critical_percent


@given(
    data=st.lists(st.integers(min_value=0, max_value=100), min_size=2, max_size=2),
)
def test_disk_thresholds_invalid_ordering_rejected(data: list[int]):
    """warning_percent >= critical_percent raises ValidationError."""
    warning, critical = data
    assume(warning >= critical)
    with pytest.raises(ValidationError, match="must be less than critical"):
        DiskThresholds(warning_percent=warning, critical_percent=critical)


@given(
    controller=st.sampled_from(["left", "right"]),
    button=st.sampled_from(["trigger", "grip", "primary", "secondary"]),
)
def test_vr_trigger_config_roundtrip(controller, button):
    """Valid VrTriggerConfig survives model_dump → model_validate roundtrip."""
    trigger = VrTriggerConfig(controller=controller, button=button)
    assert trigger.type == "vr"
    rebuilt = VrTriggerConfig.model_validate(trigger.model_dump())
    assert rebuilt == trigger


@given(
    threshold_ms=st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False),
    severity=st.sampled_from(["warning", "error", "critical"]),
)
def test_gap_detection_config_roundtrip(threshold_ms, severity):
    """Valid GapDetectionConfig survives model_dump → model_validate roundtrip."""
    config = GapDetectionConfig(threshold_ms=threshold_ms, severity=severity)
    rebuilt = GapDetectionConfig.model_validate(config.model_dump())
    assert rebuilt.threshold_ms == config.threshold_ms
    assert rebuilt.severity == severity


@given(name=st.text(min_size=2, max_size=50).map(lambda s: "/" + s))
def test_recording_config_unique_topics(name):
    """Duplicate topic names raise ValidationError."""
    topic = {"name": name, "frequency_hz": 100.0}
    with pytest.raises(ValidationError, match="Duplicate topic names"):
        RecordingConfig(
            topics=[TopicConfig(**topic), TopicConfig(**topic)],
            trigger=GpioTriggerConfig(pin=17),
            output_dir=tempfile.mkdtemp(),
        )


@given(field_name=st.text(min_size=1, max_size=20).filter(lambda s: s.isidentifier()))
def test_recording_config_rejects_extra_fields(field_name):
    """Unknown fields rejected due to extra='forbid'."""
    assume(field_name not in RecordingConfig.model_fields)
    base = {
        "topics": [{"name": "/test", "frequency_hz": 100.0}],
        "trigger": {"type": "gpio", "pin": 17},
        "output_dir": tempfile.mkdtemp(),
    }
    base[field_name] = "unexpected"
    with pytest.raises(ValidationError, match="Extra inputs"):
        RecordingConfig(**base)
