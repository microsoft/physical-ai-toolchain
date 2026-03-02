"""
Storage adapters for annotation persistence.

This module provides storage backends for saving and retrieving
episode annotations across different storage providers.
"""

from .base import StorageAdapter, StorageError
from .local import LocalStorageAdapter

__all__ = [
    "LocalStorageAdapter",
    "StorageAdapter",
    "StorageError",
]


# Lazy imports for optional dependencies
def get_azure_adapter():
    """Get the Azure Blob Storage adapter (requires azure-storage-blob)."""
    from .azure import AzureBlobStorageAdapter

    return AzureBlobStorageAdapter


def get_huggingface_adapter():
    """Get the Hugging Face Hub adapter (requires huggingface_hub)."""
    from .huggingface import HuggingFaceHubAdapter

    return HuggingFaceHubAdapter
