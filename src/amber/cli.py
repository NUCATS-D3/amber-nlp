"""amber CLI: cases | run | evaluate | register-prompts | train | export.
Thin over the modules above.
"""

import click


@click.group()
def cli() -> None:
    """Grounded clinical information extraction."""


if __name__ == "__main__":
    cli()
