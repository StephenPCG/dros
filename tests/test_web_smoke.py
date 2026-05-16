from __future__ import annotations

from fastapi.testclient import TestClient

from dros.web.app import create_app


def test_web_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
