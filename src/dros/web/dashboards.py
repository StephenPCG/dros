from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dros.settings import DrosSettings


class DashboardState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    dashboards: list[dict[str, Any]] = Field(default_factory=list)
    active_dashboard_id: str | None = Field(None, alias="activeDashboardId")


def dashboard_state_path(settings: DrosSettings) -> Path:
    return _settings_path(settings, settings.paths.run) / "web/dashboards.json"


def load_dashboard_state(settings: DrosSettings) -> DashboardState:
    path = dashboard_state_path(settings)
    if not path.exists():
        return DashboardState()
    with path.open("r", encoding="utf-8") as handle:
        return DashboardState.model_validate(json.load(handle))


def save_dashboard_state(settings: DrosSettings, state: DashboardState) -> DashboardState:
    path = dashboard_state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    data = state.model_dump(mode="json", by_alias=True)
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp_path.replace(path)
    return state


def _settings_path(settings: DrosSettings, path: Path | str) -> Path:
    logical = Path(path)
    if not logical.is_absolute() or settings.sys_root == Path("/"):
        return logical
    return settings.sys_root / logical.relative_to("/")
