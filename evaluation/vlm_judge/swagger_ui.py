"""Responsive Swagger UI route configuration."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

_REFLOW_STYLES = """
<style>
@media (max-width: 600px) {
  .swagger-ui .wrapper { padding-inline: 10px; }
  .swagger-ui table { table-layout: fixed; width: 100%; }
  .swagger-ui table .response-col_status { width: 48px; }
  .swagger-ui table .response-col_links { display: none; }
  .swagger-ui table .response-col_description { width: auto; }
  .swagger-ui .model-example,
  .swagger-ui .response-col_description__inner { min-width: 0; max-width: 100%; }
  .swagger-ui .highlight-code,
  .swagger-ui .microlight,
  .swagger-ui pre { max-width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; }
}
</style>
"""


def install_responsive_swagger_ui(app: FastAPI) -> None:
    """Install Swagger UI with narrow-viewport response reflow."""

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        response = get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - Swagger UI",
        )
        content = response.body.decode("utf-8").replace("</head>", f"{_REFLOW_STYLES}</head>")
        return HTMLResponse(content=content)
