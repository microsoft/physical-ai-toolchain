"""Unit tests for blob path validator."""

import pytest

from src.common.blob_path_validator import (
    get_validation_error,
    validate_blob_path,
)


class TestRawBagValidation:
    """Test validation for raw ROS bag paths."""

    def test_valid_raw_mcap(self):
        assert validate_blob_path("raw/robot-01/2026-03-05/episode-001.mcap", "raw")

    def test_valid_raw_bag(self):
        assert validate_blob_path("raw/ur10e-arm/2026-03-04/pick-task-001.bag", "raw")

    def test_valid_raw_mobile_manipulator(self):
        assert validate_blob_path("raw/mobile-manipulator-03/2026-03-01/navigation-001.mcap", "raw")

    def test_invalid_raw_uppercase_device(self):
        assert not validate_blob_path("raw/Robot-01/2026-03-05/episode-001.mcap", "raw")

    def test_invalid_raw_uppercase_filename(self):
        assert not validate_blob_path("raw/robot-01/2026-03-05/Episode-001.mcap", "raw")

    def test_invalid_raw_spaces(self):
        assert not validate_blob_path("raw/robot-01/2026-03-05/episode 001.mcap", "raw")

    def test_invalid_raw_wrong_date_format(self):
        assert not validate_blob_path("raw/robot-01/03-05-2026/episode-001.mcap", "raw")

    def test_invalid_raw_wrong_extension(self):
        assert not validate_blob_path("raw/robot-01/2026-03-05/episode-001.txt", "raw")

    def test_invalid_raw_missing_date(self):
        assert not validate_blob_path("raw/robot-01/episode-001.mcap", "raw")


class TestConvertedDatasetValidation:
    """Test validation for converted LeRobot dataset paths."""

    def test_valid_converted_meta_info(self):
        assert validate_blob_path("converted/pick-place-v1/meta/info.json", "converted")

    def test_valid_converted_meta_stats(self):
        assert validate_blob_path("converted/pick-place-v1/meta/stats.json", "converted")

    def test_valid_converted_data_parquet(self):
        assert validate_blob_path("converted/pick-place-v1/data/chunk-000/episode_000000.parquet", "converted")

    def test_valid_converted_video(self):
        assert validate_blob_path(
            "converted/pick-place-v1/videos/observation.image/chunk-000/episode_0000.mp4",
            "converted",
        )

    def test_valid_converted_no_version(self):
        assert validate_blob_path("converted/navigation/meta/info.json", "converted")

    def test_invalid_converted_uppercase(self):
        assert not validate_blob_path("converted/Pick-Place-v1/meta/info.json", "converted")

    def test_invalid_converted_wrong_subfolder(self):
        assert not validate_blob_path("converted/pick-place-v1/invalid/info.json", "converted")


class TestReportsValidation:
    """Test validation for validation report paths."""

    def test_valid_reports_json(self):
        assert validate_blob_path("reports/pick-place-v1/2026-03-05/eval_results.json", "reports")

    def test_valid_reports_npz(self):
        assert validate_blob_path("reports/pick-place-v1/2026-03-05/ep000_predictions.npz", "reports")

    def test_valid_reports_mp4(self):
        assert validate_blob_path("reports/pick-place-v1/2026-03-05/inference_video.mp4", "reports")

    def test_invalid_reports_uppercase(self):
        assert not validate_blob_path("reports/Pick-Place-v1/2026-03-05/eval_results.json", "reports")

    def test_invalid_reports_wrong_date_format(self):
        assert not validate_blob_path("reports/pick-place-v1/03-05-2026/eval_results.json", "reports")

    def test_invalid_reports_missing_date(self):
        assert not validate_blob_path("reports/pick-place-v1/eval_results.json", "reports")


class TestCheckpointsValidation:
    """Test validation for model checkpoint paths."""

    def test_valid_checkpoints_with_step(self):
        assert validate_blob_path("checkpoints/act-policy/20260305_143022_step_1000.pt", "checkpoints")

    def test_valid_checkpoints_no_step(self):
        assert validate_blob_path("checkpoints/velocity-anymal/20260301_120000.pt", "checkpoints")

    def test_valid_checkpoints_onnx(self):
        assert validate_blob_path("checkpoints/diffusion-policy/20260304_091500.onnx", "checkpoints")

    def test_valid_checkpoints_jit(self):
        assert validate_blob_path("checkpoints/rsl-rl/20260305_100000.jit", "checkpoints")

    def test_invalid_checkpoints_uppercase(self):
        assert not validate_blob_path("checkpoints/ACT-Policy/20260305_143022_step_1000.pt", "checkpoints")

    def test_invalid_checkpoints_wrong_timestamp_format(self):
        assert not validate_blob_path("checkpoints/act-policy/2026-03-05_14-30-22_step_1000.pt", "checkpoints")

    def test_invalid_checkpoints_wrong_extension(self):
        assert not validate_blob_path("checkpoints/act-policy/20260305_143022_step_1000.pth", "checkpoints")


class TestValidationErrors:
    """Test validation error messages."""

    def test_error_message_uppercase(self):
        error = get_validation_error("raw/Robot-01/2026-03-05/episode-001.mcap", "raw")
        assert error is not None
        assert "uppercase" in error
        assert "docs/cloud/blob-storage-structure.md" in error

    def test_error_message_spaces(self):
        error = get_validation_error("raw/robot-01/2026-03-05/episode 001.mcap", "raw")
        assert error is not None
        assert "spaces" in error
        assert "hyphens" in error

    def test_error_message_multiple_issues(self):
        error = get_validation_error("raw/Robot-01/2026-03-05/Episode 001.mcap", "raw")
        assert error is not None
        assert "uppercase" in error
        assert "spaces" in error

    def test_valid_path_no_error(self):
        error = get_validation_error("raw/robot-01/2026-03-05/episode-001.mcap", "raw")
        assert error is None

    def test_unknown_data_type_raises_error(self):
        with pytest.raises(ValueError, match="Unknown data type"):
            validate_blob_path("some/path/file.txt", "invalid")  # type: ignore
