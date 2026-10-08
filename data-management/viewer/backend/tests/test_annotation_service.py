"""Unit tests for AnnotationService CRUD and analysis logic."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.api.models.annotations import (
    AnomalyAnnotation,
    ConfidenceLevel,
    DataQualityAnnotation,
    DataQualityLevel,
    EpisodeAnnotation,
    EpisodeAnnotationFile,
    QualityScore,
    TaskCompletenessAnnotation,
    TaskCompletenessRating,
    TrajectoryFlag,
    TrajectoryQualityAnnotation,
    TrajectoryQualityMetrics,
)
from src.api.models.datasources import EpisodeData, EpisodeMeta, TrajectoryPoint
from src.api.models.episode_edits import EpisodeEditOperations, SavedEpisodeEdits
from src.api.services.annotation_service import AnnotationService
from src.api.storage import LocalStorageAdapter, RevisionConflictError


def _build_annotation(annotator_id: str = "alice", rating: QualityScore = QualityScore.FOUR) -> EpisodeAnnotation:
    return EpisodeAnnotation(
        annotator_id=annotator_id,
        timestamp=datetime.now(UTC),
        task_completeness=TaskCompletenessAnnotation(
            rating=TaskCompletenessRating.SUCCESS,
            confidence=ConfidenceLevel.FOUR,
        ),
        trajectory_quality=TrajectoryQualityAnnotation(
            overall_score=rating,
            metrics=TrajectoryQualityMetrics(
                smoothness=QualityScore.FOUR,
                efficiency=QualityScore.FOUR,
                safety=QualityScore.FOUR,
                precision=QualityScore.FOUR,
            ),
        ),
        data_quality=DataQualityAnnotation(overall_quality=DataQualityLevel.GOOD),
        anomalies=AnomalyAnnotation(),
    )


def _make_episode(points: list[TrajectoryPoint]) -> EpisodeData:
    return EpisodeData(
        meta=EpisodeMeta(index=0, length=len(points), task_index=0),
        trajectory_data=points,
    )


def _trajectory_point(frame: int, positions: list[float], velocities: list[float]) -> TrajectoryPoint:
    return TrajectoryPoint(
        timestamp=float(frame) * 0.1,
        frame=frame,
        joint_positions=positions,
        joint_velocities=velocities,
        end_effector_pose=[0.0] * 6,
        gripper_state=0.0,
    )


@pytest.fixture
def service(tmp_path: Path) -> AnnotationService:
    return AnnotationService(storage_adapter=LocalStorageAdapter(str(tmp_path)))


class TestAnnotationServiceConstruction:
    async def test_uses_provided_adapter(self, tmp_path: Path) -> None:
        adapter = LocalStorageAdapter(str(tmp_path))
        svc = AnnotationService(storage_adapter=adapter)
        await svc.save_annotation("ds", 0, _build_annotation())
        loaded = await adapter.get_annotation("ds", 0)
        assert loaded is not None
        assert loaded.annotations[0].annotator_id == "alice"

    async def test_falls_back_to_local_adapter(self, tmp_path: Path) -> None:
        svc = AnnotationService(base_path=str(tmp_path))
        await svc.save_annotation("ds", 0, _build_annotation())
        loaded = await LocalStorageAdapter(str(tmp_path)).get_annotation("ds", 0)
        assert loaded is not None
        assert loaded.annotations[0].annotator_id == "alice"


class TestContributionLedger:
    def test_given_equal_human_and_machine_values_when_machine_withdrawn_then_human_survives(self) -> None:
        from src.api.models.contributions import Contribution, ContributionLedger, MachineOrigin

        ledger = ContributionLedger()
        machine = Contribution(
            id="machine-result-label",
            field="labels/SUCCESS",
            origin="machine",
            value=True,
            sequence=1,
            timestamp="2026-10-07T12:00:00Z",
            machine=MachineOrigin(
                run_id="run-a",
                result_id="result-a",
                run_order=1,
                source_revision="source-a",
                input_revision="input-a",
                config_revision="config-a",
            ),
        )
        human = Contribution(
            id="human-label",
            field="labels/SUCCESS",
            origin="human",
            author_id="alice",
            value=True,
            sequence=2,
            timestamp="2026-10-07T12:01:00Z",
        )
        ledger.record(machine)
        ledger.record(human)
        ledger.withdraw([machine.id])
        restored = ContributionLedger.model_validate_json(ledger.model_dump_json())
        effective = restored.resolve("labels/SUCCESS", human_author_id="alice")
        assert effective.value is True and effective.origin == "human"
        assert effective.contribution_ids == [human.id]
        with pytest.raises(ValueError, match="immutable"):
            restored.record(human.model_copy(update={"value": False}))

    def test_given_acceptance_and_overlapping_runs_when_resolving_then_authorship_and_order_are_preserved(self) -> None:
        from src.api.models.contributions import Contribution, ContributionLedger, MachineOrigin

        ledger = ContributionLedger()
        for identifier, run_order, value in (("newer", 2, "new"), ("older", 1, "old")):
            ledger.record(
                Contribution(
                    id=identifier,
                    field="instruction",
                    origin="machine",
                    value=value,
                    sequence=3 - run_order,
                    timestamp="2026-10-07T12:00:00Z",
                    machine=MachineOrigin(
                        run_id=identifier,
                        result_id=identifier,
                        run_order=run_order,
                        source_revision="source",
                        input_revision="input",
                        config_revision="config",
                    ),
                )
            )
        ledger.accept("newer", "alice")
        result = ledger.resolve("instruction", human_author_id="alice")
        assert result.origin == "machine" and result.value == "new"
        assert ledger.acceptances == {"newer": ["alice"]}
        ledger.withdraw(["older"])
        ledger.withdraw(["newer"])
        assert ledger.resolve("instruction").contribution_ids == []
        assert ledger.resolve("instruction", legacy_value="new").value is None

    def test_given_unprovenanced_or_multiple_human_values_when_resolving_then_no_author_is_inferred(self) -> None:
        from src.api.models.contributions import Contribution, ContributionLedger

        ledger = ContributionLedger()
        legacy = ledger.resolve("instruction", legacy_value="human")
        assert legacy.value == "human" and legacy.origin == "legacy-unknown"
        for sequence, author in enumerate(("alice", "bob"), start=1):
            ledger.record(
                Contribution(
                    id=author,
                    field="instruction",
                    origin="human",
                    value=author,
                    author_id=author,
                    sequence=sequence,
                    timestamp="2026-10-07T12:00:00Z",
                )
            )
        with pytest.raises(ValueError, match="Select a human author"):
            ledger.resolve("instruction")
        assert ledger.resolve("instruction", human_author_id="alice").value == "alice"


class TestSaveAndGet:
    def test_given_missing_instruction_when_adoption_requested_then_schema_rejects_it(self) -> None:
        with pytest.raises(ValueError, match="adoption"):
            EpisodeAnnotation.model_validate(
                _build_annotation().model_dump() | {"instruction_adoption": {"instruction": "template"}}
            )

    @pytest.mark.parametrize("origin", ["template", "retroactive"])
    async def test_given_adopted_instruction_when_human_edits_then_original_origin_is_retained(
        self, service: AnnotationService, origin: str
    ) -> None:
        from src.api.models.annotations import LanguageInstructionAnnotation

        annotation = _build_annotation()
        annotation.language_instruction = LanguageInstructionAnnotation(
            instruction="Move the block", source=origin, subtask_instructions=["Reach"]
        )
        annotation = EpisodeAnnotation.model_validate(
            annotation.model_dump() | {"instruction_adoption": {"instruction": origin, "subtasks/legacy-0": origin}}
        )
        saved = await service.save_annotation("ds", 0, annotation, if_none_match=True)
        ledger = saved.value.provenance["alice"]
        original = ledger.resolve("language_instruction/instruction", human_author_id="alice")
        assert original.origin == origin
        assert ledger.acceptances[original.contribution_ids[0]] == ["alice"]
        assert "instruction_adoption" not in saved.value.model_dump(mode="json")["annotations"][0]
        edited = saved.value.annotations[0].model_copy(deep=True)
        edited.language_instruction.instruction = "Move the block carefully"

        revised = await service.save_annotation("ds", 0, edited, if_match=saved.etag)

        ledger = revised.value.provenance["alice"]
        assert ledger.resolve("language_instruction/instruction", human_author_id="alice").origin == "human"
        assert next(item for item in ledger.contributions if item.id == original.contribution_ids[0]).origin == origin
        assert (
            ledger.resolve("language_instruction/subtask_instructions/legacy-0", human_author_id="alice").origin
            == origin
        )

    async def test_given_subtask_ids_when_reordered_then_ownership_follows_item(
        self, service: AnnotationService
    ) -> None:
        from src.api.models.annotations import LanguageInstructionAnnotation

        annotation = _build_annotation()
        annotation.language_instruction = LanguageInstructionAnnotation(
            instruction="Move the block", source="human", subtask_instructions=["Reach", "Grasp"]
        )
        saved = await service.save_annotation("ds", 0, annotation, if_none_match=True)
        items = saved.value.annotations[0].language_instruction.subtask_instructions
        assert items[0].id != items[1].id
        assert items[0].text == "Reach"
        before = saved.value.provenance["alice"].model_dump()
        reordered = annotation.model_copy(deep=True)
        reordered.language_instruction.subtask_instructions.reverse()

        result = await service.save_annotation("ds", 0, reordered, if_match=saved.etag)

        assert result.value.provenance["alice"].model_dump() == before
        assert result.value.annotations[0].language_instruction.subtask_instructions[0].id == items[1].id

    async def test_given_human_subtask_edit_when_machine_updates_item_then_human_text_survives(
        self, service: AnnotationService
    ) -> None:
        from src.api.models.annotations import LanguageInstructionAnnotation
        from src.api.models.contributions import MachineOrigin

        annotation = _build_annotation()
        annotation.language_instruction = LanguageInstructionAnnotation(
            instruction="Move the block",
            source="human",
            subtask_instructions=[{"id": "reach", "text": "Reach carefully"}],
        )
        saved = await service.save_annotation("ds", 0, annotation, if_none_match=True)
        proposal = annotation.model_copy(deep=True)
        proposal.language_instruction.subtask_instructions[0].text = "Reach quickly"
        origin = MachineOrigin(
            run_id="run",
            result_id="result",
            run_order=1,
            source_revision="source",
            input_revision="input",
            config_revision="config",
        )

        result = await service.save_annotation("ds", 0, proposal, machine_origin=origin, if_match=saved.etag)

        item = result.value.annotations[0].language_instruction.subtask_instructions[0]
        assert item.id == "reach" and item.text == "Reach carefully"

    async def test_given_deleted_author_when_machine_recreates_then_old_human_values_do_not_return(
        self, service: AnnotationService
    ) -> None:
        from src.api.models.contributions import MachineOrigin

        alice = _build_annotation()
        alice.notes = "Old human notes"
        saved = await service.save_annotation("ds", 0, alice, if_none_match=True)
        other = await service.save_annotation("ds", 0, _build_annotation("bob"), if_match=saved.etag)
        await service.delete_annotation("ds", 0, "alice", if_match=other.etag)
        current = await service.get_annotation_versioned("ds", 0)
        proposal = alice.model_copy(update={"notes": "New machine notes"})
        origin = MachineOrigin(
            run_id="run",
            result_id="result",
            run_order=1,
            source_revision="source",
            input_revision="input",
            config_revision="config",
        )

        result = await service.save_annotation("ds", 0, proposal, machine_origin=origin, if_match=current.etag)

        restored = next(item for item in result.value.annotations if item.annotator_id == "alice")
        assert restored.notes == "New machine notes"
        assert result.value.provenance["alice"].resolve("notes", human_author_id="alice").origin == "machine"

    async def test_given_human_instruction_when_machine_clears_parent_then_save_is_rejected(
        self, service: AnnotationService
    ) -> None:
        from src.api.models.annotations import LanguageInstructionAnnotation
        from src.api.models.contributions import MachineOrigin

        original = _build_annotation()
        original.language_instruction = LanguageInstructionAnnotation(instruction="Move the block", source="human")
        saved = await service.save_annotation("ds", 0, original, if_none_match=True)
        cleared = original.model_copy(update={"language_instruction": None})
        origin = MachineOrigin(
            run_id="run",
            result_id="result",
            run_order=1,
            source_revision="source",
            input_revision="input",
            config_revision="config",
        )

        with pytest.raises(ValueError, match="human-authored"):
            await service.save_annotation("ds", 0, cleared, machine_origin=origin, if_match=saved.etag)

        loaded = await service.get_annotation_versioned("ds", 0)
        assert loaded.etag == saved.etag
        assert loaded.value.annotations[0].language_instruction.instruction == "Move the block"

    async def test_given_legacy_instruction_when_notes_saved_then_instruction_ownership_stays_unknown(
        self, service: AnnotationService, tmp_path: Path
    ) -> None:
        from src.api.models.annotations import LanguageInstructionAnnotation

        original = _build_annotation()
        original.language_instruction = LanguageInstructionAnnotation(instruction="Move the block", source="human")
        adapter = LocalStorageAdapter(str(tmp_path))
        revision = await adapter.save_annotation(
            "ds",
            0,
            EpisodeAnnotationFile(dataset_id="ds", episode_index=0, annotations=[original]),
            if_none_match=True,
        )
        changed = original.model_copy(update={"notes": "Needs review"})

        await service.save_annotation("ds", 0, changed, if_match=revision)
        loaded = await adapter.get_annotation("ds", 0)

        assert loaded is not None
        ledger = loaded.provenance["alice"]
        assert (
            ledger.resolve("language_instruction/instruction", legacy_value="Move the block").origin == "legacy-unknown"
        )
        assert ledger.resolve("notes", human_author_id="alice").origin == "human"
        assert ledger.resolve("notes", human_author_id="alice").value == "Needs review"

    async def test_given_machine_instruction_when_human_revises_then_same_valued_later_result_keeps_human_origin(
        self, service: AnnotationService
    ) -> None:
        from src.api.models.annotations import LanguageInstructionAnnotation
        from src.api.models.contributions import MachineOrigin

        generated = _build_annotation()
        generated.language_instruction = LanguageInstructionAnnotation(
            instruction="Move the block", source="llm-generated"
        )
        origin = MachineOrigin(
            run_id="run",
            result_id="result",
            run_order=1,
            source_revision="source",
            input_revision="input",
            config_revision="config",
        )
        saved = await service.save_annotation("ds", 0, generated, machine_origin=origin, if_none_match=True)
        edited = generated.model_copy(deep=True)
        edited.language_instruction.instruction = "Move the block carefully"
        revised = await service.save_annotation("ds", 0, edited, if_match=saved.etag)
        later = origin.model_copy(update={"run_id": "later", "result_id": "later", "run_order": 2})

        result = await service.save_annotation("ds", 0, edited, machine_origin=later, if_match=revised.etag)

        ledger = result.value.provenance["alice"]
        effective = ledger.resolve("language_instruction/instruction", human_author_id="alice")
        assert effective.origin == "human" and effective.value == "Move the block carefully"
        ledger.withdraw([item.id for item in ledger.contributions if item.origin == "machine"])
        assert ledger.resolve("language_instruction/instruction", human_author_id="alice") == effective

    async def test_given_complete_edits_when_storage_reopens_then_descriptor_survives(self, tmp_path: Path) -> None:
        operations = {
            "datasetId": "ds",
            "episodeIndex": 0,
            "globalTransform": {
                "crop": {"x": 0, "y": 0, "width": 64, "height": 48},
                "colorAdjustment": {"brightness": 0.2},
                "colorFilter": "warm",
            },
            "cameraTransforms": {"front": {"resize": {"width": 32, "height": 24}}},
            "removedFrames": [2],
            "insertedFrames": [{"afterFrameIndex": 3, "interpolationFactor": 0.5}],
            "trajectoryAdjustments": [{"frameIndex": 4, "rightArmDelta": [0.1, 0.2, 0.3], "leftGripperOverride": 0.4}],
            "subtasks": [
                {
                    "id": "segment-1",
                    "label": "Reach",
                    "frameRange": [0, 4],
                    "color": "#123456",
                    "source": "manual",
                    "description": "Reach target",
                }
            ],
        }
        descriptor = {
            "schema_version": "1.0.0",
            "dataset_id": "ds",
            "episode_index": 0,
            "source_id": "source-a",
            "source_revision": "generation-a",
            "author_id": "alice",
            "operations": operations,
            "updated_at": "2026-10-07T00:00:00Z",
        }
        envelope = EpisodeAnnotationFile.model_validate(
            {
                "dataset_id": "ds",
                "episode_index": 0,
                "annotations": [_build_annotation().model_dump(mode="json")],
                "saved_edits": {"source-a": {"alice": descriptor}},
            }
        )
        await LocalStorageAdapter(str(tmp_path)).save_annotation("ds", 0, envelope, if_none_match=True)

        restored = await LocalStorageAdapter(str(tmp_path)).get_annotation("ds", 0)

        assert restored is not None
        saved = restored.model_dump(mode="json", exclude_none=True)["saved_edits"]["source-a"]["alice"]
        assert saved["operations"] == operations
        assert saved["source_revision"] == "generation-a"
        assert restored.annotations[0].annotator_id == "alice"

    async def test_save_creates_new_file(self, service: AnnotationService) -> None:
        result = await service.save_annotation("ds", 0, _build_annotation())
        assert result.value is not None
        assert len(result.value.annotations) == 1
        assert result.etag is not None
        fetched = await service.get_annotation("ds", 0)
        assert fetched is not None
        assert fetched.annotations[0].annotator_id == "alice"

    async def test_save_updates_existing_annotator(self, service: AnnotationService) -> None:
        await service.save_annotation("ds", 0, _build_annotation("alice", QualityScore.TWO))
        updated = await service.save_annotation("ds", 0, _build_annotation("alice", QualityScore.FIVE))
        assert updated.value is not None
        assert len(updated.value.annotations) == 1
        assert updated.value.annotations[0].trajectory_quality.overall_score == QualityScore.FIVE.value

    async def test_save_appends_new_annotator(self, service: AnnotationService) -> None:
        await service.save_annotation("ds", 0, _build_annotation("alice"))
        result = await service.save_annotation("ds", 0, _build_annotation("bob"))
        assert result.value is not None
        assert {annotation.annotator_id for annotation in result.value.annotations} == {"alice", "bob"}

    async def test_get_missing_returns_none(self, service: AnnotationService) -> None:
        assert await service.get_annotation("ds", 99) is None


class TestDelete:
    @pytest.mark.parametrize("owner", [None, "alice"])
    async def test_given_saved_edits_when_annotations_deleted_then_edits_survive(
        self,
        tmp_path: Path,
        owner: str | None,
    ) -> None:
        descriptor = SavedEpisodeEdits(
            dataset_id="ds",
            episode_index=0,
            source_id="source-a",
            source_revision="generation-a",
            author_id="alice",
            updated_at=datetime.now(UTC),
            operations=EpisodeEditOperations(datasetId="ds", episodeIndex=0, removedFrames=[2]),
        )
        envelope = EpisodeAnnotationFile(
            dataset_id="ds",
            episode_index=0,
            annotations=[_build_annotation()],
            saved_edits={"source-a": {"alice": descriptor}},
        )
        adapter = LocalStorageAdapter(str(tmp_path))
        revision = await adapter.save_annotation("ds", 0, envelope, if_none_match=True)

        await AnnotationService(storage_adapter=adapter).delete_annotation("ds", 0, owner, if_match=revision)

        loaded = await LocalStorageAdapter(str(tmp_path)).get_annotation("ds", 0)
        assert loaded is not None
        assert loaded.annotations == []
        assert loaded.saved_edits["source-a"]["alice"] == descriptor

    async def test_delete_all_annotators(self, service: AnnotationService) -> None:
        await service.save_annotation("ds", 0, _build_annotation("alice"))
        assert await service.delete_annotation("ds", 0) is True
        assert await service.get_annotation("ds", 0) is None

    async def test_delete_unknown_returns_false(self, service: AnnotationService) -> None:
        assert await service.delete_annotation("ds", 0) is False

    async def test_delete_specific_annotator(self, service: AnnotationService) -> None:
        await service.save_annotation("ds", 0, _build_annotation("alice"))
        await service.save_annotation("ds", 0, _build_annotation("bob"))
        assert await service.delete_annotation("ds", 0, annotator_id="alice") is True
        remaining = await service.get_annotation("ds", 0)
        assert remaining is not None
        assert [annotation.annotator_id for annotation in remaining.annotations] == ["bob"]

    async def test_delete_specific_annotator_missing_file(self, service: AnnotationService) -> None:
        assert await service.delete_annotation("ds", 0, annotator_id="alice") is False

    async def test_delete_specific_annotator_not_found(self, service: AnnotationService) -> None:
        await service.save_annotation("ds", 0, _build_annotation("alice"))
        assert await service.delete_annotation("ds", 0, annotator_id="bob") is False

    async def test_delete_last_annotator_removes_file(self, service: AnnotationService) -> None:
        await service.save_annotation("ds", 0, _build_annotation("alice"))
        assert await service.delete_annotation("ds", 0, annotator_id="alice") is True
        assert await service.get_annotation("ds", 0) is None


class TestSavedEdits:
    async def test_given_scoped_edits_when_saved_and_cleared_then_durable_state_is_distinct(
        self,
        tmp_path: Path,
    ) -> None:
        service = AnnotationService(base_path=str(tmp_path))
        scope = {"author_id": "alice", "source_id": "source-a", "source_revision": "generation-a"}
        assert (await service.get_saved_edits("ds", 0, **scope)).value is None
        operations = EpisodeEditOperations(datasetId="ds", episodeIndex=0, removedFrames=[2])
        saved = await service.save_edits(
            "ds",
            0,
            operations,
            **scope,
            frame_count=10,
            cameras={"front"},
            if_none_match=True,
        )
        assert await LocalStorageAdapter(str(tmp_path)).list_annotated_episodes("ds") == []
        reopened = AnnotationService(base_path=str(tmp_path))
        loaded = await reopened.get_saved_edits("ds", 0, **scope)
        assert loaded == saved
        assert (await reopened.get_saved_edits("ds", 0, **{**scope, "author_id": "bob"})).value is None
        assert (await reopened.get_saved_edits("ds", 0, **{**scope, "source_id": "source-b"})).value is None

        await reopened.save_edits(
            "ds",
            0,
            EpisodeEditOperations(datasetId="ds", episodeIndex=0),
            **scope,
            frame_count=10,
            cameras={"front"},
            if_match=loaded.etag,
        )
        cleared = await AnnotationService(base_path=str(tmp_path)).get_saved_edits("ds", 0, **scope)
        assert cleared.value is not None
        assert cleared.value.operations.removedFrames is None

    async def test_given_concurrent_annotation_when_saving_edits_then_stale_revision_is_rejected(
        self,
        service: AnnotationService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        scope = {"author_id": "alice", "source_id": "source-a", "source_revision": "generation-a"}
        saved = await service.save_edits(
            "ds",
            0,
            EpisodeEditOperations(datasetId="ds", episodeIndex=0, removedFrames=[2]),
            **scope,
            frame_count=10,
            cameras={"front"},
            if_none_match=True,
        )
        await service.save_annotation("ds", 0, _build_annotation("bob"), if_none_match=True)
        newer = await service.save_edits(
            "ds",
            0,
            EpisodeEditOperations(datasetId="ds", episodeIndex=0, removedFrames=[3]),
            **scope,
            frame_count=10,
            cameras={"front"},
            if_match=saved.etag,
        )
        with pytest.raises(RevisionConflictError):
            await service.save_edits(
                "ds",
                0,
                EpisodeEditOperations(datasetId="ds", episodeIndex=0),
                **scope,
                frame_count=10,
                cameras={"front"},
                if_match=saved.etag,
            )
        loaded = await service.get_annotation("ds", 0)
        assert loaded.annotations[0].annotator_id == "bob"
        edit = await service.get_saved_edits("ds", 0, **scope)
        assert edit.etag == newer.etag
        assert edit.value.operations.removedFrames == [3]
        assert "revision conflict" in caplog.text

    async def test_given_replaced_source_when_loading_edits_then_old_generation_is_rejected(
        self,
        service: AnnotationService,
    ) -> None:
        scope = {"author_id": "alice", "source_id": "source-a", "source_revision": "generation-a"}
        await service.save_edits(
            "ds",
            0,
            EpisodeEditOperations(datasetId="ds", episodeIndex=0),
            **scope,
            frame_count=10,
            cameras={"front"},
            if_none_match=True,
        )
        with pytest.raises(ValueError, match="source revision"):
            await service.get_saved_edits("ds", 0, **{**scope, "source_revision": "generation-b"})


class TestRunAutoAnalysis:
    async def test_short_trajectory_returns_neutral(self, service: AnnotationService) -> None:
        ep = _make_episode([_trajectory_point(0, [0.0] * 6, [0.0] * 6)])
        result = await service.run_auto_analysis("ds", 0, ep)
        assert result.suggested_rating == 3
        assert result.confidence == 0.0
        assert result.flags == []
        assert result.computed.jitter_metric == 0.0
        assert result.computed.hesitation_count == 0
        assert result.computed.correction_count == 0

    async def test_smooth_trajectory_no_flags(self, service: AnnotationService) -> None:
        points = [_trajectory_point(i, [float(i)] * 6, [0.5] * 6) for i in range(10)]
        result = await service.run_auto_analysis("ds", 0, _make_episode(points))
        assert result.flags == []
        assert result.suggested_rating == 5
        assert result.confidence == pytest.approx(0.1)
        assert result.computed.smoothness_score == pytest.approx(1.0)
        assert result.computed.efficiency_score == pytest.approx(0.99)

    async def test_jittery_trajectory_flagged(self, service: AnnotationService) -> None:
        points = []
        for i in range(20):
            vel = 5.0 if i % 2 == 0 else 0.0
            points.append(_trajectory_point(i, [float(i)] * 6, [vel] * 6))
        result = await service.run_auto_analysis("ds", 0, _make_episode(points))
        assert TrajectoryFlag.JITTERY in result.flags
        assert result.computed.jitter_metric == pytest.approx(19 / 20)

    async def test_hesitation_flagged(self, service: AnnotationService) -> None:
        points: list[TrajectoryPoint] = []
        frame = 0
        for _ in range(3):
            for _ in range(15):
                points.append(_trajectory_point(frame, [0.0] * 6, [0.0] * 6))
                frame += 1
            points.append(_trajectory_point(frame, [0.0] * 6, [1.0] * 6))
            frame += 1
        result = await service.run_auto_analysis("ds", 0, _make_episode(points))
        assert TrajectoryFlag.HESITATION in result.flags
        assert result.computed.hesitation_count == 3

    async def test_correction_heavy_flagged(self, service: AnnotationService) -> None:
        points = []
        for i in range(20):
            pos = [float(i % 2)] * 6
            points.append(_trajectory_point(i, pos, [0.0] * 6))
        result = await service.run_auto_analysis("ds", 0, _make_episode(points))
        assert TrajectoryFlag.CORRECTION_HEAVY in result.flags
        assert result.computed.correction_count == 18


class TestGetSummary:
    async def test_empty_dataset(self, service: AnnotationService) -> None:
        summary = await service.get_summary("ds", total_episodes=10)
        assert summary.dataset_id == "ds"
        assert summary.total_episodes == 10
        assert summary.annotated_episodes == 0
        assert summary.task_completeness_distribution == {}
        assert summary.quality_score_distribution == {}
        assert summary.anomaly_type_counts == {}

    async def test_aggregates_distributions(self, service: AnnotationService) -> None:
        await service.save_annotation("ds", 0, _build_annotation("alice", QualityScore.FIVE))
        await service.save_annotation("ds", 1, _build_annotation("bob", QualityScore.FIVE))
        await service.save_annotation("ds", 2, _build_annotation("carol", QualityScore.THREE))
        summary = await service.get_summary("ds", total_episodes=10)
        assert summary.annotated_episodes == 3
        assert summary.quality_score_distribution == {5: 2, 3: 1}
        assert summary.task_completeness_distribution == {"success": 3}
        assert summary.anomaly_type_counts == {}
