from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from dros.invocation_log import read_error_logs, read_invocation_logs
from dros.ovpn import (
    create_client_profile,
    create_server_profile,
    download_profile_file,
    list_instances_summary,
    list_profile_certs,
    list_profiles_summary,
    renew_client,
    renew_server,
    revoke_profile_cert,
    update_client,
    update_server,
)
from dros.settings import DrosSettings
from dros.web.auth import COOKIE_NAME, WebAuthStore, resolve_auth_db_path
from dros.web.monitor import collect_monitor_summary
from dros.web.rrd import collect_bandwidth_series, collect_ping_series, collect_rrd_targets


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = False


class OpenVPNCreateProfileRequest(BaseModel):
    kind: Literal["server", "client"]
    name: str
    endpoint: str | None = None
    cn: str | None = None
    network: str | None = None
    netmask: str | None = None


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, object]) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND and "." not in Path(path).name:
                return await super().get_response("index.html", scope)
            raise


def create_app(settings: DrosSettings | None = None) -> FastAPI:
    if settings is None:
        settings = DrosSettings()

    api = FastAPI(title="DROS Web API", version="0.1.0")
    auth_store = WebAuthStore(resolve_auth_db_path(settings))

    def require_auth(
        session_token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
    ) -> str:
        username = auth_store.username_for_session(session_token)
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        return username

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

    @api.get("/api/openvpn/instances")
    def openvpn_instances(_username: str = Depends(require_auth)) -> dict[str, object]:
        return {
            "instances": [
                {
                    "name": item.name,
                    "root": item.root,
                    "serverProfiles": item.server_profiles,
                    "clientProfiles": item.client_profiles,
                    "serverCerts": item.server_certs,
                    "clientCerts": item.client_certs,
                    "crlExists": item.crl_exists,
                }
                for item in list_instances_summary(settings)
            ]
        }

    @api.get("/api/monitor/summary")
    def monitor_summary(_username: str = Depends(require_auth)) -> dict[str, object]:
        return collect_monitor_summary(settings)

    @api.get("/api/monitor/rrd/targets")
    def monitor_rrd_targets(_username: str = Depends(require_auth)) -> dict[str, object]:
        return collect_rrd_targets(settings)

    @api.get("/api/monitor/rrd/bandwidth")
    def monitor_rrd_bandwidth(
        target: str,
        timespan: str = "1h",
        _username: str = Depends(require_auth),
    ) -> dict[str, object]:
        try:
            return collect_bandwidth_series(settings, target=target, timespan=timespan)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @api.get("/api/monitor/rrd/ping")
    def monitor_rrd_ping(
        target: str,
        timespan: str = "1h",
        _username: str = Depends(require_auth),
    ) -> dict[str, object]:
        try:
            return collect_ping_series(settings, target=target, timespan=timespan)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @api.get("/api/logs/invocations")
    def logs_invocations(
        limit: int = 200,
        _username: str = Depends(require_auth),
    ) -> dict[str, object]:
        return {"records": read_invocation_logs(settings, limit=limit)}

    @api.get("/api/logs/errors")
    def logs_errors(
        limit: int = 200,
        _username: str = Depends(require_auth),
    ) -> dict[str, object]:
        return {"records": read_error_logs(settings, limit=limit)}

    @api.get("/api/openvpn/instances/{instance}/profiles")
    def openvpn_profiles(
        instance: str,
        _username: str = Depends(require_auth),
    ) -> dict[str, object]:
        try:
            profiles = list_profiles_summary(settings, instance=instance)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {
            "instance": instance,
            "profiles": [
                {
                    "kind": item.kind,
                    "name": item.name,
                    "path": item.path,
                    "latestCert": item.latest_cert,
                    "outputFiles": list(item.output_files),
                }
                for item in profiles
            ],
        }

    @api.post("/api/openvpn/instances/{instance}/profiles")
    def openvpn_create_profile(
        instance: str,
        payload: OpenVPNCreateProfileRequest,
        _username: str = Depends(require_auth),
    ) -> dict[str, object]:
        try:
            if payload.kind == "server":
                profile = create_server_profile(
                    settings,
                    payload.name,
                    instance=instance,
                    endpoint=payload.endpoint,
                    cn=payload.cn,
                    network=payload.network,
                    netmask=payload.netmask,
                )
                outputs = update_server(settings, payload.name, instance=instance)
            else:
                profile = create_client_profile(
                    settings,
                    payload.name,
                    instance=instance,
                    cn=payload.cn,
                )
                outputs = update_client(settings, payload.name, instance=instance)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {
            "ok": True,
            "profile": {"kind": profile.kind, "name": profile.name},
            "outputs": [str(path) for path in outputs],
        }

    @api.post("/api/openvpn/instances/{instance}/profiles/{kind}/{name}/renew")
    def openvpn_renew_profile(
        instance: str,
        kind: str,
        name: str,
        _username: str = Depends(require_auth),
    ) -> dict[str, object]:
        try:
            if kind == "server":
                outputs = renew_server(settings, name, instance=instance)
            elif kind == "client":
                outputs = renew_client(settings, name, instance=instance)
            else:
                raise ValueError("profile kind must be one of: server, client")
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"ok": True, "outputs": [str(path) for path in outputs]}

    @api.get("/api/openvpn/instances/{instance}/profiles/{kind}/{name}/certs")
    def openvpn_profile_certs(
        instance: str,
        kind: str,
        name: str,
        _username: str = Depends(require_auth),
    ) -> dict[str, object]:
        try:
            certs = list_profile_certs(settings, instance, kind, name)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {
            "instance": instance,
            "kind": kind,
            "name": name,
            "certs": [
                {
                    "certId": item.cert_id,
                    "path": item.path,
                    "latest": item.latest,
                    "revoked": item.revoked,
                }
                for item in certs
            ],
        }

    @api.post("/api/openvpn/instances/{instance}/profiles/{kind}/{name}/certs/{cert_id}/revoke")
    def openvpn_revoke_profile_cert(
        instance: str,
        kind: str,
        name: str,
        cert_id: str,
        _username: str = Depends(require_auth),
    ) -> dict[str, object]:
        try:
            crl = revoke_profile_cert(settings, instance=instance, kind=kind, name=name, cert_id=cert_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"ok": True, "crl": str(crl)}

    @api.get("/api/openvpn/instances/{instance}/profiles/{kind}/{name}/download")
    def openvpn_download_profile(
        instance: str,
        kind: str,
        name: str,
        _username: str = Depends(require_auth),
        file: str | None = None,
    ) -> FileResponse:
        try:
            path = download_profile_file(settings, instance=instance, kind=kind, name=name, file_name=file)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return FileResponse(path, filename=path.name)

    if settings and settings.web.static_dir and settings.web.static_dir.exists():
        api.mount("/", SPAStaticFiles(directory=settings.web.static_dir, html=True), name="web")
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
