"""
Dataset service package.

Re-exports DatasetService, get_dataset_service, and _dataset_service
for backward-compatible imports.

Tests reset the singleton via:
    import src.api.services.dataset_service as ds_mod
    ds_mod._dataset_service = None

To support this pattern, __getattr__ and __setattr__-style module
attribute access is proxied to the service submodule.
"""

import sys
import types

from .service import DatasetService, get_dataset_service

__all__ = [
    "DatasetService",
    "get_dataset_service",
]

# Proxy _dataset_service reads/writes to the service submodule so that
# ``ds_mod._dataset_service = None`` propagates correctly.
_this = sys.modules[__name__]
_service_mod = sys.modules[f"{__name__}.service"]


class _ModuleProxy(types.ModuleType):
    """Thin proxy so attribute writes to the package reach service.py."""

    def __getattr__(self, name: str):
        if name == "_dataset_service":
            return _service_mod._dataset_service
        raise AttributeError(name)

    def __setattr__(self, name: str, value):
        if name == "_dataset_service":
            _service_mod._dataset_service = value
            return
        super().__setattr__(name, value)


# Install the proxy — keep all existing module attributes intact
_proxy = _ModuleProxy(__name__)
_proxy.__dict__.update({k: v for k, v in _this.__dict__.items() if k != "__class__"})
sys.modules[__name__] = _proxy
