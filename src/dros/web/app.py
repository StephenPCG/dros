from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, FastAPI, HTTPException, Response, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dros.settings import DrosSettings
from dros.web.auth import COOKIE_NAME, WebAuthStore, resolve_auth_db_path


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = False


def create_app(settings: DrosSettings | None = None) -> FastAPI:
    if settings is None:
        settings = DrosSettings()

    api = FastAPI(title="DROS Web API", version="0.1.0")
    auth_store = WebAuthStore(resolve_auth_db_path(settings))

    @api.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/api/auth/me")
    def auth_me(
        session_token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
    ) -> dict[str, str | bool]:
        username = auth_store.username_for_session(session_token)
        if username is None:
            return {"authenticated": False}
        return {"authenticated": True, "username": username}

    @api.post("/api/auth/login")
    def auth_login(payload: LoginRequest, response: Response) -> dict[str, str]:
        if not auth_store.verify_password(payload.username, payload.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid username or password",
            )
        session = auth_store.create_session(payload.username, persistent=payload.remember)
        response.set_cookie(
            COOKIE_NAME,
            session.token,
            max_age=session.max_age,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return {"username": session.username}

    @api.post("/api/auth/logout")
    def auth_logout(
        response: Response,
        session_token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
    ) -> dict[str, bool]:
        auth_store.delete_session(session_token)
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    if settings and settings.web.static_dir and settings.web.static_dir.exists():
        api.mount("/", StaticFiles(directory=settings.web.static_dir, html=True), name="web")
    else:

        @api.get("/", response_class=HTMLResponse)
        def index() -> str:
            return """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>DROS</title>
  </head>
  <body>
    <main style="font-family: system-ui, sans-serif; margin: 3rem;">
      <h1>DROS</h1>
      <p>DROS Web skeleton is running.</p>
    </main>
  </body>
</html>
"""

    return api


app = create_app()
