"""FastAPI dependencies shared by versioned routers."""

from typing import Annotated

from fastapi import Depends, Request

from amber.client import Amber


def get_amber(request: Request) -> Amber:
    """Return the application-scoped Amber façade."""

    return request.app.state.amber


AmberDep = Annotated[Amber, Depends(get_amber)]
