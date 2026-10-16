"""Behavioral tests for the FastAPI router."""

from __future__ import annotations

import base64
import io
import sys
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pytest_mock import MockerFixture


class TestAccessibilityDocumentationApps:
    def test_openai_echo_app_exposes_safe_documentation_and_completion(self) -> None:
        # Arrange
        from vlm_judge.openai_shim import build_echo_app

        # Act
        with TestClient(build_echo_app()) as documentation_client:
            docs_response = documentation_client.get("/docs")
            redoc_response = documentation_client.get("/redoc")
            completion_response = documentation_client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "Return a deterministic test response"}]},
            )

        # Assert
        assert docs_response.status_code == 200
        assert redoc_response.status_code == 200
        assert completion_response.status_code == 200
        assert completion_response.json()["model"] == "echo"


def test_given_echo_shim_when_querying_health_and_models_then_lists_the_served_model() -> None:
    from vlm_judge.openai_shim import build_echo_app

    with TestClient(build_echo_app()) as client:
        health = client.get("/health")
        models = client.get("/v1/models")

    assert health.json() == {"status": "ok", "model": "echo"}
    assert models.json()["object"] == "list"
    assert models.json()["data"][0]["id"] == "echo"


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        ({"messages": [], "stream": True}, 400, "Streaming is not supported"),
        ({"messages": [], "max_tokens": 0}, 422, ""),
        (
            {
                "messages": [
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "file:///example.png"}}]}
                ]
            },
            400,
            "Unsupported image_url scheme",
        ),
        (
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": "https://example.invalid/image.png"}}],
                    }
                ]
            },
            400,
            "Remote image URLs are disabled",
        ),
    ],
    ids=["streaming", "invalid-token-limit", "file-url", "remote-url"],
)
def test_given_unsupported_shim_request_when_completing_then_rejects_request(
    body: dict[str, object], status: int, detail: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vlm_judge import openai_shim

    monkeypatch.setattr(openai_shim, "_ALLOW_REMOTE_IMAGES", False)
    with TestClient(openai_shim.build_echo_app()) as client:
        response = client.post("/v1/chat/completions", json=body)

    assert response.status_code == status
    if detail:
        assert detail in response.json()["detail"]


def test_given_multimodal_history_when_completing_then_decodes_frames_and_returns_echo_progress() -> None:
    from vlm_judge.openai_shim import build_echo_app

    buffer = io.BytesIO()
    Image.new("RGBA", (10, 12), "red").save(buffer, "PNG")
    image_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    body = {
        "messages": [
            {"role": "system", "content": "Observe the frames"},
            {"role": "assistant", "content": "Previous observation"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe progress"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ]
    }
    with TestClient(build_echo_app()) as client:
        response = client.post("/v1/chat/completions", json=body)

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "[0, 100]"


def test_given_shim_launch_arguments_when_starting_then_forwards_model_and_server_configuration(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from vlm_judge import openai_shim

    uvicorn = ModuleType("uvicorn")
    serve = mocker.Mock()
    uvicorn.run = serve
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    model = mocker.patch.object(openai_shim, "Qwen3VLBackend", return_value=openai_shim.EchoBackend())

    result = openai_shim.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--model-id",
            "synthetic-model",
            "--model-revision",
            "synthetic-revision",
            "--device-map",
            "cpu",
            "--dtype",
            "float32",
        ]
    )

    assert result == 0
    model.assert_called_once_with(
        model_id="synthetic-model", revision="synthetic-revision", device_map="cpu", dtype="float32"
    )
    assert serve.call_args.kwargs == {"host": "127.0.0.1", "port": 8123, "log_level": "info"}
    with TestClient(serve.call_args.args[0]) as client:
        assert client.get("/health").json()["model"] == "echo"
