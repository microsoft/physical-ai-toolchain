"""
Storage adapters for annotation persistence and dataset file access.

Provides backends for saving/retrieving episode annotations and
reading dataset files across local and Azure Blob Storage providers.
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


def get_blob_dataset_provider():
    """Get the BlobDatasetProvider for dataset file access (requires azure-storage-blob)."""
    from .blob_dataset import BlobDatasetProvider

    return BlobDatasetProvider


def get_huggingface_adapter():
    """Get the Hugging Face Hub adapter (requires huggingface_hub)."""
    from .huggingface import HuggingFaceHubAdapter

    return HuggingFaceHubAdapter
