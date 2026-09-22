"""Unit tests for detection service episode processing behavior."""

from __future__ import annotations

import asyncio
import hashlib
import io
import types
from pathlib import Path

import pytest
from PIL import Image as PILImage

from src.api.models.detection import DetectionRequest, DetectionResult, EpisodeDetectionSummary
from src.api.services import detection_service as ds_module
from src.api.services.detection_service import (
    DetectionModelError,
    DetectionModelUnavailableError,
    DetectionService,
    InvalidDetectionModelError,
)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (8, 8), color=(0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _model_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestDetectionEpisodeProcessing:
    """Tests for frame index handling in episode detection."""

    def test_detect_episode_preserves_integer_frame_indices(self, monkeypatch):
        service = DetectionService()
        observed_indices: list[int] = []

        async def get_frame_image(frame_idx: int) -> bytes:
            assert isinstance(frame_idx, int)
            observed_indices.append(frame_idx)
            return b"image-bytes"

        async def fake_detect_frame(
            self,
            image_bytes: bytes,
            frame_idx: int,
            confidence: float = 0.25,
            model_name: str = "yolo11n",
            labels: list[str] | None = None,
        ) -> DetectionResult:
            assert image_bytes == b"image-bytes"
            assert isinstance(frame_idx, int)
            observed_indices.append(frame_idx)
            return DetectionResult(frame=frame_idx, detections=[], processing_time_ms=1.0)

        monkeypatch.setattr(DetectionService, "detect_frame", fake_detect_frame)
        monkeypatch.setattr(service, "_resolve_model_path", lambda _model_name: Path("yolo11n.pt"))

        summary = asyncio.run(
            service.detect_episode(
                dataset_id="dataset",
                episode_idx=0,
                request=DetectionRequest(frames=[1, 3]),
                get_frame_image=get_frame_image,
                total_frames=10,
            )
        )

        assert observed_indices == [1, 1, 3, 3]
        assert [result.frame for result in summary.detections_by_frame] == [1, 3]
        assert summary.processed_frames == 2

    def test_get_model_logs_sanitized_model_name(self, monkeypatch, tmp_path: Path):
        model_path = tmp_path / "yolo11n.pt"
        model_path.touch()
        service = DetectionService(models_dir=tmp_path, model_digests={"yolo11n": _model_digest(model_path)})
        logged: list[tuple[object, ...]] = []

        class FakeYOLO:
            def __init__(self, model_path: str):
                self.model_path = model_path

            def __call__(self, *_args, **_kwargs):
                return []

        monkeypatch.setattr(
            "src.api.services.detection_service.logger.info",
            lambda message, *args: logged.append((message, *args)),
        )
        monkeypatch.setitem(__import__("sys").modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO))

        service._get_model("yolo11n\r\n")

        assert logged[0] == ("Loading YOLO model: %s", "yolo11n")


class _FakeTensor:
    def __init__(self, value):
        self._value = value

    def item(self):
        return self._value


class _FakeXYXY:
    def __init__(self, coords):
        self._coords = coords

    def tolist(self):
        return self._coords


class _FakeBoxes:
    def __init__(self, classes, confs, xyxy):
        self.cls = [_FakeTensor(c) for c in classes]
        self.conf = [_FakeTensor(c) for c in confs]
        self.xyxy = [_FakeXYXY(b) for b in xyxy]

    def __len__(self):
        return len(self.cls)


class _FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes


class _FakeYOLOModel:
    def __init__(self, results):
        self._results = results

    def __call__(self, *_a, **_kw):
        return self._results


class TestGetModelExtra:
    def test_returns_cached_model(self):
        s = DetectionService()
        sentinel = _FakeYOLOModel([])
        s._model = sentinel
        s._model_name = "yolo11n"
        assert s._get_model("yolo11n") is sentinel

    def test_raises_on_import_error(self, monkeypatch, tmp_path: Path):
        import builtins as _bi

        model_path = tmp_path / "yolo11n.pt"
        model_path.touch()
        s = DetectionService(models_dir=tmp_path, model_digests={"yolo11n": _model_digest(model_path)})
        real_import = _bi.__import__

        def fake_import(name, *a, **kw):
            if name == "ultralytics":
                raise ImportError("no ultralytics")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(_bi, "__import__", fake_import)
        with pytest.raises(ImportError):
            s._get_model("yolo11n")

    def test_load_failure_does_not_publish_model_state(self, monkeypatch, tmp_path: Path):
        model_path = tmp_path / "yolo11n.pt"
        model_path.write_bytes(b"reviewed checkpoint")
        service = DetectionService(models_dir=tmp_path, model_digests={"yolo11n": _model_digest(model_path)})

        class FailingYOLO:
            def __init__(self, _model_path: str):
                raise RuntimeError("corrupt checkpoint")

        monkeypatch.setitem(__import__("sys").modules, "ultralytics", types.SimpleNamespace(YOLO=FailingYOLO))

        with pytest.raises(DetectionModelUnavailableError):
            service._get_model("yolo11n")

        assert service._model is None
        assert service._model_name == ""

    def test_warmup_failure_does_not_publish_model_state(self, monkeypatch, tmp_path: Path):
        model_path = tmp_path / "yolo11n.pt"
        model_path.write_bytes(b"reviewed checkpoint")
        service = DetectionService(models_dir=tmp_path, model_digests={"yolo11n": _model_digest(model_path)})

        class FailingYOLO:
            def __init__(self, staged_path: str):
                assert Path(staged_path).read_bytes() == model_path.read_bytes()

            def __call__(self, *_args, **_kwargs):
                raise RuntimeError("warmup failed")

        monkeypatch.setitem(__import__("sys").modules, "ultralytics", types.SimpleNamespace(YOLO=FailingYOLO))

        with pytest.raises(DetectionModelUnavailableError):
            service._get_model("yolo11n")

        assert service._model is None
        assert service._model_name == ""

    def test_model_switching_uses_distinct_service_generated_staging_paths(self, monkeypatch, tmp_path: Path):
        model_paths = {
            "yolo11n": tmp_path / "yolo11n.pt",
            "yolov8s-world": tmp_path / "yolov8s-world.pt",
        }
        for model_name, model_path in model_paths.items():
            model_path.write_bytes(model_name.encode())
        service = DetectionService(
            models_dir=tmp_path,
            model_digests={model_name: _model_digest(model_path) for model_name, model_path in model_paths.items()},
        )
        staged_paths: list[Path] = []

        class FakeYOLO:
            def __init__(self, staged_path: str):
                staged_paths.append(Path(staged_path))

            def __call__(self, *_args, **_kwargs):
                return []

        monkeypatch.setitem(__import__("sys").modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO))

        service._get_model("yolo11n")
        service._get_model("yolov8s-world")
        service._get_model("yolo11n")

        assert len(set(staged_paths)) == 3
        assert all(path.name not in {"yolo11n.pt", "yolov8s-world.pt"} for path in staged_paths)


class TestModelPathRestrictions:
    def test_resolves_approved_model_inside_configured_directory(self, tmp_path: Path):
        model_path = tmp_path / "yolo11n.pt"
        model_path.touch()
        service = DetectionService(models_dir=tmp_path, model_digests={"yolo11n": _model_digest(model_path)})

        assert service._resolve_model_path("yolo11n") == model_path.resolve()

    @pytest.mark.parametrize(
        "model_name",
        [
            "unapproved",
            "../yolo11n",
            "/tmp/yolo11n.pt",
            "nested/yolo11n",
        ],
    )
    def test_rejects_unknown_and_path_like_model_identifiers(self, tmp_path: Path, model_name: str):
        service = DetectionService(models_dir=tmp_path)

        with pytest.raises(DetectionModelError, match="approved model identifier"):
            service._resolve_model_path(model_name)

    def test_rejects_missing_approved_model_file(self, tmp_path: Path):
        service = DetectionService(models_dir=tmp_path, model_digests={"yolo11n": "0" * 64})

        with pytest.raises(DetectionModelUnavailableError, match="not found"):
            service._resolve_model_path("yolo11n")

    def test_rejects_symlink_escape(self, tmp_path: Path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        outside_model = tmp_path / "outside.pt"
        outside_model.touch()
        (models_dir / "yolo11n.pt").symlink_to(outside_model)
        service = DetectionService(models_dir=models_dir, model_digests={"yolo11n": _model_digest(outside_model)})

        with pytest.raises(DetectionModelError, match="outside configured models directory"):
            service._resolve_model_path("yolo11n")

    def test_invalid_model_is_rejected_before_processing_empty_episode(self, tmp_path: Path):
        service = DetectionService(models_dir=tmp_path)

        async def get_frame_image(_frame_idx: int) -> None:
            return None

        with pytest.raises(InvalidDetectionModelError, match="approved model identifier"):
            asyncio.run(
                service.detect_episode(
                    dataset_id="d",
                    episode_idx=0,
                    request=DetectionRequest(model="../unsafe"),
                    get_frame_image=get_frame_image,
                    total_frames=0,
                )
            )

    def test_invalid_model_with_labels_is_not_replaced_before_validation(self, tmp_path: Path):
        (tmp_path / "yolov8s-world.pt").touch()
        service = DetectionService(models_dir=tmp_path)

        async def get_frame_image(_frame_idx: int) -> None:
            return None

        with pytest.raises(InvalidDetectionModelError, match="approved model identifier"):
            asyncio.run(
                service.detect_episode(
                    dataset_id="d",
                    episode_idx=0,
                    request=DetectionRequest(model="../unsafe", labels=["part"]),
                    get_frame_image=get_frame_image,
                    total_frames=0,
                )
            )

    def test_rejects_model_without_configured_digest(self, tmp_path: Path):
        (tmp_path / "yolo11n.pt").touch()
        service = DetectionService(models_dir=tmp_path)

        with pytest.raises(DetectionModelUnavailableError, match="digest"):
            service._resolve_model_path("yolo11n")

    def test_rejects_model_with_mismatched_digest(self, tmp_path: Path):
        model_path = tmp_path / "yolo11n.pt"
        model_path.write_bytes(b"untrusted checkpoint")
        service = DetectionService(models_dir=tmp_path, model_digests={"yolo11n": "0" * 64})

        with pytest.raises(DetectionModelUnavailableError, match="integrity"):
            service._resolve_model_path("yolo11n")


class TestCacheHelpers:
    @staticmethod
    def _detect_empty_episode(service: DetectionService, dataset_id: str, episode_idx: int) -> EpisodeDetectionSummary:
        async def get_frame_image(_frame_idx: int) -> None:
            return None

        return asyncio.run(
            service.detect_episode(
                dataset_id=dataset_id,
                episode_idx=episode_idx,
                request=DetectionRequest(),
                get_frame_image=get_frame_image,
                total_frames=0,
            )
        )

    def test_get_cached_returns_none_and_value(self, monkeypatch):
        service = DetectionService()
        monkeypatch.setattr(service, "_resolve_model_path", lambda _model_name: Path("yolo11n.pt"))

        assert service.get_cached("d", 0) is None
        summary = self._detect_empty_episode(service, "d", 0)

        assert service.get_cached("d", 0) is summary

    def test_clear_cache_hit_and_miss(self, monkeypatch):
        service = DetectionService()
        monkeypatch.setattr(service, "_resolve_model_path", lambda _model_name: Path("yolo11n.pt"))

        assert service.clear_cache("d", 0) is False
        self._detect_empty_episode(service, "d", 0)
        assert service.clear_cache("d", 0) is True
        assert service.get_cached("d", 0) is None

    def test_cache_evicts_least_recently_used_entry_at_capacity(self, monkeypatch):
        service = DetectionService(cache_max_size=2)
        monkeypatch.setattr(service, "_resolve_model_path", lambda _model_name: Path("yolo11n.pt"))

        first = self._detect_empty_episode(service, "d", 0)
        self._detect_empty_episode(service, "d", 1)
        assert service.get_cached("d", 0) is first
        third = self._detect_empty_episode(service, "d", 2)

        assert service.get_cached("d", 0) is first
        assert service.get_cached("d", 1) is None
        assert service.get_cached("d", 2) is third

    def test_cache_entry_expires_after_ttl(self, monkeypatch):
        now = 100.0
        service = DetectionService(cache_ttl_seconds=10, cache_timer=lambda: now)
        monkeypatch.setattr(service, "_resolve_model_path", lambda _model_name: Path("yolo11n.pt"))
        self._detect_empty_episode(service, "d", 0)

        now = 111.0

        assert service.get_cached("d", 0) is None


class TestEffectiveConfidence:
    def test_uses_service_default_when_request_omits_confidence(self):
        service = DetectionService(default_confidence=0.42)

        assert service.effective_confidence(DetectionRequest()) == 0.42

    def test_uses_request_override(self):
        service = DetectionService(default_confidence=0.42)

        assert service.effective_confidence(DetectionRequest(confidence=0.7)) == 0.7


class TestDetectFrame:
    def test_no_results(self):
        s = DetectionService()
        s._model = _FakeYOLOModel([])
        s._model_name = "yolo11n"
        out = asyncio.run(s.detect_frame(_png_bytes(), frame_idx=2))
        assert out.frame == 2
        assert out.detections == []

    def test_no_boxes(self):
        s = DetectionService()
        s._model = _FakeYOLOModel([_FakeResult(boxes=None)])
        s._model_name = "yolo11n"
        out = asyncio.run(s.detect_frame(_png_bytes(), frame_idx=0))
        assert out.detections == []

    def test_with_boxes_and_unknown_class(self):
        s = DetectionService()
        boxes = _FakeBoxes(
            classes=[0, 999],
            confs=[0.9, 0.5],
            xyxy=[[0.0, 0.0, 1.0, 1.0], [1.0, 1.0, 2.0, 2.0]],
        )
        s._model = _FakeYOLOModel([_FakeResult(boxes=boxes)])
        s._model_name = "yolo11n"
        out = asyncio.run(s.detect_frame(_png_bytes(), frame_idx=0))
        names = [d.class_name for d in out.detections]
        assert names == ["person", "class_999"]
        assert out.detections[0].confidence == pytest.approx(0.9)


class TestDetectEpisodeFull:
    def test_full_path_with_skips_exception_and_detections(self, monkeypatch):
        s = DetectionService()
        boxes = _FakeBoxes(classes=[0], confs=[0.8], xyxy=[[0.0, 0.0, 1.0, 1.0]])
        s._model = _FakeYOLOModel([_FakeResult(boxes=boxes)])
        s._model_name = "yolo11n"
        monkeypatch.setattr(s, "_resolve_model_path", lambda _model_name: Path("yolo11n.pt"))

        async def get_frame_image(idx: int):
            if idx in (1, 2, 3, 4):
                return None
            if idx == 7:
                raise RuntimeError("explode")
            return _png_bytes()

        summary = asyncio.run(
            s.detect_episode(
                dataset_id="d",
                episode_idx=0,
                request=DetectionRequest(),
                get_frame_image=get_frame_image,
                total_frames=8,
            )
        )
        assert summary.total_frames == 8
        assert summary.processed_frames == 3
        assert summary.total_detections == 3
        assert "person" in summary.class_summary
        assert summary.class_summary["person"].count == 3
        assert s.get_cached("d", 0) is summary

    def test_uses_configured_confidence_when_request_omits_override(self, monkeypatch):
        service = DetectionService(default_confidence=0.42)
        observed_confidence: list[float] = []

        async def fake_detect_frame(
            image_bytes: bytes,
            frame_idx: int,
            confidence: float,
            model_name: str,
            labels: list[str] | None,
        ) -> DetectionResult:
            observed_confidence.append(confidence)
            return DetectionResult(frame=frame_idx, detections=[], processing_time_ms=1.0)

        monkeypatch.setattr(service, "detect_frame", fake_detect_frame)
        monkeypatch.setattr(service, "_resolve_model_path", lambda _model_name: Path("yolo11n.pt"))

        async def get_frame_image(_frame_idx: int) -> bytes:
            return _png_bytes()

        asyncio.run(
            service.detect_episode(
                dataset_id="d",
                episode_idx=0,
                request=DetectionRequest(),
                get_frame_image=get_frame_image,
                total_frames=1,
            )
        )

        assert observed_confidence == [0.42]

    def test_request_confidence_overrides_configured_default(self, monkeypatch):
        service = DetectionService(default_confidence=0.42)
        observed_confidence: list[float] = []

        async def fake_detect_frame(
            image_bytes: bytes,
            frame_idx: int,
            confidence: float,
            model_name: str,
            labels: list[str] | None,
        ) -> DetectionResult:
            observed_confidence.append(confidence)
            return DetectionResult(frame=frame_idx, detections=[], processing_time_ms=1.0)

        monkeypatch.setattr(service, "detect_frame", fake_detect_frame)
        monkeypatch.setattr(service, "_resolve_model_path", lambda _model_name: Path("yolo11n.pt"))

        async def get_frame_image(_frame_idx: int) -> bytes:
            return _png_bytes()

        asyncio.run(
            service.detect_episode(
                dataset_id="d",
                episode_idx=0,
                request=DetectionRequest(confidence=0.7),
                get_frame_image=get_frame_image,
                total_frames=1,
            )
        )

        assert observed_confidence == [0.7]


class TestSingleton:
    def test_get_detection_service_returns_singleton(self, monkeypatch):
        monkeypatch.setattr(ds_module, "_detection_service", None)
        a = ds_module.get_detection_service()
        b = ds_module.get_detection_service()
        assert a is b
        assert isinstance(a, DetectionService)
