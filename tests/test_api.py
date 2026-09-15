"""Optional HTTP smoke tests; run with the app extra and the dev dependency group."""

import pytest

from amber.config import Settings

pytestmark = pytest.mark.usefixtures("isolated_settings")


def test_api_health_and_info() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from amber.api import create_app

    app = create_app(settings=Settings(_env_file=None, environment="test"))
    with TestClient(app) as http:
        assert http.get("/health").json() == {"status": "ok"}
        assert http.get("/api/v1/admin/health").json() == {"status": "ok"}
        response = http.get("/api/v1/admin/info")
        assert response.status_code == 200
        assert response.json()["environment"] == "test"
