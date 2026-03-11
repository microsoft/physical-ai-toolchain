"""FastAPI application entry point."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth import require_auth
from .csrf import CSRF_COOKIE_NAME, generate_csrf_token
from .routers import analysis, annotations, datasets, detection, export, joint_config, labels
from .routes import ai_analysis

# Configure logging to show INFO level
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Load .env before any config or service singletons are initialized so that
# all env vars are available to get_app_config() on first access.
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

# Read config once at module load. CORS origins must be known before the app object
# is created, and all service singletons share this same config instance.
from .config import load_config  # noqa: E402

_config = load_config()

app = FastAPI(
    title="LeRobot Annotation API",
    description="API for episode annotation in robot demonstration datasets",
    version="0.1.0",
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

# Configure CORS — origins are read from CORS_ORIGINS env var (comma-separated)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    """Health check endpoint."""
    return {"status": "healthy"}


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
    )
    return response
