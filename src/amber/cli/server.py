"""Commands for running Amber's HTTP API."""

import click

from amber.config import get_settings


@click.group()
def api() -> None:
    """Run and inspect the FastAPI service."""


@api.command("serve")
@click.option("--host", help="Bind address; defaults to AMBER_API_HOST.")
@click.option("--port", type=click.IntRange(1, 65535), help="Port; defaults to AMBER_API_PORT.")
@click.option("--reload", is_flag=True, help="Reload the process when source files change.")
def serve(host: str | None, port: int | None, reload: bool) -> None:
    """Start the FastAPI development server."""

    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - depends on optional installation
        raise click.ClickException(
            "Install the API dependencies with: uv sync --extra app"
        ) from exc

    settings = get_settings()
    uvicorn.run(
        "amber.api.app:create_app",
        factory=True,
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
        log_level=settings.log_level,
    )
