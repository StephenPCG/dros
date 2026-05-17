from __future__ import annotations

from io import StringIO
from pathlib import Path

import yaml
from rich.console import Console

from dros.settings import DrosPaths, DrosSettings
from dros.update import run_update


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


def test_update_cronjob_writes_cron_d_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "cron.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: CronJob
metadata:
  name: refresh-routes
spec:
  schedule: "*/10 * * * *"
  user: root
  command: /usr/local/bin/gw hook route-refresh --verbose 0
  environment:
    PATH: /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  comment: Refresh policy routes from daemon queue.
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="cronjob/refresh-routes", console=_console(StringIO()))

    content = (settings.sys_root / "etc/cron.d/dros-refresh-routes").read_text(
        encoding="utf-8"
    )
    assert "# Resource: CronJob/refresh-routes\n" in content
    assert "# Refresh policy routes from daemon queue.\n" in content
    assert "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n" in content
    assert "*/10 * * * * root /usr/local/bin/gw hook route-refresh --verbose 0\n" in content


def test_docker_mount_can_reference_configmap_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "docker.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: ConfigMap
metadata:
  name: nginx-config
spec:
  files:
    default.conf: |
      server {
        listen 80;
      }
---
apiVersion: dros/v1alpha1
kind: DockerContainer
metadata:
  name: web-demo
spec:
  image: nginx:stable-alpine
  mounts:
    - sourceType: configMap
      source: nginx-config
      key: default.conf
      target: /etc/nginx/conf.d/default.conf
      mode: ro
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="docker-container/web-demo", console=_console(StringIO()))

    root = settings.sys_root / "opt/gateway/containers/web-demo"
    generated = root / "generated/configmap/nginx-config/default.conf"
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "listen 80;" in generated.read_text(encoding="utf-8")
    assert compose["services"]["app"]["volumes"] == [
        "/opt/gateway/containers/web-demo/generated/configmap/nginx-config/default.conf:"
        "/etc/nginx/conf.d/default.conf:ro"
    ]
