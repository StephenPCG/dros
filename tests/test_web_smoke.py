from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dros.settings import DrosSettings, WebSettings
from dros.web.app import create_app


def test_web_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_web_static_serves_spa_fallback_for_page_routes(tmp_path: Path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<main>DROS app</main>", encoding="utf-8")
    settings = DrosSettings(web=WebSettings(staticDir=static_dir))
    client = TestClient(create_app(settings))

    response = client.get("/openvpn")

    assert response.status_code == 200
    assert "DROS app" in response.text


def test_web_static_keeps_missing_assets_as_404(tmp_path: Path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<main>DROS app</main>", encoding="utf-8")
    settings = DrosSettings(web=WebSettings(staticDir=static_dir))
    client = TestClient(create_app(settings))

    response = client.get("/assets/missing.js")

    assert response.status_code == 404
