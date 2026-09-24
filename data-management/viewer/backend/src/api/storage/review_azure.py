"""Azure Blob persistence for immutable review records."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any, TypeVar

from pydantic import BaseModel

from ..models.releases import OperationalEvent, canonical_json_bytes
from ..models.reviews import AnnotationRevision, EditRevision, QualityReport, ReviewDecision, SourceIdentity
from .review_base import DuplicateReviewRecordError, ReviewStorageError, validate_review_identifier

try:
    from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
    from azure.identity.aio import DefaultAzureCredential
    from azure.storage.blob.aio import BlobServiceClient
except ImportError:

    class ResourceExistsError(Exception):
        """Sentinel used when the Azure SDK is unavailable."""

    class ResourceNotFoundError(Exception):
        """Sentinel used when the Azure SDK is unavailable."""

    DefaultAzureCredential = None
    BlobServiceClient = None


_RecordT = TypeVar("_RecordT", bound=BaseModel)


class AzureReviewRepository:
    """Store immutable review records under a configured Blob export prefix."""

    def __init__(
        self,
        *,
        export_prefix: str,
        container_client: Any | None = None,
        account_name: str | None = None,
        container_name: str | None = None,
        sas_token: str | None = None,
    ) -> None:
        if container_client is None and (not account_name or not container_name):
            raise ValueError("account_name and container_name are required without an injected container client")
        if container_client is None and BlobServiceClient is None:
            raise ImportError("Azure review persistence requires azure-storage-blob and azure-identity")
        self._container = container_client
        self._account_name = account_name
        self._container_name = container_name
        self._sas_token = sas_token
        self._client = None
        self._prefix = self._validate_prefix(export_prefix)

    async def create_annotation_revision(self, revision: AnnotationRevision) -> None:
        await self._create_record("annotations", revision.revision_id, revision)

    async def get_annotation_revision(self, revision_id: str) -> AnnotationRevision | None:
        return await self._get_record("annotations", revision_id, AnnotationRevision)

    async def list_annotation_revisions(self, source: SourceIdentity) -> list[AnnotationRevision]:
        records = await self._list_records("annotations", AnnotationRevision, source)
        return sorted((record for record in records if record.source == source), key=lambda record: record.created_at)

    async def create_edit_revision(self, revision: EditRevision) -> None:
        await self._create_record("edits", revision.revision_id, revision)

    async def get_edit_revision(self, revision_id: str) -> EditRevision | None:
        return await self._get_record("edits", revision_id, EditRevision)

    async def list_edit_revisions(self, source: SourceIdentity) -> list[EditRevision]:
        records = await self._list_records("edits", EditRevision, source)
        return sorted((record for record in records if record.source == source), key=lambda record: record.created_at)

    async def create_quality_report(self, report: QualityReport) -> None:
        await self._create_record("quality", report.run_id, report)

    async def get_quality_report(self, run_id: str) -> QualityReport | None:
        return await self._get_record("quality", run_id, QualityReport)

    async def list_quality_reports(self, dataset_id: str, episode_index: int) -> list[QualityReport]:
        validated_dataset_id = validate_review_identifier(dataset_id)
        prefix = f"{self._prefix}/reviews/{validated_dataset_id}/episodes/episode-{episode_index:06d}/quality/"
        records: list[QualityReport] = []
        try:
            container = await self._get_container()
            async for item in container.list_blobs(name_starts_with=prefix):
                download = await container.get_blob_client(item.name).download_blob()
                records.append(QualityReport.model_validate_json(await download.readall()))
            return records
        except Exception as exc:
            raise ReviewStorageError(f"Failed to list quality review records: {exc}") from exc

    async def create_decision(self, decision: ReviewDecision) -> None:
        await self._create_record("decisions", decision.decision_id, decision)

    async def get_decision(self, decision_id: str) -> ReviewDecision | None:
        return await self._get_record("decisions", decision_id, ReviewDecision)

    async def list_decisions(self, dataset_id: str, episode_index: int) -> list[ReviewDecision]:
        validated_dataset_id = validate_review_identifier(dataset_id)
        prefix = f"{self._prefix}/reviews/{validated_dataset_id}/episodes/episode-{episode_index:06d}/decisions/"
        records: list[ReviewDecision] = []
        try:
            container = await self._get_container()
            async for item in container.list_blobs(name_starts_with=prefix):
                download = await container.get_blob_client(item.name).download_blob()
                records.append(ReviewDecision.model_validate_json(await download.readall()))
            return records
        except Exception as exc:
            raise ReviewStorageError(f"Failed to list review decision records: {exc}") from exc

    async def append_event(self, event: OperationalEvent) -> None:
        operation_id = validate_review_identifier(event.operation_id)
        payload = canonical_json_bytes(event)
        digest = hashlib.sha256(payload).hexdigest()[:16]
        timestamp = event.timestamp.strftime("%Y%m%dT%H%M%S.%fZ")
        path = f"{self._prefix}/events/{operation_id}/{timestamp}-{digest}.jsonl"
        await self._upload_create_only(path, payload, operation_id)

    async def _create_record(
        self,
        kind: str,
        record_id: str,
        record: AnnotationRevision | EditRevision | QualityReport | ReviewDecision,
    ) -> None:
        validated_id = validate_review_identifier(record_id)
        path = self._record_path(record.source, kind, validated_id)
        await self._upload_create_only(path, canonical_json_bytes(record), validated_id)

    async def _upload_create_only(self, path: str, payload: bytes, record_id: str) -> None:
        container = await self._get_container()
        blob = container.get_blob_client(path)
        try:
            await blob.upload_blob(payload, overwrite=False)
        except ResourceExistsError as exc:
            raise DuplicateReviewRecordError(f"Review record already exists: {record_id}") from exc
        except Exception as exc:
            raise ReviewStorageError(f"Failed to create review record {record_id}: {exc}") from exc

    async def _get_record(self, kind: str, record_id: str, model: type[_RecordT]) -> _RecordT | None:
        validated_id = validate_review_identifier(record_id)
        suffix = f"/{kind}/{validated_id}.json"
        try:
            container = await self._get_container()
            matches = [
                item.name
                async for item in container.list_blobs(name_starts_with=f"{self._prefix}/reviews/")
                if item.name.endswith(suffix)
            ]
            if not matches:
                return None
            if len(matches) > 1:
                raise ReviewStorageError(f"Review record identifier is not unique: {validated_id}")
            download = await container.get_blob_client(matches[0]).download_blob()
            return model.model_validate_json(await download.readall())
        except ResourceNotFoundError:
            return None
        except Exception as exc:
            raise ReviewStorageError(f"Failed to read review record {validated_id}: {exc}") from exc

    async def _list_records(self, kind: str, model: type[_RecordT], source: SourceIdentity) -> list[_RecordT]:
        prefix = self._record_path(source, kind, "placeholder").rsplit("/", 1)[0] + "/"
        records: list[_RecordT] = []
        try:
            container = await self._get_container()
            async for item in container.list_blobs(name_starts_with=prefix):
                blob = container.get_blob_client(item.name)
                download = await blob.download_blob()
                records.append(model.model_validate_json(await download.readall()))
            return records
        except Exception as exc:
            raise ReviewStorageError(f"Failed to list {kind} review records: {exc}") from exc

    def _record_path(self, source: SourceIdentity, kind: str, record_id: str) -> str:
        dataset_id = validate_review_identifier(source.dataset_id)
        return (
            f"{self._prefix}/reviews/{dataset_id}/episodes/episode-{source.episode_index:06d}/{kind}/{record_id}.json"
        )

    async def _get_container(self) -> Any:
        if self._container is not None:
            return self._container
        if self._client is None:
            account_url = f"https://{self._account_name}.blob.core.windows.net"
            credential = self._sas_token if self._sas_token else DefaultAzureCredential()
            self._client = BlobServiceClient(account_url=account_url, credential=credential)
        self._container = self._client.get_container_client(self._container_name)
        return self._container

    @staticmethod
    def _validate_prefix(value: str) -> str:
        path = PurePosixPath(value)
        if not value or path.is_absolute() or "\\" in value or ".." in path.parts:
            raise ReviewStorageError("Invalid dataset export prefix")
        return path.as_posix().rstrip("/")
