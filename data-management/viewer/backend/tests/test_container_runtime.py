"""Container packaging contract for the production backend."""

from __future__ import annotations

from pathlib import Path


def test_dependency_installation_is_isolated_from_runtime_layers() -> None:
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    base, stages = dockerfile.split("FROM base AS build\n", maxsplit=1)
    build, runtime = stages.split("FROM base AS runtime\n", maxsplit=1)

    install = "uv sync --frozen --no-dev ${extras}"
    assert install in build
    assert 'ARG BACKEND_EXTRAS="azure,analysis,export,auth,yolo"' in build
    assert "tr ',' ' '" in build
    assert "uv sync" not in base
    assert "uv sync" not in runtime
    assert "COPY --from=build --chown=appuser:appuser /app/.venv /app/.venv" in runtime
    assert "chown -R" not in runtime
    assert runtime.count("COPY --from=build ") == 1
    assert "/root/.cache" not in runtime
    assert "COPY --chown=appuser:appuser pyproject.toml uv.lock logging.json ./" in runtime
    assert "COPY --chown=appuser:appuser src/ ./src/" in runtime
    assert "USER appuser" in runtime
