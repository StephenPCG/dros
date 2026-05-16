from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dros.settings import DrosSettings, WebSettings
from dros.web.app import create_app
from dros.web.auth import WebAuthStore


def _settings(tmp_path: Path) -> DrosSettings:
    return DrosSettings(web=WebSettings(authDb=tmp_path / "web-auth.sqlite3"))


def test_login_sets_session_cookie_and_me_returns_user(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = WebAuthStore(settings.web.auth_db)
    store.create_user("alice", "secret")
    client = TestClient(create_app(settings))

    login = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret", "remember": False},
    )

    assert login.status_code == 200
    assert login.json() == {"username": "alice"}
    cookie = login.headers["set-cookie"]
    assert "dros_session=" in cookie
    assert "Max-Age" not in cookie

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"authenticated": True, "username": "alice"}


def test_long_login_cookie_lasts_90_days(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = WebAuthStore(settings.web.auth_db)
    store.create_user("alice", "secret")
    client = TestClient(create_app(settings))

    login = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret", "remember": True},
    )

    assert login.status_code == 200
    assert "Max-Age=7776000" in login.headers["set-cookie"]


def test_logout_removes_server_session(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = WebAuthStore(settings.web.auth_db)
    store.create_user("alice", "secret")
    client = TestClient(create_app(settings))
    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret", "remember": True},
    ).status_code == 200

    logout = client.post("/api/auth/logout")

    assert logout.status_code == 200
    assert logout.json() == {"ok": True}
    assert client.get("/api/auth/me").json() == {"authenticated": False}


def test_login_rejects_wrong_password(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = WebAuthStore(settings.web.auth_db)
    store.create_user("alice", "secret")
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "bad", "remember": True},
    )

    assert response.status_code == 401
    assert client.get("/api/auth/me").json() == {"authenticated": False}
