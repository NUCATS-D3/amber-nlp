"""Top-level Click application."""

import json

import click

from amber import create_client
from amber.cli.server import api


@click.group()
@click.version_option(package_name="amber")
def cli() -> None:
    """Grounded clinical information extraction."""


@cli.command()
@click.option("as_json", "--json", is_flag=True, help="Emit machine-readable JSON.")
def info(as_json: bool) -> None:
    """Show the active Amber version and environment."""

    system_info = create_client().info()
    if as_json:
        click.echo(json.dumps(system_info.model_dump(), sort_keys=True))
        return
    click.echo(f"amber {system_info.version} ({system_info.environment})")


cli.add_command(api)


if __name__ == "__main__":
    cli()
