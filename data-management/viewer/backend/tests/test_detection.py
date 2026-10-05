"""Behavior tests for the object detection service."""

from __future__ import annotations

import hashlib
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from src.api.services.detection_service import DEFAULT_OPEN_VOCAB_MODEL, DetectionService

_MODEL_BYTES = b"deterministic model"


def _jpeg_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 24), color=(128, 128, 128)).save(buffer, format="JPEG")
    return buffer.getvalue()


class _FakeTensor:
    def __init__(self, value: int | float) -> None:
        self._value = value

    def item(self) -> int | float:
        return self._value


class _FakeCoordinates:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return self._values


class _FakeBoxes:
    def __init__(
        self,
        classes: list[int],
        confidences: list[float],
        coordinates: list[list[float]],
    ) -> None:
        self.cls = [_FakeTensor(class_id) for class_id in classes]
        self.conf = [_FakeTensor(confidence) for confidence in confidences]
        self.xyxy = [_FakeCoordinates(values) for values in coordinates]

    def __len__(self) -> int:
        return len(self.cls)


class _FakeModel:
    def __init__(
        self,
        boxes: _FakeBoxes | None = None,
        names: dict[int, str] | list[str] | None = None,
        *,
        return_result: bool | None = None,
    ) -> None:
        self.names = names if names is not None else {}
        self._boxes = boxes
        self._return_result = boxes is not None if return_result is None else return_result
        self.calls: list[tuple[object, dict[str, object]]] = []
        self.class_updates: list[list[str]] = []

    def __call__(self, image: object, **kwargs: object) -> list[SimpleNamespace]:
        self.calls.append((image, kwargs))
        if isinstance(image, np.ndarray):
            return []
        if not self._return_result:
            return []
        return [SimpleNamespace(boxes=self._boxes)]

    def set_classes(self, labels: list[str]) -> None:
        self.class_updates.append(labels)
        self.names = list(labels)


class _FakeYOLOFactory:
    def __init__(self, model: _FakeModel) -> None:
        self.model = model
        self.model_paths: list[str] = []

    def __call__(self, model_path: str) -> _FakeModel:
        self.model_paths.append(model_path)
        return self.model


def _install_fake_ultralytics(
    monkeypatch: pytest.MonkeyPatch,
    model: _FakeModel,
) -> _FakeYOLOFactory:
    factory = _FakeYOLOFactory(model)
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=factory))
    return factory


def _detection_service(tmp_path: Path, *model_names: str) -> DetectionService:
    digests = {}
    for model_name in model_names:
        model_path = tmp_path / f"{model_name}.pt"
        model_path.write_bytes(_MODEL_BYTES)
        digests[model_name] = hashlib.sha256(_MODEL_BYTES).hexdigest()
    return DetectionService(models_dir=tmp_path, model_digests=digests)


@pytest.mark.asyncio
async def test_detect_frame_loads_model_and_returns_exact_empty_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model = _FakeModel(names={0: "person"})
    factory = _install_fake_ultralytics(monkeypatch, model)
    service = _detection_service(tmp_path, "yolo11n")

    result = await service.detect_frame(_jpeg_bytes(), frame_idx=7, confidence=0.4)

    assert result.frame == 7
    assert result.detections == []
    assert result.processing_time_ms >= 0
    assert len(factory.model_paths) == 1
    assert Path(factory.model_paths[0]).read_bytes() == _MODEL_BYTES
    assert len(model.calls) == 2
    warmup_image, warmup_kwargs = model.calls[0]
    assert isinstance(warmup_image, np.ndarray)
    assert warmup_image.shape == (640, 640, 3)
    assert warmup_image.dtype == np.uint8
    assert warmup_kwargs == {"verbose": False}
    inference_image, inference_kwargs = model.calls[1]
    assert isinstance(inference_image, Image.Image)
    assert inference_image.size == (32, 24)
    assert inference_kwargs == {"conf": 0.4, "verbose": False}


@pytest.mark.asyncio
async def test_detect_frame_maps_model_names_and_fallback_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    boxes = _FakeBoxes(
        classes=[2, 0, 999],
        confidences=[0.875, 0.75, 0.5],
        coordinates=[
            [1.0, 2.0, 20.0, 22.0],
            [3.0, 4.0, 10.0, 12.0],
            [5.0, 6.0, 15.0, 16.0],
        ],
    )
    model = _FakeModel(boxes=boxes, names={2: "custom-class"})
    _install_fake_ultralytics(monkeypatch, model)

    service = _detection_service(tmp_path, "yolo11n")
    result = await service.detect_frame(_jpeg_bytes(), frame_idx=3, confidence=0.25)

    assert [detection.model_dump() for detection in result.detections] == [
        {
            "class_id": 2,
            "class_name": "custom-class",
            "confidence": 0.875,
            "bbox": (1.0, 2.0, 20.0, 22.0),
        },
        {
            "class_id": 0,
            "class_name": "person",
            "confidence": 0.75,
            "bbox": (3.0, 4.0, 10.0, 12.0),
        },
        {
            "class_id": 999,
            "class_name": "class_999",
            "confidence": 0.5,
            "bbox": (5.0, 6.0, 15.0, 16.0),
        },
    ]


@pytest.mark.asyncio
async def test_detect_frame_returns_empty_result_when_model_returns_no_boxes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model = _FakeModel(names=["person"], return_result=True)
    _install_fake_ultralytics(monkeypatch, model)

    service = _detection_service(tmp_path, "yolo11n")
    result = await service.detect_frame(_jpeg_bytes(), frame_idx=5)

    assert result.frame == 5
    assert result.detections == []
    assert result.processing_time_ms >= 0


@pytest.mark.asyncio
async def test_detect_frame_uses_open_vocabulary_model_and_reuses_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    boxes = _FakeBoxes(classes=[0], confidences=[0.9], coordinates=[[0.0, 1.0, 2.0, 3.0]])
    model = _FakeModel(boxes=boxes)
    factory = _install_fake_ultralytics(monkeypatch, model)
    service = _detection_service(tmp_path, DEFAULT_OPEN_VOCAB_MODEL)

    first = await service.detect_frame(_jpeg_bytes(), frame_idx=1, labels=["widget"])
    second = await service.detect_frame(_jpeg_bytes(), frame_idx=2, labels=["gadget"])

    assert len(factory.model_paths) == 1
    assert Path(factory.model_paths[0]).read_bytes() == _MODEL_BYTES
    assert model.class_updates == [["widget"], ["gadget"]]
    assert [detection.class_name for detection in first.detections] == ["widget"]
    assert [detection.class_name for detection in second.detections] == ["gadget"]
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_detect_frame_surfaces_missing_model_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "ultralytics", None)

    with pytest.raises(ModuleNotFoundError, match="import of ultralytics halted"):
        await DetectionService().detect_frame(_jpeg_bytes(), frame_idx=0)
