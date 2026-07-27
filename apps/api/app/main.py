"""FastAPI application factory.

Kept as a factory (rather than a module-level singleton with side effects) so
tests can build an app with overridden settings.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import documents, extract, health

#: Versioned prefix from day one - the frontend pins to it, so a v2 can ship
#: alongside v1 instead of breaking deployed clients.
API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="LoanDoc AI API",
        version=settings.api_version,
        description=(
            "Document ingestion, classification and field extraction for loan underwriting."
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # The browser calls this API cross-origin (localhost:3000 -> :8000), so the
    # allowed origins are configuration, never a wildcard.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(documents.router, prefix=API_PREFIX)
    app.include_router(extract.router, prefix=API_PREFIX)
    return app


app = create_app()
