"""FastAPI application entry point."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .auth import require_auth
from .csrf import CSRF_COOKIE_NAME, generate_csrf_token
from .middleware import ContentSizeLimitMiddleware, SecurityHeadersMiddleware
from .rate_limiter import limiter
from .routers import analysis, annotations, datasets, detection, export, joint_config, labels
from .routes import ai_analysis

# Configure logging to show INFO level
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

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


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Clean up blob sync temp directories on shutdown."""
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

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


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


@app.get("/health")
async def health_check():
    """Health check verifying API and storage connectivity."""
    checks: dict[str, str] = {"api": "healthy"}

    try:
        from .services.dataset_service import get_dataset_service

        service = get_dataset_service()
        if hasattr(service, "base_path"):
            from pathlib import Path as _Path

            if _Path(service.base_path).exists():
                checks["storage"] = "healthy"
            else:
                checks["storage"] = "unhealthy"
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
