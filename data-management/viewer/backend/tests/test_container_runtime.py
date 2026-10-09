"""Container packaging contract for the production backend."""

from __future__ import annotations

from pathlib import Path


def test_dependency_installation_is_isolated_from_runtime_layers() -> None:
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    base, stages = dockerfile.split("FROM base AS build\n", maxsplit=1)
    build, runtime = stages.split("FROM base AS runtime\n", maxsplit=1)

    install = "uv sync --frozen --no-dev --no-editable --inexact ${extras}"
    assert install in build
    assert 'ARG BACKEND_EXTRAS="azure,analysis,export,auth,yolo"' in build
    assert "tr ',' ' '" in build
    assert "uv sync" not in base
    assert "uv sync" not in runtime
    assert "COPY --from=build --chown=appuser:appuser /app/.venv /app/.venv" in runtime
    assert "chown -R" not in runtime
    assert runtime.count("COPY --from=build ") == 1
    assert "/root/.cache" not in runtime
    assert "COPY --chown=appuser:appuser data-management/viewer/backend/logging.json ./" in runtime
    assert "USER appuser" in runtime


def test_judge_profiles_install_from_their_own_lock_without_checkout_imports() -> None:
    backend = Path(__file__).resolve().parents[1]
    dockerfile = (backend / "Dockerfile").read_text()
    compose = (backend.parent / "docker-compose.yml").read_text()
    assert "WORKDIR /app/data-management/viewer/backend" in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/app/.venv" in dockerfile
    assert "COPY evaluation/vlm_judge/ /app/evaluation/vlm_judge/" in dockerfile
    assert 'ARG JUDGE_EXTRAS=""' in dockerfile
    assert "uv sync --project /app/evaluation/vlm_judge --frozen --no-dev --no-editable" in dockerfile
    assert "COPY data-management/viewer/backend/src/ ./src/" in dockerfile
    assert "PYTHONPATH" not in dockerfile
    assert "context: ../.." in compose.split("  frontend:")[0]
    assert "dockerfile: data-management/viewer/backend/Dockerfile" in compose


def test_build_upload_is_allowlisted_and_compose_keeps_jobs_durable() -> None:
    backend = Path(__file__).resolve().parents[1]
    ignore = backend / "Dockerfile.dockerignore"
    assert ignore.exists()
    patterns = ignore.read_text().splitlines()
    assert patterns[0] == "**"
    assert "!evaluation/vlm_judge/*.py" in patterns
    assert not any(line.startswith("!") and ".env" in line for line in patterns)
    compose = (backend.parent / "docker-compose.yml").read_text()
    assert 'VLM_JUDGE_JOB_DIR: "/state/judge"' in compose
    assert 'VLM_JUDGE_CACHE_DIR: "/scratch/judge"' in compose
    assert "judge-state:/state" in compose
    assert 'VLM_JUDGE_ENABLED: "${VLM_JUDGE_ENABLED:-false}"' in compose
    deploy = (backend.parents[1] / "setup" / "deploy-dataviewer.sh").read_text()
    assert '"$SRC_DIR/backend/"' not in deploy
    assert '--build-arg "JUDGE_EXTRAS=${judge_extras}"' in deploy
    assert 'cp "$backend_dockerfile" "$backend_context/data-management/viewer/backend/Dockerfile"' in deploy


def test_browser_gate_cannot_reuse_live_services_or_resolve_dependencies() -> None:
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    config = (frontend / "playwright.config.ts").read_text()
    assert "--with" not in config
    assert "reuseExistingServer: !process.env.CI" not in config
    assert "--no-sync --frozen" in config
    assert "mkdtempSync" in config
    assert "STORAGE_BACKEND: 'local'" in config
    assert "--port 18000" in config
    assert "--port 18001" in config
    assert "--port 18002" in config
    scenarios = (frontend / "e2e" / "accessibility.spec.ts").read_text()
    assert "http://127.0.0.1:8000/" not in scenarios
