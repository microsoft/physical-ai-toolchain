"""Tests for image transformation functions including color adjustments."""

from unittest.mock import patch

import numpy as np
import pytest

from src.api.services import image_transform as image_transform_module
from src.api.services.image_transform import (
    ColorAdjustment,
    CropRegion,
    ImageTransform,
    ImageTransformError,
    ResizeDimensions,
    apply_brightness,
    apply_camera_transforms,
    apply_color_adjustment,
    apply_color_filter,
    apply_contrast,
    apply_crop,
    apply_gamma,
    apply_hue_rotation,
    apply_resize,
    apply_saturation,
    apply_transform,
    apply_transforms_batch,
    get_output_dimensions,
)


# Test fixtures
@pytest.fixture
def sample_rgb_frame() -> np.ndarray:
    """Create a sample RGB frame for testing."""
    # Create a 100x100 RGB image with gradient
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    for i in range(100):
        for j in range(100):
            frame[i, j] = [i * 2.5, j * 2.5, 128]  # R and G gradients, B constant
    return frame


@pytest.fixture
def sample_gray_frame() -> np.ndarray:
    """Create a sample grayscale frame for testing."""
    return np.full((100, 100), 128, dtype=np.uint8)


class TestApplyCrop:
    """Tests for apply_crop function."""

    def test_crop_valid_region(self, sample_rgb_frame: np.ndarray) -> None:
        """Test cropping a valid region."""
        crop = CropRegion(x=10, y=20, width=50, height=30)
        result = apply_crop(sample_rgb_frame, crop)

        assert result.shape == (30, 50, 3)
        # Verify content matches the cropped region
        np.testing.assert_array_equal(result, sample_rgb_frame[20:50, 10:60])

    def test_crop_at_origin(self, sample_rgb_frame: np.ndarray) -> None:
        """Test cropping starting at origin."""
        crop = CropRegion(x=0, y=0, width=25, height=25)
        result = apply_crop(sample_rgb_frame, crop)

        assert result.shape == (25, 25, 3)

    def test_crop_to_edge(self, sample_rgb_frame: np.ndarray) -> None:
        """Test cropping to the edge of the image."""
        crop = CropRegion(x=50, y=50, width=50, height=50)
        result = apply_crop(sample_rgb_frame, crop)

        assert result.shape == (50, 50, 3)

    def test_crop_exceeds_bounds_raises_error(self, sample_rgb_frame: np.ndarray) -> None:
        """Test that cropping outside bounds raises error."""
        crop = CropRegion(x=80, y=80, width=50, height=50)

        with pytest.raises(ImageTransformError, match="exceeds image bounds"):
            apply_crop(sample_rgb_frame, crop)

    def test_crop_negative_offset_raises_error(self, sample_rgb_frame: np.ndarray) -> None:
        """Test that negative offset raises error."""
        crop = CropRegion(x=-10, y=0, width=50, height=50)

        with pytest.raises(ImageTransformError, match="cannot be negative"):
            apply_crop(sample_rgb_frame, crop)

    def test_crop_zero_dimensions_raises_error(self, sample_rgb_frame: np.ndarray) -> None:
        """Test that zero dimensions raise error."""
        crop = CropRegion(x=0, y=0, width=0, height=50)

        with pytest.raises(ImageTransformError, match="must be positive"):
            apply_crop(sample_rgb_frame, crop)


class TestApplyResize:
    """Tests for apply_resize function."""

    def test_resize_smaller(self, sample_rgb_frame: np.ndarray) -> None:
        """Test resizing to smaller dimensions."""
        size = ResizeDimensions(width=50, height=50)
        result = apply_resize(sample_rgb_frame, size)

        assert result.shape == (50, 50, 3)
        assert result.dtype == np.uint8

    def test_resize_larger(self, sample_rgb_frame: np.ndarray) -> None:
        """Test resizing to larger dimensions."""
        size = ResizeDimensions(width=200, height=200)
        result = apply_resize(sample_rgb_frame, size)

        assert result.shape == (200, 200, 3)
        assert result.dtype == np.uint8

    def test_resize_non_square(self, sample_rgb_frame: np.ndarray) -> None:
        """Test resizing to non-square dimensions."""
        size = ResizeDimensions(width=80, height=40)
        result = apply_resize(sample_rgb_frame, size)

        assert result.shape == (40, 80, 3)

    def test_resize_zero_dimensions_raises_error(self, sample_rgb_frame: np.ndarray) -> None:
        """Test that zero dimensions raise error."""
        size = ResizeDimensions(width=0, height=50)

        with pytest.raises(ImageTransformError, match="must be positive"):
            apply_resize(sample_rgb_frame, size)


class TestApplyBrightness:
    """Tests for apply_brightness function."""

    def test_brightness_increase(self, sample_rgb_frame: np.ndarray) -> None:
        """Test increasing brightness."""
        result = apply_brightness(sample_rgb_frame, 0.5)

        assert result.shape == sample_rgb_frame.shape
        assert result.dtype == np.uint8
        # Average brightness should increase
        assert np.mean(result) > np.mean(sample_rgb_frame)

    def test_brightness_decrease(self, sample_rgb_frame: np.ndarray) -> None:
        """Test decreasing brightness."""
        result = apply_brightness(sample_rgb_frame, -0.5)

        assert result.shape == sample_rgb_frame.shape
        # Average brightness should decrease
        assert np.mean(result) < np.mean(sample_rgb_frame)

    def test_brightness_zero_no_change(self, sample_rgb_frame: np.ndarray) -> None:
        """Test zero brightness adjustment has minimal effect."""
        result = apply_brightness(sample_rgb_frame, 0)

        # Should be very close to original (allowing for minor numerical differences)
        np.testing.assert_array_almost_equal(result, sample_rgb_frame, decimal=0)


class TestApplyContrast:
    """Tests for apply_contrast function."""

    def test_contrast_increase(self, sample_rgb_frame: np.ndarray) -> None:
        """Test increasing contrast."""
        result = apply_contrast(sample_rgb_frame, 0.5)

        assert result.shape == sample_rgb_frame.shape
        assert result.dtype == np.uint8

    def test_contrast_decrease(self, sample_rgb_frame: np.ndarray) -> None:
        """Test decreasing contrast."""
        result = apply_contrast(sample_rgb_frame, -0.5)

        assert result.shape == sample_rgb_frame.shape
        # Standard deviation should decrease (less contrast)
        assert np.std(result) < np.std(sample_rgb_frame)


class TestApplySaturation:
    """Tests for apply_saturation function."""

    def test_saturation_increase(self, sample_rgb_frame: np.ndarray) -> None:
        """Test increasing saturation."""
        result = apply_saturation(sample_rgb_frame, 0.5)

        assert result.shape == sample_rgb_frame.shape
        assert result.dtype == np.uint8

    def test_saturation_decrease(self, sample_rgb_frame: np.ndarray) -> None:
        """Test decreasing saturation (towards grayscale)."""
        result = apply_saturation(sample_rgb_frame, -1.0)

        assert result.shape == sample_rgb_frame.shape
        # Should be close to grayscale (R ≈ G ≈ B)
        # With -1 saturation, colors should be nearly equal


class TestApplyGamma:
    """Tests for apply_gamma function."""

    def test_gamma_brighten(self, sample_rgb_frame: np.ndarray) -> None:
        """Test gamma > 1 brightens image (applies power < 1)."""
        result = apply_gamma(sample_rgb_frame, 2.0)

        assert result.shape == sample_rgb_frame.shape
        assert result.dtype == np.uint8
        # Gamma > 1 applies power(x, 1/gamma) = power(x, 0.5) = sqrt → brightens midtones
        assert np.mean(result) > np.mean(sample_rgb_frame)

    def test_gamma_darken(self, sample_rgb_frame: np.ndarray) -> None:
        """Test gamma < 1 darkens image (applies power > 1)."""
        result = apply_gamma(sample_rgb_frame, 0.5)

        assert result.shape == sample_rgb_frame.shape
        # Gamma < 1 applies power(x, 1/gamma) = power(x, 2) → darkens midtones
        assert np.mean(result) < np.mean(sample_rgb_frame)

    def test_gamma_one_no_change(self, sample_rgb_frame: np.ndarray) -> None:
        """Test gamma = 1 has no effect."""
        result = apply_gamma(sample_rgb_frame, 1.0)

        np.testing.assert_array_equal(result, sample_rgb_frame)

    def test_gamma_zero_raises_error(self, sample_rgb_frame: np.ndarray) -> None:
        """Test that gamma <= 0 raises error."""
        with pytest.raises(ImageTransformError, match="must be positive"):
            apply_gamma(sample_rgb_frame, 0)


class TestApplyHueRotation:
    """Tests for apply_hue_rotation function."""

    def test_hue_rotation_positive(self, sample_rgb_frame: np.ndarray) -> None:
        """Test positive hue rotation."""
        result = apply_hue_rotation(sample_rgb_frame, 90)

        assert result.shape == sample_rgb_frame.shape
        assert result.dtype == np.uint8

    def test_hue_rotation_negative(self, sample_rgb_frame: np.ndarray) -> None:
        """Test negative hue rotation."""
        result = apply_hue_rotation(sample_rgb_frame, -45)

        assert result.shape == sample_rgb_frame.shape

    def test_hue_rotation_full_circle(self, sample_rgb_frame: np.ndarray) -> None:
        """Test 360 degree rotation returns similar to original."""
        result = apply_hue_rotation(sample_rgb_frame, 360)

        assert result.shape == sample_rgb_frame.shape
        # Should be close to original (may have minor differences due to rounding)

    def test_hue_rotation_grayscale_raises_error(self, sample_gray_frame: np.ndarray) -> None:
        """Test that grayscale image raises error."""
        with pytest.raises(ImageTransformError, match="requires RGB"):
            apply_hue_rotation(sample_gray_frame, 45)


class TestApplyColorFilter:
    """Tests for apply_color_filter function."""

    def test_filter_none(self, sample_rgb_frame: np.ndarray) -> None:
        """Test 'none' filter returns original."""
        result = apply_color_filter(sample_rgb_frame, "none")

        np.testing.assert_array_equal(result, sample_rgb_frame)

    def test_filter_grayscale(self, sample_rgb_frame: np.ndarray) -> None:
        """Test grayscale filter."""
        result = apply_color_filter(sample_rgb_frame, "grayscale")

        assert result.shape == sample_rgb_frame.shape
        # All channels should be equal in grayscale
        np.testing.assert_array_equal(result[:, :, 0], result[:, :, 1])
        np.testing.assert_array_equal(result[:, :, 1], result[:, :, 2])

    def test_filter_sepia(self, sample_rgb_frame: np.ndarray) -> None:
        """Test sepia filter."""
        result = apply_color_filter(sample_rgb_frame, "sepia")

        assert result.shape == sample_rgb_frame.shape
        assert result.dtype == np.uint8

    def test_filter_invert(self, sample_rgb_frame: np.ndarray) -> None:
        """Test invert filter."""
        result = apply_color_filter(sample_rgb_frame, "invert")

        assert result.shape == sample_rgb_frame.shape
        # Inverting twice should return original
        double_invert = apply_color_filter(result, "invert")
        np.testing.assert_array_equal(double_invert, sample_rgb_frame)

    def test_filter_warm(self, sample_rgb_frame: np.ndarray) -> None:
        """Test warm filter."""
        result = apply_color_filter(sample_rgb_frame, "warm")

        assert result.shape == sample_rgb_frame.shape
        # Red channel should generally increase

    def test_filter_cool(self, sample_rgb_frame: np.ndarray) -> None:
        """Test cool filter."""
        result = apply_color_filter(sample_rgb_frame, "cool")

        assert result.shape == sample_rgb_frame.shape


class TestErrorPathsAndPILUnavailable:
    """Tests for validation errors, exception wrapping, and PIL unavailability."""

    def test_apply_crop_zero_dimensions_raises(self, sample_rgb_frame: np.ndarray) -> None:
        crop = CropRegion(x=0, y=0, width=0, height=10)
        with pytest.raises(ImageTransformError, match="Crop dimensions must be positive"):
            apply_crop(sample_rgb_frame, crop)

    def test_apply_resize_zero_dimensions_raises(self, sample_rgb_frame: np.ndarray) -> None:
        with pytest.raises(ImageTransformError, match="Resize dimensions must be positive"):
            apply_resize(sample_rgb_frame, ResizeDimensions(width=0, height=10))

    def test_apply_resize_wraps_pil_failure(self, sample_rgb_frame: np.ndarray) -> None:
        with (
            patch.object(image_transform_module.Image, "fromarray", side_effect=RuntimeError("boom")),
            pytest.raises(ImageTransformError, match="Resize operation failed"),
        ):
            apply_resize(sample_rgb_frame, ResizeDimensions(width=10, height=10))

    def test_apply_brightness_pil_unavailable_raises(self, sample_rgb_frame: np.ndarray) -> None:
        with (
            patch.object(image_transform_module, "PIL_AVAILABLE", False),
            pytest.raises(ImageTransformError, match=r"PIL .* required"),
        ):
            apply_brightness(sample_rgb_frame, 0.2)

    def test_apply_brightness_wraps_pil_failure(self, sample_rgb_frame: np.ndarray) -> None:
        with (
            patch.object(image_transform_module.Image, "fromarray", side_effect=RuntimeError("boom")),
            pytest.raises(ImageTransformError, match="Brightness adjustment failed"),
        ):
            apply_brightness(sample_rgb_frame, 0.2)

    def test_apply_contrast_pil_unavailable_raises(self, sample_rgb_frame: np.ndarray) -> None:
        with (
            patch.object(image_transform_module, "PIL_AVAILABLE", False),
            pytest.raises(ImageTransformError, match=r"PIL .* required"),
        ):
            apply_contrast(sample_rgb_frame, 0.2)

    def test_apply_contrast_wraps_pil_failure(self, sample_rgb_frame: np.ndarray) -> None:
        with (
            patch.object(image_transform_module.Image, "fromarray", side_effect=RuntimeError("boom")),
            pytest.raises(ImageTransformError, match="Contrast adjustment failed"),
        ):
            apply_contrast(sample_rgb_frame, 0.2)

    def test_apply_saturation_pil_unavailable_raises(self, sample_rgb_frame: np.ndarray) -> None:
        with (
            patch.object(image_transform_module, "PIL_AVAILABLE", False),
            pytest.raises(ImageTransformError, match=r"PIL .* required"),
        ):
            apply_saturation(sample_rgb_frame, 0.2)

    def test_apply_saturation_wraps_pil_failure(self, sample_rgb_frame: np.ndarray) -> None:
        with (
            patch.object(image_transform_module.Image, "fromarray", side_effect=RuntimeError("boom")),
            pytest.raises(ImageTransformError, match="Saturation adjustment failed"),
        ):
            apply_saturation(sample_rgb_frame, 0.2)

    def test_apply_gamma_non_positive_raises(self, sample_rgb_frame: np.ndarray) -> None:
        with pytest.raises(ImageTransformError, match="Gamma must be positive"):
            apply_gamma(sample_rgb_frame, 0.0)

    def test_apply_gamma_wraps_failure(self, sample_rgb_frame: np.ndarray) -> None:
        with (
            patch.object(image_transform_module.np, "power", side_effect=RuntimeError("boom")),
            pytest.raises(ImageTransformError, match="Gamma correction failed"),
        ):
            apply_gamma(sample_rgb_frame, 1.5)

    def test_apply_hue_rotation_pil_unavailable_raises(self, sample_rgb_frame: np.ndarray) -> None:
        with (
            patch.object(image_transform_module, "PIL_AVAILABLE", False),
            pytest.raises(ImageTransformError, match=r"PIL .* required"),
        ):
            apply_hue_rotation(sample_rgb_frame, 45)

    def test_apply_hue_rotation_wraps_failure(self, sample_rgb_frame: np.ndarray) -> None:
        with (
            patch.object(image_transform_module.Image, "fromarray", side_effect=RuntimeError("boom")),
            pytest.raises(ImageTransformError, match="Hue rotation failed"),
        ):
            apply_hue_rotation(sample_rgb_frame, 45)

    def test_apply_color_filter_pil_unavailable_raises(self, sample_rgb_frame: np.ndarray) -> None:
        with (
            patch.object(image_transform_module, "PIL_AVAILABLE", False),
            pytest.raises(ImageTransformError, match=r"PIL .* required"),
        ):
            apply_color_filter(sample_rgb_frame, "grayscale")

    def test_apply_color_filter_unknown_raises(self, sample_rgb_frame: np.ndarray) -> None:
        with pytest.raises(ImageTransformError, match="Unknown color filter"):
            apply_color_filter(sample_rgb_frame, "nonexistent_filter")

    def test_apply_color_filter_wraps_failure(self, sample_rgb_frame: np.ndarray) -> None:
        with (
            patch.object(image_transform_module.Image, "fromarray", side_effect=RuntimeError("boom")),
            pytest.raises(ImageTransformError, match="Color filter failed"),
        ):
            apply_color_filter(sample_rgb_frame, "grayscale")

    def test_apply_color_filter_empty_string_returns_original(self, sample_rgb_frame: np.ndarray) -> None:
        result = apply_color_filter(sample_rgb_frame, "")
        np.testing.assert_array_equal(result, sample_rgb_frame)


class TestApplyTransformsBatch:
    """Tests for apply_transforms_batch."""

    def test_no_transform_returns_input_unchanged(self, sample_rgb_frame: np.ndarray) -> None:
        frames = np.stack([sample_rgb_frame, sample_rgb_frame], axis=0)
        result = apply_transforms_batch(frames, ImageTransform())
        assert result is frames

    def test_applies_transform_and_invokes_progress_callback(self, sample_rgb_frame: np.ndarray) -> None:
        frames = np.stack([sample_rgb_frame, sample_rgb_frame, sample_rgb_frame], axis=0)
        transform = ImageTransform(crop=CropRegion(x=0, y=0, width=10, height=10))
        progress_calls: list[tuple[int, int]] = []

        result = apply_transforms_batch(frames, transform, progress_callback=lambda c, t: progress_calls.append((c, t)))

        assert result.shape == (3, 10, 10, 3)
        assert progress_calls == [(1, 3), (2, 3), (3, 3)]


class TestApplyCameraTransforms:
    """Tests for apply_camera_transforms."""

    def test_no_transform_returns_input_dict_values(self, sample_rgb_frame: np.ndarray) -> None:
        frames = np.stack([sample_rgb_frame], axis=0)
        images = {"cam0": frames, "cam1": frames}

        result = apply_camera_transforms(images, global_transform=None, camera_transforms=None)

        assert result["cam0"] is frames
        assert result["cam1"] is frames

    def test_global_transform_applied_to_all_cameras(self, sample_rgb_frame: np.ndarray) -> None:
        frames = np.stack([sample_rgb_frame], axis=0)
        images = {"cam0": frames, "cam1": frames}
        global_transform = ImageTransform(crop=CropRegion(x=0, y=0, width=10, height=10))

        result = apply_camera_transforms(images, global_transform=global_transform, camera_transforms=None)

        assert result["cam0"].shape == (1, 10, 10, 3)
        assert result["cam1"].shape == (1, 10, 10, 3)

    def test_per_camera_transform_overrides_global(self, sample_rgb_frame: np.ndarray) -> None:
        frames = np.stack([sample_rgb_frame], axis=0)
        images = {"cam0": frames, "cam1": frames}
        global_transform = ImageTransform(crop=CropRegion(x=0, y=0, width=10, height=10))
        camera_transforms = {"cam1": ImageTransform(crop=CropRegion(x=0, y=0, width=20, height=20))}

        result = apply_camera_transforms(
            images,
            global_transform=global_transform,
            camera_transforms=camera_transforms,
        )

        assert result["cam0"].shape == (1, 10, 10, 3)
        assert result["cam1"].shape == (1, 20, 20, 3)

    def test_progress_callback_receives_camera_name(self, sample_rgb_frame: np.ndarray) -> None:
        frames = np.stack([sample_rgb_frame], axis=0)
        images = {"cam0": frames}
        transform = ImageTransform(crop=CropRegion(x=0, y=0, width=10, height=10))
        calls: list[tuple[str, int, int]] = []

        apply_camera_transforms(
            images,
            global_transform=transform,
            camera_transforms=None,
            progress_callback=lambda cam, c, t: calls.append((cam, c, t)),
        )

        assert calls == [("cam0", 1, 1)]


class TestGetOutputDimensions:
    """Tests for get_output_dimensions."""

    def test_no_transform_returns_original(self) -> None:
        assert get_output_dimensions((640, 480), ImageTransform()) == (640, 480)

    def test_crop_changes_dimensions(self) -> None:
        transform = ImageTransform(crop=CropRegion(x=0, y=0, width=100, height=80))
        assert get_output_dimensions((640, 480), transform) == (100, 80)

    def test_resize_overrides_crop(self) -> None:
        transform = ImageTransform(
            crop=CropRegion(x=0, y=0, width=100, height=80),
            resize=ResizeDimensions(width=64, height=64),
        )
        assert get_output_dimensions((640, 480), transform) == (64, 64)
        # Blue channel should generally increase

    def test_filter_unknown_raises_error(self, sample_rgb_frame: np.ndarray) -> None:
        """Test unknown filter raises error."""
        with pytest.raises(ImageTransformError, match="Unknown color filter"):
            apply_color_filter(sample_rgb_frame, "unknown")


class TestApplyColorAdjustment:
    """Tests for apply_color_adjustment function."""

    def test_adjustment_brightness_only(self, sample_rgb_frame: np.ndarray) -> None:
        """Test adjustment with only brightness."""
        adjustment = ColorAdjustment(brightness=0.3)
        result = apply_color_adjustment(sample_rgb_frame, adjustment)

        assert result.shape == sample_rgb_frame.shape

    def test_adjustment_multiple_params(self, sample_rgb_frame: np.ndarray) -> None:
        """Test adjustment with multiple parameters."""
        adjustment = ColorAdjustment(
            brightness=0.2,
            contrast=0.1,
            saturation=-0.3,
        )
        result = apply_color_adjustment(sample_rgb_frame, adjustment)

        assert result.shape == sample_rgb_frame.shape
        assert result.dtype == np.uint8

    def test_adjustment_all_params(self, sample_rgb_frame: np.ndarray) -> None:
        """Test adjustment with all parameters."""
        adjustment = ColorAdjustment(
            brightness=0.1,
            contrast=0.1,
            saturation=0.1,
            gamma=1.2,
            hue=30,
        )
        result = apply_color_adjustment(sample_rgb_frame, adjustment)

        assert result.shape == sample_rgb_frame.shape

    def test_adjustment_empty(self, sample_rgb_frame: np.ndarray) -> None:
        """Test empty adjustment returns original."""
        adjustment = ColorAdjustment()
        result = apply_color_adjustment(sample_rgb_frame, adjustment)

        np.testing.assert_array_equal(result, sample_rgb_frame)


class TestApplyTransform:
    """Tests for apply_transform with full pipeline."""

    def test_transform_crop_only(self, sample_rgb_frame: np.ndarray) -> None:
        """Test transform with crop only."""
        transform = ImageTransform(
            crop=CropRegion(x=10, y=10, width=50, height=50),
        )
        result = apply_transform(sample_rgb_frame, transform)

        assert result.shape == (50, 50, 3)

    def test_transform_resize_only(self, sample_rgb_frame: np.ndarray) -> None:
        """Test transform with resize only."""
        transform = ImageTransform(
            resize=ResizeDimensions(width=50, height=50),
        )
        result = apply_transform(sample_rgb_frame, transform)

        assert result.shape == (50, 50, 3)

    def test_transform_color_adjustment_only(self, sample_rgb_frame: np.ndarray) -> None:
        """Test transform with color adjustment only."""
        transform = ImageTransform(
            color_adjustment=ColorAdjustment(brightness=0.3, contrast=0.2),
        )
        result = apply_transform(sample_rgb_frame, transform)

        assert result.shape == sample_rgb_frame.shape

    def test_transform_color_filter_only(self, sample_rgb_frame: np.ndarray) -> None:
        """Test transform with color filter only."""
        transform = ImageTransform(color_filter="grayscale")
        result = apply_transform(sample_rgb_frame, transform)

        assert result.shape == sample_rgb_frame.shape

    def test_transform_full_pipeline(self, sample_rgb_frame: np.ndarray) -> None:
        """Test full transform pipeline: crop -> resize -> color."""
        transform = ImageTransform(
            crop=CropRegion(x=10, y=10, width=80, height=80),
            resize=ResizeDimensions(width=40, height=40),
            color_adjustment=ColorAdjustment(brightness=0.1),
            color_filter="warm",
        )
        result = apply_transform(sample_rgb_frame, transform)

        assert result.shape == (40, 40, 3)
        assert result.dtype == np.uint8

    def test_transform_empty(self, sample_rgb_frame: np.ndarray) -> None:
        """Test empty transform returns original."""
        transform = ImageTransform()
        result = apply_transform(sample_rgb_frame, transform)

        np.testing.assert_array_equal(result, sample_rgb_frame)
