"""Unit tests for detection service episode processing behavior."""

import types

import pytest

from src.api.models.detection import DetectionRequest, DetectionResult
from src.api.services.detection_service import DetectionService


class TestDetectionEpisodeProcessing:
    """Tests for frame index handling in episode detection."""

    @pytest.mark.asyncio
    async def test_detect_episode_preserves_integer_frame_indices(self, monkeypatch):
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
        ) -> DetectionResult:
            assert image_bytes == b"image-bytes"
            assert isinstance(frame_idx, int)
            observed_indices.append(frame_idx)
            return DetectionResult(frame=frame_idx, detections=[], processing_time_ms=1.0)

        monkeypatch.setattr(DetectionService, "detect_frame", fake_detect_frame)

        summary = await service.detect_episode(
            dataset_id="dataset",
            episode_idx=0,
            request=DetectionRequest(frames=[1, 3]),
            get_frame_image=get_frame_image,
            total_frames=10,
        )

        assert observed_indices == [1, 1, 3, 3]
        assert [result.frame for result in summary.detections_by_frame] == [1, 3]
        assert summary.processed_frames == 2

    def test_get_model_logs_sanitized_model_name(self, monkeypatch):
        service = DetectionService()
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


# ---------------------------------------------------------------------------
# Synthetic-model tests for full coverage of detection_service branches.
# ---------------------------------------------------------------------------

import io  # noqa: E402

from PIL import Image as _PILImage  # noqa: E402

from src.api.models.detection import EpisodeDetectionSummary  # noqa: E402
from src.api.services import detection_service as ds_module  # noqa: E402


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    _PILImage.new("RGB", (8, 8), color=(0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


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

    def test_raises_on_import_error(self, monkeypatch):
        import builtins as _bi

        s = DetectionService()
        real_import = _bi.__import__

        def fake_import(name, *a, **kw):
            if name == "ultralytics":
                raise ImportError("no ultralytics")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(_bi, "__import__", fake_import)
        with pytest.raises(ImportError):
            s._get_model("yolo11n")


class TestCacheHelpers:
    def test_get_cached_returns_none_and_value(self):
        s = DetectionService()
        assert s.get_cached("d", 0) is None
        summary = EpisodeDetectionSummary(
            total_frames=1, processed_frames=0, total_detections=0, detections_by_frame=[], class_summary={}
        )
        s._cache[s._cache_key("d", 0)] = summary
        assert s.get_cached("d", 0) is summary

    def test_clear_cache_hit_and_miss(self):
        s = DetectionService()
        assert s.clear_cache("d", 0) is False
        s._cache[s._cache_key("d", 0)] = EpisodeDetectionSummary(
            total_frames=1, processed_frames=0, total_detections=0, detections_by_frame=[], class_summary={}
        )
        assert s.clear_cache("d", 0) is True
        assert s.get_cached("d", 0) is None


class TestDetectFrame:
    @pytest.mark.asyncio
    async def test_no_results(self):
        s = DetectionService()
        s._model = _FakeYOLOModel([])
        s._model_name = "yolo11n"
        out = await s.detect_frame(_png_bytes(), frame_idx=2)
        assert out.frame == 2
        assert out.detections == []

    @pytest.mark.asyncio
    async def test_no_boxes(self):
        s = DetectionService()
        s._model = _FakeYOLOModel([_FakeResult(boxes=None)])
        s._model_name = "yolo11n"
        out = await s.detect_frame(_png_bytes(), frame_idx=0)
        assert out.detections == []

    @pytest.mark.asyncio
    async def test_with_boxes_and_unknown_class(self):
        s = DetectionService()
        boxes = _FakeBoxes(
            classes=[0, 999],
            confs=[0.9, 0.5],
            xyxy=[[0.0, 0.0, 1.0, 1.0], [1.0, 1.0, 2.0, 2.0]],
        )
        s._model = _FakeYOLOModel([_FakeResult(boxes=boxes)])
        s._model_name = "yolo11n"
        out = await s.detect_frame(_png_bytes(), frame_idx=0)
        names = [d.class_name for d in out.detections]
        assert names == ["person", "class_999"]
        assert out.detections[0].confidence == pytest.approx(0.9)


class TestDetectEpisodeFull:
    @pytest.mark.asyncio
    async def test_full_path_with_skips_exception_and_detections(self):
        s = DetectionService()
        boxes = _FakeBoxes(classes=[0], confs=[0.8], xyxy=[[0.0, 0.0, 1.0, 1.0]])
        s._model = _FakeYOLOModel([_FakeResult(boxes=boxes)])
        s._model_name = "yolo11n"

        async def get_frame_image(idx: int):
            if idx in (1, 2, 3, 4):
                return None
            if idx == 7:
                raise RuntimeError("explode")
            return _png_bytes()

        summary = await s.detect_episode(
            dataset_id="d",
            episode_idx=0,
            request=DetectionRequest(),
            get_frame_image=get_frame_image,
            total_frames=8,
        )
        assert summary.total_frames == 8
        assert summary.processed_frames == 3
        assert summary.total_detections == 3
        assert "person" in summary.class_summary
        assert summary.class_summary["person"].count == 3
        assert s.get_cached("d", 0) is summary


class TestSingleton:
    def test_get_detection_service_returns_singleton(self, monkeypatch):
        monkeypatch.setattr(ds_module, "_detection_service", None)
        a = ds_module.get_detection_service()
        b = ds_module.get_detection_service()
        assert a is b
        assert isinstance(a, DetectionService)
