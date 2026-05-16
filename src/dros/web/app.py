from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from dros.settings import DrosSettings


def create_app(settings: DrosSettings | None = None) -> FastAPI:
    api = FastAPI(title="DROS Web API", version="0.1.0")

    @api.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
