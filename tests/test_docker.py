from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
import yaml
from rich.console import Console

from dros.settings import DrosPaths, DrosSettings
from dros.update import UpdateValidationError, run_update


def _console(output: StringIO) -> Console:
    return Console(file=output, force_terminal=False, color_system=None, width=120)


def _settings(tmp_path: Path) -> DrosSettings:
    return DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(
            configs=tmp_path / "configs",
            run=tmp_path / "run",
            containers=Path("/opt/gateway/containers"),
        ),
    )


def _compose(settings: DrosSettings, name: str) -> dict[str, object]:
    path = (
        settings.sys_root
        / (settings.paths.containers / name / "docker-compose.yml").relative_to("/")
    )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _commands(result: object) -> list[str]:
    return [" ".join(action.command or []) for action in result.actions if action.command]


def test_update_docker_container_writes_compose_and_uses_custom_network(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "docker.yaml").write_text(
        """
kind: Interface
metadata:
  name: br-app
spec:
  type: docker
  subnet: 172.30.0.0/24
---
kind: DockerContainer
metadata:
  name: web-demo
spec:
  image: nginx:stable-alpine
  network: br-app
  restart: unless-stopped
  environment:
    TZ: Asia/Shanghai
  capAdd:
    - NET_ADMIN
  mounts:
    - sourceType: inline
      name: default.conf
      target: /etc/nginx/conf.d/default.conf
      mode: ro
      source: |
        server {
          listen 80;
        }
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="docker-container/web-demo", console=_console(StringIO()))

    root = settings.sys_root / "opt/gateway/containers/web-demo"
    assert (root / "data").is_dir()
    assert (root / "config").is_dir()
    generated = root / "generated/inline/default.conf"
    assert "listen 80;" in generated.read_text(encoding="utf-8")
    compose = _compose(settings, "web-demo")
    service = compose["services"]["app"]
    assert service["image"] == "nginx:stable-alpine"
    assert service["container_name"] == "web-demo"
    assert service["environment"] == {"TZ": "Asia/Shanghai"}
    assert service["cap_add"] == ["NET_ADMIN"]
    assert service["networks"] == ["br-app"]
    assert compose["networks"] == {"br-app": {"external": True}}
    assert service["volumes"] == [
        "/opt/gateway/containers/web-demo/generated/inline/default.conf:"
        "/etc/nginx/conf.d/default.conf:ro"
    ]
    commands = _commands(result)
    assert any("docker compose --project-name dros-web-demo" in command for command in commands)
    assert any(command.endswith("up -d") for command in commands)
    assert any(command.endswith("restart") for command in commands)


def test_update_docker_rejects_missing_custom_network_interface(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "docker.yaml").write_text(
        """
kind: DockerContainer
metadata:
  name: web-demo
spec:
  image: nginx:stable-alpine
  network: br-app
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError, match="Interface/br-app"):
        run_update(settings, target="docker-container/web-demo", console=_console(StringIO()))


def test_update_docker_apps_render_builtin_compose_projects(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "apps.yaml").write_text(
        """
kind: DockerApp
metadata:
  name: vlmcsd
spec:
  app: vlmcsd
---
kind: DockerApp
metadata:
  name: nginx
spec:
  app: nginx
  variant: nginx
  network: host
  nginxConfFile:
    sourceType: inline
    name: nginx.conf
    source: |
      events {}
---
kind: DockerApp
metadata:
  name: ddns-go
spec:
  app: ddns-go
---
kind: DockerApp
metadata:
  name: unifi
spec:
  app: unifi
---
kind: DockerApp
metadata:
  name: certimate
spec:
  app: certimate
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="docker-apps", console=_console(StringIO()))

    assert _compose(settings, "vlmcsd")["services"]["app"]["image"] == "mikolatero/vlmcsd:latest"
    nginx_service = _compose(settings, "nginx")["services"]["app"]
    assert nginx_service["image"] == "nginx:1.30-alpine"
    assert nginx_service["network_mode"] == "host"
    assert nginx_service["volumes"] == [
        "/opt/gateway/containers/nginx/generated/inline/nginx.conf:/etc/nginx/nginx.conf:ro"
    ]
    ddns_service = _compose(settings, "ddns-go")["services"]["app"]
    assert ddns_service["network_mode"] == "bridge"
    assert "/opt/gateway/containers/ddns-go/data:/root:rw" in ddns_service["volumes"]
    unifi_volumes = _compose(settings, "unifi")["services"]["app"]["volumes"]
    assert "/opt/gateway/containers/unifi/data:/usr/lib/unifi/data:rw" in unifi_volumes
    certimate_service = _compose(settings, "certimate")["services"]["app"]
    assert certimate_service["image"] == "certimate/certimate:latest"
    assert "/opt/gateway/containers/certimate/data:/app/pb_data:rw" in certimate_service["volumes"]


def test_update_docker_app_rejects_collectd_templates(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "collectd.yaml").write_text(
        """
kind: DockerApp
metadata:
  name: collectd
spec:
  app: collectd
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError, match="collectd"):
        run_update(settings, target="docker-app/collectd", console=_console(StringIO()))
