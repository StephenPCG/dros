from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dros.ovpn import (
    create_client_profile,
    create_server_profile,
    init_instance,
    list_instances_summary,
    list_profile_certs,
    renew_client,
    renew_crl,
    resolve_instance,
    update_client,
    update_server,
)
from dros.settings import DrosPaths, DrosSettings, WebSettings
from dros.web.app import create_app
from dros.web.auth import WebAuthStore


def _settings(tmp_path: Path) -> DrosSettings:
    return DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(
            configs=tmp_path / "configs",
            logs=tmp_path / "logs",
            run=Path("/opt/gateway/run"),
            containers=Path("/opt/gateway/containers"),
        ),
        web=WebSettings(authDb=tmp_path / "web-auth.sqlite3"),
    )


def _client(settings: DrosSettings) -> TestClient:
    store = WebAuthStore(settings.web.auth_db)
    store.create_user("admin", "secret")
    client = TestClient(create_app(settings))
    assert client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret", "remember": False},
    ).status_code == 200
    return client


def test_ovpn_server_and_client_update_create_profiles_certs_and_configs(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    init_instance(settings, "office", ca_cn="office CA")
    create_server_profile(
        settings,
        "beijing",
        instance="office",
        endpoint="beijing.vpn.example.net",
        network="10.60.253.0",
    )
    create_server_profile(settings, "shanghai", instance="office", endpoint="shanghai.vpn.example.net")
    create_client_profile(settings, "alice", instance="office")

    server_outputs = update_server(settings, "beijing", instance="office")
    client_outputs = update_client(settings, "alice", instance="office")

    server_conf = server_outputs[0].read_text(encoding="utf-8")
    output_names = {path.name for path in client_outputs}
    auto = next(path for path in client_outputs if path.name == "client-auto.ovpn").read_text(
        encoding="utf-8"
    )
    summaries = list_instances_summary(settings)

    assert "server 10.60.253.0 255.255.255.0" in server_conf
    assert "\nproto udp6\n" in server_conf
    assert "\ndev-type tun\n" in server_conf
    assert "<ca>" in server_conf
    assert "<cert>" in server_conf
    assert "<key>" in server_conf
    assert "dh none" in server_conf
    assert output_names == {"client-auto.ovpn", "client-beijing.ovpn", "client-shanghai.ovpn"}
    assert "dev tun" in auto
    assert "<connection>\nremote beijing.vpn.example.net 1194 udp\n</connection>" in auto
    assert "<connection>\nremote shanghai.vpn.example.net 1194 udp\n</connection>" in auto
    assert summaries[0].name == "office"
    assert summaries[0].server_profiles == 2
    assert summaries[0].client_profiles == 1
    assert summaries[0].server_certs == 1
    assert summaries[0].client_certs == 1


def test_ovpn_requires_explicit_instance_when_multiple_exist(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    init_instance(settings, "one")
    init_instance(settings, "two")

    with pytest.raises(ValueError, match="multiple ovpn instances found"):
        resolve_instance(settings, None)


def test_ovpn_client_cert_disabled_omits_client_cert_and_rejects_renew(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    instance = init_instance(settings, "office", ca_cn="office CA")
    instance.config_path.write_text(
        """
auth:
  client_cert: none
  user_auth:
    type: ldap
    plugin: /usr/lib/openvpn/openvpn-auth-ldap.so
    config: /opt/gateway/openvpn/ldap-auth
""".lstrip(),
        encoding="utf-8",
    )
    create_server_profile(settings, "beijing", instance="office", endpoint="beijing.vpn.example.net")
    create_client_profile(settings, "alice", instance="office")

    update_server(settings, "beijing", instance="office")
    outputs = update_client(settings, "alice", instance="office")
    auto = next(path for path in outputs if path.name == "client-auto.ovpn").read_text(
        encoding="utf-8"
    )

    assert "auth-user-pass" in auto
    assert "<ca>" in auto
    assert "<cert>" not in auto
    assert "<key>" not in auto
    with pytest.raises(ValueError, match="client certificates are disabled"):
        renew_client(settings, "alice", instance="office")


def test_ovpn_lists_and_revokes_profile_certs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    init_instance(settings, "office")
    create_server_profile(settings, "beijing", instance="office", endpoint="vpn.example.net")
    update_server(settings, "beijing", instance="office")
    second = update_server(settings, "beijing", instance="office")
    assert second

    certs = list_profile_certs(settings, "office", "server", "beijing")

    assert len(certs) == 1
    assert certs[0].latest is True
    assert certs[0].cert_id


def test_ovpn_crl_days_is_configurable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    instance = init_instance(settings, "office", ca_cn="office CA")
    instance.config_path.write_text("crl:\n  days: 3650\n", encoding="utf-8")

    crl_path = renew_crl(settings, instance="office")
    openssl_config = instance.root / "pki" / "openssl.cnf"

    assert crl_path.exists()
    assert "default_crl_days = 3650" in openssl_config.read_text(encoding="utf-8")


def test_ovpn_web_api_lists_instances_and_profiles(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    init_instance(settings, "office")
    create_server_profile(settings, "beijing", instance="office", endpoint="vpn.example.net")
    create_client_profile(settings, "alice", instance="office")
    update_server(settings, "beijing", instance="office")
    update_client(settings, "alice", instance="office")
    client = _client(settings)

    instances = client.get("/api/openvpn/instances")
    profiles = client.get("/api/openvpn/instances/office/profiles")

    assert instances.status_code == 200
    assert instances.json()["instances"][0]["name"] == "office"
    assert instances.json()["instances"][0]["serverProfiles"] == 1
    assert profiles.status_code == 200
    assert {item["kind"] for item in profiles.json()["profiles"]} == {"server", "client"}


def test_ovpn_web_api_creates_renews_revokes_and_downloads_profile(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    init_instance(settings, "office")
    client = _client(settings)

    create_server = client.post(
        "/api/openvpn/instances/office/profiles",
        json={"kind": "server", "name": "beijing"},
    )
    create_client = client.post(
        "/api/openvpn/instances/office/profiles",
        json={"kind": "client", "name": "alice"},
    )
    renew = client.post("/api/openvpn/instances/office/profiles/client/alice/renew")
    certs = client.get("/api/openvpn/instances/office/profiles/client/alice/certs")
    download = client.get("/api/openvpn/instances/office/profiles/client/alice/download")
    revoke = client.post(
        "/api/openvpn/instances/office/profiles/client/alice/certs/latest/revoke"
    )

    assert create_server.status_code == 200
    assert create_client.status_code == 200
    assert renew.status_code == 200
    assert certs.status_code == 200
    assert certs.json()["certs"][0]["latest"] is True
    assert download.status_code == 200
    assert "client" in download.text
    assert "remote beijing.vpn.example.net 1194 udp" in download.text
    assert revoke.status_code == 200
    assert revoke.json()["ok"] is True
