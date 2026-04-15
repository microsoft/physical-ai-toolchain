"""Tests for the shared rate limiter module."""

from __future__ import annotations


class TestSharedRateLimiter:
    """Verify a single Limiter instance is shared across main and detection."""

    def test_given_shared_module_when_imported_from_main_and_detection_then_same_instance(self):
        # Act
        from src.api.main import limiter as main_limiter
        from src.api.routers.detection import limiter as detection_limiter

        # Assert
        assert main_limiter is detection_limiter

    def test_given_rate_limiter_module_when_imported_then_has_limiter_attribute(self):
        # Act
        from src.api.rate_limiter import limiter

        # Assert
        assert limiter is not None

    def test_given_rate_limiter_module_when_imported_then_uses_get_remote_address(self):
        # Act
        from slowapi.util import get_remote_address

        from src.api.rate_limiter import limiter

        # Assert
        assert limiter._key_func is get_remote_address
