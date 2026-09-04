"""FastAPI application factory."""

from fastapi import FastAPI

from amber import __version__
from amber.api.v1.router import router as v1_router
from amber.client import Amber, create_client
from amber.config import Settings, get_settings


def create_app(settings: Settings | None = None, client: Amber | None = None) -> FastAPI:
    """Build an isolated API application for production or tests."""

    resolved_settings = settings or get_settings()
    app = FastAPI(title="Amber API", version=__version__)
    app.state.amber = client or create_client(resolved_settings)
    app.include_router(v1_router, prefix=resolved_settings.api_prefix)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
