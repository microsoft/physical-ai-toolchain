"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .auth import MediaSession, PrincipalContext, issue_media_session, require_auth, require_principal_context
from .csrf import CSRF_COOKIE_NAME, generate_csrf_token, require_csrf_token
from .middleware import ContentSizeLimitMiddleware, EpisodeCompressionMiddleware, SecurityHeadersMiddleware
from .rate_limiter import limiter
from .routers import analysis, annotations, datasets, detection, export, joint_config, labels, vlm_judge
from .routes import ai_analysis
from .storage import RevisionConflictError
from .swagger_ui import install_responsive_swagger_ui

# Match application verbosity to uvicorn's effective CLI level without replacing its handlers.
_log_level = logging.getLogger("uvicorn.error").level or logging.INFO
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s - %(levelname)s %(name)s - %(message)s",
)
logging.getLogger("src.api").setLevel(_log_level)

# Suppress verbose Azure SDK HTTP request logging
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)

# Load .env before any config or service singletons are initialized so that
# all env vars are available to get_app_config() on first access.
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

# Read config once at module load. CORS origins must be known before the app object
# is created, and all service singletons share this same config instance.
from .config import load_config  # noqa: E402

_config = load_config()

logger = logging.getLogger(__name__)


def _validate_local_storage_writable(data_path: str) -> None:
    """Verify local storage supports the atomic writes used for annotations."""
    file_descriptor: int | None = None
    temporary_path: str | None = None
    replacement_path: str | None = None

    try:
        file_descriptor, temporary_path = tempfile.mkstemp(
            dir=data_path,
            prefix=".dataviewer-write-",
            suffix=".tmp",
        )
        os.write(file_descriptor, b"dataviewer-storage-probe")
        os.fsync(file_descriptor)
        os.close(file_descriptor)
        file_descriptor = None

        replacement_path = f"{temporary_path}.replace"
        os.replace(temporary_path, replacement_path)
        temporary_path = None
        os.unlink(replacement_path)
        replacement_path = None
    except OSError as exc:
        sanitized_path = data_path.replace("\r", "").replace("\n", "")
        effective_uid = os.geteuid() if hasattr(os, "geteuid") else -1
        effective_gid = os.getegid() if hasattr(os, "getegid") else -1
        raise RuntimeError(
            f"Local storage path '{sanitized_path}' is not writable by effective UID {effective_uid} "
            f"and GID {effective_gid}. Grant the process write access. For rootful Docker on Linux or WSL, "
            "set DATAVIEWER_UID and DATAVIEWER_GID to the results of 'id -u' and 'id -g'."
        ) from exc
    finally:
        if file_descriptor is not None:
            with suppress(OSError):
                os.close(file_descriptor)
        for path in (temporary_path, replacement_path):
            if path is not None:
                with suppress(OSError):
                    os.unlink(path)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Validate local storage and clean up blob sync directories."""
    if _config.storage_backend == "local":
        await asyncio.to_thread(_validate_local_storage_writable, _config.data_path)

    yield
    from .services.dataset_service import get_dataset_service

    try:
        service = get_dataset_service()
        service.cleanup_temp_dirs()
        logger.info("Cleaned up blob sync temp directories")
    except Exception:
        pass  # Best-effort cleanup; failure here must not block shutdown


app = FastAPI(
    title="LeRobot Annotation API",
    description="API for episode annotation in robot demonstration datasets",
    version="0.1.0",
    docs_url=None,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "auth", "description": "Authentication utilities"},
    ],
    # OpenAPI security scheme definitions
    components={
        "securitySchemes": {
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": "API key authentication (DATAVIEWER_AUTH_PROVIDER=apikey)",
            },
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Bearer JWT authentication (azure_ad / auth0 providers)",
            },
        }
    },
)
install_responsive_swagger_ui(app)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(RevisionConflictError)
async def revision_conflict_handler(_request, exc: RevisionConflictError) -> JSONResponse:
    """Return the current revision when a conditional write is stale."""
    headers = {"ETag": exc.current_etag} if exc.current_etag else None
    return JSONResponse(
        status_code=412,
        content={
            "code": "PRECONDITION_FAILED",
            "message": "Resource revision precondition failed",
            "details": {"currentEtag": exc.current_etag},
        },
        headers=headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception) -> JSONResponse:
    """Log full traceback server-side, return generic error to client."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError) -> JSONResponse:
    """Return validation errors without internal paths."""
    errors = [{"loc": error.get("loc"), "msg": error.get("msg"), "type": error.get("type")} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


# Middleware stack (last added = outermost = first to execute)
# Order: SecurityHeaders → ContentSizeLimit → CORS → FastAPI App
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(EpisodeCompressionMiddleware, minimum_size=1024, compresslevel=1)
app.add_middleware(
    ContentSizeLimitMiddleware,
    max_content_length=int(os.environ.get("MAX_REQUEST_BODY_BYTES", str(10 * 1024 * 1024))),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_config.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-API-Key", "X-Request-ID"],
)

# All /api/* routes require authentication (health and csrf-token are on app directly)
api_auth = [Depends(require_auth)]
app.include_router(export.router, prefix="/api/datasets", tags=["export"], dependencies=api_auth)
app.include_router(detection.router, prefix="/api/datasets", tags=["detection"], dependencies=api_auth)
app.include_router(datasets.router, prefix="/api/datasets", tags=["datasets"], dependencies=api_auth)
app.include_router(annotations.router, prefix="/api", tags=["annotations"], dependencies=api_auth)
app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"], dependencies=api_auth)
app.include_router(ai_analysis.router, prefix="/api", tags=["ai"], dependencies=api_auth)
app.include_router(labels.router, prefix="/api/datasets", tags=["labels"], dependencies=api_auth)
app.include_router(joint_config.router, prefix="/api/datasets", tags=["joint-config"], dependencies=api_auth)
app.include_router(joint_config.defaults_router, prefix="/api", tags=["joint-config"], dependencies=api_auth)
if _config.vlm_judge_enabled:
    app.include_router(
        vlm_judge.router,
        prefix="/api/datasets",
        tags=["vlm-judge"],
        dependencies=api_auth,
    )


@app.get("/health")
async def health_check():
    """Health check verifying API and storage connectivity."""
    checks: dict[str, str] = {"api": "healthy"}

    try:
        from .services.dataset_service import get_dataset_service

        service = get_dataset_service()
        # In Azure mode the local base_path is irrelevant; treat the blob
        # provider's presence as the storage health signal.
        if _config.storage_backend == "azure":
            checks["storage"] = "healthy" if service._blob_provider is not None else "unhealthy"
        elif hasattr(service, "base_path"):
            from pathlib import Path as _Path

            checks["storage"] = "healthy" if _Path(service.base_path).exists() else "unhealthy"
        else:
            checks["storage"] = "healthy"
    except Exception:
        checks["storage"] = "unhealthy"

    overall = "healthy" if all(v == "healthy" for v in checks.values()) else "degraded"
    status_code = 200 if overall == "healthy" else 503
    return JSONResponse(content={"status": overall, "checks": checks}, status_code=status_code)


@app.get("/api/csrf-token", tags=["auth"])
async def get_csrf_token() -> JSONResponse:
    """Return a CSRF token and set it as a ``csrf_token`` cookie.

    Clients should call this endpoint once on application start, then include
    the returned token in the ``X-CSRF-Token`` header for every state-changing
    request (POST / PUT / PATCH / DELETE).
    """
    token = generate_csrf_token()
    # httponly=False is intentional: the double-submit cookie pattern requires
    # the client to read the cookie value and echo it in the X-CSRF-Token header.
    # This makes the cookie readable by JavaScript; in environments where XSS is
    # a concern, ensure a strong CSP is configured in addition to CSRF protection.
    secure = os.environ.get("DATAVIEWER_SECURE_COOKIES", "false").lower() == "true"
    response = JSONResponse(content={"csrf_token": token})
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,
        samesite="strict",
        secure=secure,
        path="/",
    )
    return response


@app.get("/api/auth/context", response_model=PrincipalContext, tags=["auth"])
async def get_auth_context(
    principal: PrincipalContext = Depends(require_principal_context),
) -> PrincipalContext:
    """Return the opaque ownership scope for the authenticated principal."""
    return principal


@app.post(
    "/api/auth/media-session",
    response_model=MediaSession,
    tags=["auth"],
    dependencies=[Depends(require_csrf_token)],
)
async def create_media_session(
    request: Request,
    response: Response,
    user: dict[str, Any] | None = Depends(require_auth),
) -> MediaSession:
    """Authorize native same-origin images and range-based video without URL credentials."""
    return issue_media_session(request, response, user)
