from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SETTINGS_PATH = Path("/etc/dros/settings.yaml")


class DrosPaths(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    configs: Path | list[Path] = Path("/opt/gateway/configs")
    logs: Path = Path("/opt/gateway/logs")
    run: Path = Path("/opt/gateway/run")
    containers: Path = Path("/opt/gateway/containers")

    def config_dirs(self) -> list[Path]:
        if isinstance(self.configs, list):
            return [Path(path) for path in self.configs]
        return [Path(self.configs)]


class DaemonSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    socket_path: Path = Field(Path("/opt/gateway/run/drosd.sock"), alias="socketPath")


class WebSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    host: str = "127.0.0.1"
    port: int = 8765
    static_dir: Path | None = Field(None, alias="staticDir")
    auth_db: Path | None = Field(None, alias="authDb")


class DrosSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    sys_root: Path = Field(Path("/"), alias="sysRoot")
    paths: DrosPaths = Field(default_factory=DrosPaths)
    daemon: DaemonSettings = Field(default_factory=DaemonSettings)
    web: WebSettings = Field(default_factory=WebSettings)


def load_settings(path: Path | str | None = None) -> DrosSettings:
    if path is None:
        return DrosSettings()

    settings_path = Path(path)
    with settings_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise ValueError(f"settings file must contain a mapping: {settings_path}")

    return DrosSettings.model_validate(data)
