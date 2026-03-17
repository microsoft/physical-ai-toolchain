"""
Image transformation service for frame editing operations.

Provides crop and resize functions for NumPy image arrays using
PIL for high-quality resizing with LANCZOS interpolation.
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

# PIL is an optional dependency for export
try:
    from PIL import Image, ImageEnhance

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


@dataclass
class CropRegion:
    """Crop region definition in pixel coordinates."""

    x: int
    """X offset from left edge."""
    y: int
    """Y offset from top edge."""
    width: int
    """Width of crop region."""
    height: int
    """Height of crop region."""


@dataclass
class ResizeDimensions:
    """Target resize dimensions."""

    width: int
    """Target width in pixels."""
    height: int
    """Target height in pixels."""


@dataclass
class ColorAdjustment:
    """Color adjustment parameters for image processing."""

    brightness: float | None = None
    """Brightness adjustment (-1 to 1, 0 = no change)."""
    contrast: float | None = None
    """Contrast adjustment (-1 to 1, 0 = no change)."""
    saturation: float | None = None
    """Saturation adjustment (-1 to 1, 0 = no change)."""
    gamma: float | None = None
    """Gamma correction (0.1 to 3.0, 1 = no change)."""
    hue: float | None = None
    """Hue rotation in degrees (-180 to 180)."""


# Predefined color filter presets
ColorFilterPreset = str  # 'none' | 'grayscale' | 'sepia' | 'invert' | 'warm' | 'cool'


@dataclass
class ImageTransform:
    """Combined image transform operations."""

    crop: CropRegion | None = None
    """Crop region to apply first."""
    resize: ResizeDimensions | None = None
    """Resize dimensions to apply after crop."""
    color_adjustment: ColorAdjustment | None = None
    """Color adjustment parameters."""
    color_filter: ColorFilterPreset | None = None
    """Predefined color filter preset."""


class ImageTransformError(Exception):
    """Exception raised for image transformation failures."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


def apply_crop(
    frame: NDArray[np.uint8],
    crop: CropRegion,
) -> NDArray[np.uint8]:
    """
    Apply crop to a single frame.

    Args:
        frame: Image array of shape (H, W, C) or (H, W).
        crop: Crop region to extract.

    Returns:
        Cropped image array.

    Raises:
        ImageTransformError: If crop region is invalid.
    """
    h, w = frame.shape[:2]

    # Validate crop bounds
    if crop.x < 0 or crop.y < 0:
        raise ImageTransformError(f"Crop offset cannot be negative: ({crop.x}, {crop.y})")
    if crop.x + crop.width > w:
        raise ImageTransformError(f"Crop width exceeds image bounds: {crop.x} + {crop.width} > {w}")
    if crop.y + crop.height > h:
        raise ImageTransformError(f"Crop height exceeds image bounds: {crop.y} + {crop.height} > {h}")
    if crop.width <= 0 or crop.height <= 0:
        raise ImageTransformError(f"Crop dimensions must be positive: {crop.width}x{crop.height}")

    # Apply crop using NumPy slicing
    return frame[crop.y : crop.y + crop.height, crop.x : crop.x + crop.width]


def apply_resize(
    frame: NDArray[np.uint8],
    size: ResizeDimensions,
) -> NDArray[np.uint8]:
    """
    Apply resize to a single frame using PIL LANCZOS interpolation.

    Args:
        frame: Image array of shape (H, W, C) or (H, W).
        size: Target dimensions.

    Returns:
        Resized image array.

    Raises:
        ImageTransformError: If PIL is not available or resize fails.
    """
    if not PIL_AVAILABLE:
        raise ImageTransformError("PIL (Pillow) is required for resize operations. Install with: pip install Pillow")

    if size.width <= 0 or size.height <= 0:
        raise ImageTransformError(f"Resize dimensions must be positive: {size.width}x{size.height}")

    try:
        # Convert to PIL Image
        pil_image = Image.fromarray(frame)

        # Resize with high-quality LANCZOS interpolation
        resized = pil_image.resize(
            (size.width, size.height),
            resample=Image.Resampling.LANCZOS,
        )

        # Convert back to NumPy array
        return np.asarray(resized, dtype=np.uint8)

    except Exception as e:
        raise ImageTransformError(f"Resize operation failed: {e}", cause=e)


def apply_brightness(
    frame: NDArray[np.uint8],
    factor: float,
) -> NDArray[np.uint8]:
    """
    Apply brightness adjustment to a single frame.

    Args:
        frame: Image array of shape (H, W, C) or (H, W).
        factor: Brightness adjustment (-1 to 1, 0 = no change).

    Returns:
        Brightness-adjusted image array.

    Raises:
        ImageTransformError: If PIL is not available.
    """
    if not PIL_AVAILABLE:
        raise ImageTransformError("PIL (Pillow) is required for color operations. Install with: pip install Pillow")

    try:
        pil_image = Image.fromarray(frame)
        enhancer = ImageEnhance.Brightness(pil_image)
        # Convert -1 to 1 range to PIL's 0 to 2 range (0 = black, 1 = original, 2 = bright)
        enhanced = enhancer.enhance(1 + factor)
        return np.asarray(enhanced, dtype=np.uint8)
    except Exception as e:
        raise ImageTransformError(f"Brightness adjustment failed: {e}", cause=e)


def apply_contrast(
    frame: NDArray[np.uint8],
    factor: float,
) -> NDArray[np.uint8]:
    """
    Apply contrast adjustment to a single frame.

    Args:
        frame: Image array of shape (H, W, C) or (H, W).
        factor: Contrast adjustment (-1 to 1, 0 = no change).

    Returns:
        Contrast-adjusted image array.

    Raises:
        ImageTransformError: If PIL is not available.
    """
    if not PIL_AVAILABLE:
        raise ImageTransformError("PIL (Pillow) is required for color operations. Install with: pip install Pillow")

    try:
        pil_image = Image.fromarray(frame)
        enhancer = ImageEnhance.Contrast(pil_image)
        # Convert -1 to 1 range to PIL's 0 to 2 range
        enhanced = enhancer.enhance(1 + factor)
        return np.asarray(enhanced, dtype=np.uint8)
    except Exception as e:
        raise ImageTransformError(f"Contrast adjustment failed: {e}", cause=e)


def apply_saturation(
    frame: NDArray[np.uint8],
    factor: float,
) -> NDArray[np.uint8]:
    """
    Apply saturation adjustment to a single frame.

    Args:
        frame: Image array of shape (H, W, C) or (H, W).
        factor: Saturation adjustment (-1 to 1, 0 = no change).

    Returns:
        Saturation-adjusted image array.

    Raises:
        ImageTransformError: If PIL is not available.
    """
    if not PIL_AVAILABLE:
        raise ImageTransformError("PIL (Pillow) is required for color operations. Install with: pip install Pillow")

    try:
        pil_image = Image.fromarray(frame)
        enhancer = ImageEnhance.Color(pil_image)
        # Convert -1 to 1 range to PIL's 0 to 2 range
        enhanced = enhancer.enhance(1 + factor)
        return np.asarray(enhanced, dtype=np.uint8)
    except Exception as e:
        raise ImageTransformError(f"Saturation adjustment failed: {e}", cause=e)


def apply_gamma(
    frame: NDArray[np.uint8],
    gamma: float,
) -> NDArray[np.uint8]:
    """
    Apply gamma correction to a single frame.

    Args:
        frame: Image array of shape (H, W, C) or (H, W).
        gamma: Gamma value (0.1 to 3.0, 1 = no change).
               Values > 1 brighten midtones, values < 1 darken midtones.
               Applies: output = input^(1/gamma)

    Returns:
        Gamma-corrected image array.

    Raises:
        ImageTransformError: If gamma value is invalid.
    """
    if gamma <= 0:
        raise ImageTransformError(f"Gamma must be positive: {gamma}")

    try:
        # Normalize to 0-1, apply gamma, scale back to 0-255
        normalized = frame.astype(np.float32) / 255.0
        corrected = np.power(normalized, 1.0 / gamma)
        return (corrected * 255).clip(0, 255).astype(np.uint8)
    except Exception as e:
        raise ImageTransformError(f"Gamma correction failed: {e}", cause=e)


def apply_hue_rotation(
    frame: NDArray[np.uint8],
    degrees: float,
) -> NDArray[np.uint8]:
    """
    Apply hue rotation to a single frame.

    Args:
        frame: Image array of shape (H, W, C) with RGB channels.
        degrees: Hue rotation in degrees (-180 to 180).

    Returns:
        Hue-rotated image array.

    Raises:
        ImageTransformError: If PIL is not available or operation fails.
    """
    if not PIL_AVAILABLE:
        raise ImageTransformError("PIL (Pillow) is required for color operations. Install with: pip install Pillow")

    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ImageTransformError(f"Hue rotation requires RGB image, got shape: {frame.shape}")

    try:
        pil_image = Image.fromarray(frame)
        # Convert to HSV
        hsv_image = pil_image.convert("HSV")
        hsv_array = np.asarray(hsv_image, dtype=np.int16)

        # Rotate hue channel (0-255 maps to 0-360 degrees)
        hue_shift = int((degrees / 360.0) * 255)
        hsv_array[:, :, 0] = (hsv_array[:, :, 0] + hue_shift) % 256

        # Convert back to RGB
        hsv_rotated = Image.fromarray(hsv_array.astype(np.uint8), mode="HSV")
        rgb_result = hsv_rotated.convert("RGB")
        return np.asarray(rgb_result, dtype=np.uint8)
    except Exception as e:
        raise ImageTransformError(f"Hue rotation failed: {e}", cause=e)


def apply_color_filter(
    frame: NDArray[np.uint8],
    filter_name: str,
) -> NDArray[np.uint8]:
    """
    Apply a predefined color filter to a single frame.

    Args:
        frame: Image array of shape (H, W, C) or (H, W).
        filter_name: One of 'grayscale', 'sepia', 'invert', 'warm', 'cool'.

    Returns:
        Filtered image array.

    Raises:
        ImageTransformError: If filter is unknown or operation fails.
    """
    if filter_name == "none" or not filter_name:
        return frame

    if not PIL_AVAILABLE:
        raise ImageTransformError("PIL (Pillow) is required for color operations. Install with: pip install Pillow")

    try:
        if filter_name == "grayscale":
            pil_image = Image.fromarray(frame)
            gray = pil_image.convert("L").convert("RGB")
            return np.asarray(gray, dtype=np.uint8)

        elif filter_name == "sepia":
            # Sepia transformation matrix
            sepia_matrix = np.array(
                [
                    [0.393, 0.769, 0.189],
                    [0.349, 0.686, 0.168],
                    [0.272, 0.534, 0.131],
                ]
            )
            sepia_frame = frame.astype(np.float32)
            # Apply sepia transformation
            result = np.dot(sepia_frame[..., :3], sepia_matrix.T)
            return result.clip(0, 255).astype(np.uint8)

        elif filter_name == "invert":
            return (255 - frame).astype(np.uint8)

        elif filter_name == "warm":
            # Increase red/yellow tones
            result = frame.astype(np.float32)
            result[:, :, 0] = np.clip(result[:, :, 0] * 1.1, 0, 255)  # Red
            result[:, :, 1] = np.clip(result[:, :, 1] * 1.05, 0, 255)  # Green
            result[:, :, 2] = np.clip(result[:, :, 2] * 0.9, 0, 255)  # Blue
            return result.astype(np.uint8)

        elif filter_name == "cool":
            # Increase blue tones
            result = frame.astype(np.float32)
            result[:, :, 0] = np.clip(result[:, :, 0] * 0.9, 0, 255)  # Red
            result[:, :, 1] = np.clip(result[:, :, 1] * 0.95, 0, 255)  # Green
            result[:, :, 2] = np.clip(result[:, :, 2] * 1.1, 0, 255)  # Blue
            return result.astype(np.uint8)

        else:
            raise ImageTransformError(f"Unknown color filter: {filter_name}")

    except ImageTransformError:
        raise
    except Exception as e:
        raise ImageTransformError(f"Color filter failed: {e}", cause=e)


def apply_color_adjustment(
    frame: NDArray[np.uint8],
    adjustment: ColorAdjustment,
) -> NDArray[np.uint8]:
    """
    Apply all color adjustments to a single frame.

    Adjustments are applied in order: brightness, contrast, saturation, gamma, hue.

    Args:
        frame: Image array of shape (H, W, C) or (H, W).
        adjustment: Color adjustment parameters.

    Returns:
        Color-adjusted image array.
    """
    result = frame

    if adjustment.brightness is not None and adjustment.brightness != 0:
        result = apply_brightness(result, adjustment.brightness)

    if adjustment.contrast is not None and adjustment.contrast != 0:
        result = apply_contrast(result, adjustment.contrast)

    if adjustment.saturation is not None and adjustment.saturation != 0:
        result = apply_saturation(result, adjustment.saturation)

    if adjustment.gamma is not None and adjustment.gamma != 1.0:
        result = apply_gamma(result, adjustment.gamma)

    if adjustment.hue is not None and adjustment.hue != 0:
        result = apply_hue_rotation(result, adjustment.hue)

    return result


def apply_transform(
    frame: NDArray[np.uint8],
    transform: ImageTransform,
) -> NDArray[np.uint8]:
    """
    Apply a complete transform (crop, resize, color) to a single frame.

    Args:
        frame: Image array of shape (H, W, C) or (H, W).
        transform: Transform to apply.

    Returns:
        Transformed image array.
    """
    result = frame

    if transform.crop:
        result = apply_crop(result, transform.crop)

    if transform.resize:
        result = apply_resize(result, transform.resize)

    if transform.color_adjustment:
        result = apply_color_adjustment(result, transform.color_adjustment)

    if transform.color_filter:
        result = apply_color_filter(result, transform.color_filter)

    return result


def apply_transforms_batch(
    frames: NDArray[np.uint8],
    transform: ImageTransform,
    progress_callback: Callable[[int, int], None] | None = None,
) -> NDArray[np.uint8]:
    """
    Apply transform to a batch of frames.

    Args:
        frames: Image array of shape (N, H, W, C).
        transform: Transform to apply to all frames.
        progress_callback: Optional callback(current, total) for progress.

    Returns:
        Transformed frames array.
    """
    # Check if any transform is specified
    has_transform = (
        transform.crop is not None
        or transform.resize is not None
        or transform.color_adjustment is not None
        or transform.color_filter is not None
    )
    if not has_transform:
        return frames

    n_frames = len(frames)
    results = []

    for i, frame in enumerate(frames):
        result = apply_transform(frame, transform)
        results.append(result)

        if progress_callback:
            progress_callback(i + 1, n_frames)

    return np.stack(results, axis=0)


def apply_camera_transforms(
    images: dict[str, NDArray[np.uint8]],
    global_transform: ImageTransform | None,
    camera_transforms: dict[str, ImageTransform] | None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> dict[str, NDArray[np.uint8]]:
    """
    Apply transforms to all camera images.

    Per-camera transforms override the global transform for that camera.

    Args:
        images: Dict of camera name to image array (N, H, W, C).
        global_transform: Transform to apply to all cameras by default.
        camera_transforms: Per-camera transform overrides.
        progress_callback: Optional callback(camera, current, total) for progress.

    Returns:
        Dict of camera name to transformed image array.
    """
    camera_transforms = camera_transforms or {}
    results = {}

    def make_camera_progress(cam: str) -> Callable[[int, int], None]:
        def camera_progress(current: int, total: int) -> None:
            if progress_callback:
                progress_callback(cam, current, total)

        return camera_progress

    for camera, frames in images.items():
        # Use camera-specific transform if available, else global
        transform = camera_transforms.get(camera, global_transform)

        if transform:
            results[camera] = apply_transforms_batch(frames, transform, make_camera_progress(camera))
        else:
            results[camera] = frames

    return results


def get_output_dimensions(
    original_size: tuple[int, int],
    transform: ImageTransform,
) -> tuple[int, int]:
    """
    Calculate output dimensions after applying a transform.

    Args:
        original_size: Original (width, height).
        transform: Transform to apply.

    Returns:
        Output (width, height) after transform.
    """
    width, height = original_size

    if transform.crop:
        width = transform.crop.width
        height = transform.crop.height

    if transform.resize:
        width = transform.resize.width
        height = transform.resize.height

    return (width, height)
