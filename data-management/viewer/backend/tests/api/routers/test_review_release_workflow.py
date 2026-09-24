"""End-to-end API contract tests for review and release workflows."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.models.release_workflow import ReleaseSubmitRequest
from src.api.models.releases import PackageQualityReport, QualityEvidenceReference, ReleaseFormat, ReleaseManifest
from src.api.models.reviews import QualityOutcome, SourceFileIdentity, SourceIdentity
from src.api.release.jobs import ReleaseJobStore
from src.api.release.local_publisher import LocalReleasePublisher
from src.api.release.processor import ReleaseAssembly, ReleaseProcessor
from src.api.services.release_workflow_service import ReleaseWorkflowService, get_release_workflow_service
from src.api.services.review_workflow_service import ReviewWorkflowService, get_review_workflow_service
from src.api.storage.review_local import LocalReviewRepository


@pytest.fixture
def workflow_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, ReleaseWorkflowService, SourceIdentity]:
    from src.api import config as config_module
    from src.api.main import app
    from src.api.release import processor as processor_module
    from src.api.services import release_workflow_service as release_service_module

    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "source"))
    monkeypatch.setenv("DATAVIEWER_RELEASE_ROOT", str(tmp_path / "runtime"))
    config_module._app_config = None
    processor_module._release_processor = None
    release_service_module._release_workflow_service = None

    repository = LocalReviewRepository(tmp_path / "release", source_roots=(tmp_path / "source",))
    source = SourceIdentity(
        dataset_id="dataset-1",
        episode_index=0,
        source_format="lerobot",
        format_version="3.0",
        source_digest="a" * 64,
        files=(SourceFileIdentity(relative_path="meta/info.json", size_bytes=2, sha256="b" * 64),),
    )

    async def resolve_current(identity: SourceIdentity) -> SourceIdentity:
        return identity

    review_service = ReviewWorkflowService(repository)
    jobs = ReleaseJobStore(tmp_path / "jobs")
    release_service = ReleaseWorkflowService(repository, jobs, resolve_current)

    async def assemble(
        request: ReleaseSubmitRequest,
        _selections,
        staging_root: Path,
    ) -> ReleaseAssembly:
        persisted_decision = await repository.get_decision("decision-1")
        assert persisted_decision is not None
        quality_report = await repository.get_quality_report(persisted_decision.quality_run_id)
        assert quality_report is not None
        staging_root.mkdir(parents=True)
        (staging_root / "dataset.bin").write_bytes(b"release-data")
        return ReleaseAssembly(
            manifest=ReleaseManifest(
                release_id=request.release_id,
                created_at=datetime(2026, 9, 23, tzinfo=UTC),
                actor_id=request.actor_id,
                source_provenance=(source,),
                accepted_decision_ids=(persisted_decision.decision_id,),
                rejected_decision_ids=(),
                excluded_episode_indices=(),
                episode_index_mapping={0: 0},
                source_formats=(ReleaseFormat(name="lerobot", version="3.0"),),
                target_format=request.target_format,
                adapter_versions={"lerobot": "0.6.1"},
                tool_versions={"dataviewer": "0.1.0"},
                feature_schema={},
                candidate_count=1,
                accepted_count=1,
                rejected_count=0,
                excluded_count=0,
                nonincluded_count=0,
                episode_count=1,
                frame_count=1,
                quality_evidence=(
                    QualityEvidenceReference(
                        source_episode_index=0,
                        release_episode_index=0,
                        decision_id=persisted_decision.decision_id,
                        quality_run_id=persisted_decision.quality_run_id,
                        quality_report_path=f"metadata/quality/{persisted_decision.quality_run_id}.json",
                        check_set_version=quality_report.check_set_version,
                        required_outcome=QualityOutcome.PASS,
                    ),
                ),
                files=(),
            ),
            accepted=(persisted_decision,),
            rejected=(),
            quality_reports=(quality_report,),
            package_quality=PackageQualityReport(
                target_format=request.target_format,
                episode_count=1,
                frame_count=1,
                episode_frame_counts={0: 1},
                features=(),
                nonvisual_rows_read_back=1,
                visual_samples=(),
                inventory_verified=True,
                checksums_verified=True,
            ),
        )

    processor = ReleaseProcessor(
        release_service,
        jobs,
        staging_root=tmp_path / "staging",
        assembler=assemble,
        local_publisher=LocalReleasePublisher(tmp_path / "published"),
    )

    def notify(job_id: str) -> None:
        if jobs.get(job_id).release_id == "release-2":
            processor.notify(job_id)

    release_service.set_job_notifier(notify)
    app.dependency_overrides[get_review_workflow_service] = lambda: review_service
    app.dependency_overrides[get_release_workflow_service] = lambda: release_service
    try:
        with TestClient(app) as client:
            yield client, release_service, source
    finally:
        app.dependency_overrides.pop(get_review_workflow_service, None)
        app.dependency_overrides.pop(get_release_workflow_service, None)
        processor_module._release_processor = None
        release_service_module._release_workflow_service = None
        config_module._app_config = None


def test_given_review_evidence_when_release_lifecycle_runs_then_api_contract_is_stable(
    workflow_client: tuple[TestClient, ReleaseWorkflowService, SourceIdentity],
) -> None:
    # Arrange
    client, _, source = workflow_client
    timestamp = datetime(2025, 1, 1, tzinfo=UTC).isoformat()
    base_path = "/api/datasets/dataset-1/episodes/0/review"
    annotation = {
        "revision_id": "annotation-1",
        "source": source.model_dump(mode="json"),
        "actor_id": "reviewer\r\n",
        "created_at": timestamp,
        "annotation": {"label": "usable\r\n"},
    }
    edit = {
        "revision_id": "edit-1",
        "source": source.model_dump(mode="json"),
        "actor_id": "reviewer",
        "created_at": timestamp,
        "operations": [],
    }
    quality = {
        "run_id": "quality-1",
        "check_set_version": "1.0.0",
        "source": source.model_dump(mode="json"),
        "actor_id": "reviewer",
        "created_at": timestamp,
        "episode_checks": [{"check_id": "source.identity", "required": True, "outcome": "pass"}],
        "package_checks": [],
    }
    decision = {
        "decision_id": "decision-1",
        "decision": "accept",
        "reason_codes": ["reviewed"],
        "notes": "ready\r\nfor release",
        "actor_id": "reviewer",
        "created_at": timestamp,
        "source": source.model_dump(mode="json"),
        "annotation_revision_id": "annotation-1",
        "edit_revision_id": "edit-1",
        "quality_run_id": "quality-1",
    }

    # Act
    annotation_response = client.post(f"{base_path}/annotation-revisions", json=annotation)
    edit_response = client.post(f"{base_path}/edit-revisions", json=edit)
    quality_response = client.post(f"{base_path}/quality-reports", json=quality)
    quality_get = client.get(f"{base_path}/quality-reports/quality-1")
    latest_quality_get = client.get(f"{base_path}/quality-reports/latest")
    decision_response = client.post(f"{base_path}/decisions", json=decision)
    latest_decision_get = client.get(f"{base_path}/decisions/latest")
    request = {
        "releaseId": "release-1",
        "datasetId": "dataset-1",
        "actorId": "publisher\r\n",
        "reason": "approved\r\ntraining set",
        "destinationKind": "local",
        "idempotencyKey": "request-1",
        "targetFormat": ReleaseFormat(name="lerobot", version="3.0").model_dump(mode="json"),
        "episodes": [{"episodeIndex": 0, "decisionId": "decision-1"}],
    }
    eligibility = client.post("/api/releases/eligibility", json=request)
    submitted = client.post("/api/releases", json=request)
    duplicate = client.post("/api/releases", json=request)
    conflict = client.post("/api/releases", json={**request, "reason": "changed"})
    cancelled = client.post(f"/api/releases/jobs/{submitted.json()['jobId']}/cancel")
    second_request = {**request, "releaseId": "release-2", "idempotencyKey": "request-2"}
    second = client.post("/api/releases", json=second_request)
    for _ in range(100):
        terminal = client.get(f"/api/releases/jobs/{second.json()['jobId']}")
        if terminal.json()["state"] == "succeeded":
            break
    inspected = client.get("/api/releases/release-2")

    # Assert
    assert [
        annotation_response.status_code,
        edit_response.status_code,
        quality_response.status_code,
        quality_get.status_code,
        latest_quality_get.status_code,
        decision_response.status_code,
        latest_decision_get.status_code,
    ] == [201, 201, 201, 200, 200, 201, 200]
    assert annotation_response.json()["actor_id"] == "reviewer"
    assert quality_get.json()["run_id"] == "quality-1"
    assert latest_quality_get.json()["run_id"] == "quality-1"
    assert latest_decision_get.json()["decision_id"] == "decision-1"
    assert eligibility.status_code == 200
    eligibility_payload = eligibility.json()
    assert eligibility_payload["eligibleEpisodes"] == [
        {"episodeIndex": 0, "decisionId": "decision-1", "qualityRunId": "quality-1"}
    ]
    assert eligibility_payload["excludedEpisodes"] == []
    assert eligibility_payload["rejectedEpisodes"] == []
    assert len(eligibility_payload["eligibilityFingerprint"]) == 64
    assert submitted.status_code == 202
    assert submitted.json() == {
        "releaseId": "release-1",
        "jobId": submitted.json()["jobId"],
        "state": "queued",
        "eligibleEpisodes": [{"episodeIndex": 0, "decisionId": "decision-1", "qualityRunId": "quality-1"}],
        "excludedEpisodes": [],
        "rejectedEpisodes": [],
        "eligibilityFingerprint": eligibility_payload["eligibilityFingerprint"],
        "conflict": None,
        "verification": {"manifestPath": None, "checksumsPath": None, "verified": False},
    }
    assert duplicate.json()["jobId"] == submitted.json()["jobId"]
    assert conflict.status_code == 409
    assert cancelled.json()["state"] == "cancelled"
    assert terminal.json()["state"] == "succeeded"
    assert terminal.json()["verification"]["verified"] is True
    assert inspected.json()["verification"]["manifestPath"].endswith("manifest.json")
    manifest_path = Path(inspected.json()["verification"]["manifestPath"])
    assert (manifest_path.parents[1] / ".published.json").is_file()


def test_given_auth_and_csrf_enabled_when_release_routes_called_then_dependencies_are_enforced(
    workflow_client: tuple[TestClient, ReleaseWorkflowService, SourceIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    client, _, _ = workflow_client
    from src.api import auth as auth_mod

    monkeypatch.delenv("DATAVIEWER_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
    monkeypatch.setenv("DATAVIEWER_API_KEY", "test-key")
    auth_mod.reset_auth_provider()

    # Act
    read_response = client.get("/api/releases/jobs/missing")
    mutation_response = client.post("/api/releases", headers={"X-API-Key": "test-key"}, json={})

    # Assert
    assert read_response.status_code == 401
    assert mutation_response.status_code == 403
    auth_mod.reset_auth_provider()
