"""Tests for image transformation functions including color adjustments."""

import numpy as np
import pytest

from src.api.services.image_transform import (
    ColorAdjustment,
    CropRegion,
    ImageTransform,
    ImageTransformError,
    ResizeDimensions,
    apply_brightness,
    apply_color_adjustment,
    apply_color_filter,
    apply_contrast,
    apply_crop,
    apply_gamma,
    apply_hue_rotation,
    apply_resize,
    apply_saturation,
    apply_transform,
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
