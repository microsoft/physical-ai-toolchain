"""Shared path utilities for mapping dataset IDs to storage paths."""


def dataset_id_to_blob_prefix(dataset_id: str) -> str:
    """Convert a --separated dataset ID to a /-separated blob prefix."""
    return dataset_id.replace("--", "/")
