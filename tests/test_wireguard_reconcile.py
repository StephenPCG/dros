import subprocess
import io
from types import SimpleNamespace
import json
import os
import sys

import pytest
import yaml

from rich.console import Console

from dros.executor import SystemExecutor
from dros.config_objects import InterfaceConfig
from dros.plugins import network_interfaces
from dros.settings import DrosPaths, DrosSettings
from dros.update import run_update


def fixture(tmp_path):
    settings = DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(configs=tmp_path / "configs", run=tmp_path / "run"),
    )
    settings.paths.configs.mkdir()
    spec = {
        "type": "wireguard",
        "privateKeyFile": "/etc/keys/wg0.key",
        "address": "192.0.2.1/24",
        "peers": [
            {"publicKey": "retained-peer", "allowedIPs": ["192.0.2.2/32"]},
            {
                "publicKey": "retired-peer",
                "endpoint": "198.51.100.2:12345",
                "allowedIPs": ["192.0.2.3/32"],
            },
        ],
    }
    path = settings.paths.configs / "wg.yaml"

    def write():
        path.write_text(
            yaml.safe_dump({"kind": "Interface", "metadata": {"name": "wg0"}, "spec": spec})
        )

    write()
    run_update(settings, target="iface/wg0", verbose=0)
    return settings, spec, write


def test_removed_peer_uses_complete_remaining_config_without_interface_restart(tmp_path):
    settings, spec, write = fixture(tmp_path)
    spec["peers"].pop()
    write()
    result = run_update(settings, target="iface/wg0", verbose=0)
    commands = [action.command for action in result.actions if action.command]
    assert any("dros-wireguard-reconcile" in command for command in commands)
    assert not any(command[0] == "ifdown" for command in commands)
    content = (settings.sys_root / "etc/wireguard/wg0.conf").read_text()
    assert "PublicKey = retained-peer" in content
    assert "retired-peer" not in content
    # Omitted endpoint/key fields stay omitted, allowing the kernel to retain
    # the current endpoint learned by roaming/wgsd and a key loaded from a file.
    assert "Endpoint =" not in content
    assert "PrivateKey =" not in content


def test_unchanged_file_reconciles_historical_addconf_drift(tmp_path):
    settings, _spec, _write = fixture(tmp_path)
    result = run_update(settings, target="iface/wg0", verbose=0)
    assert not any(action.kind == "write_file" for action in result.actions)
    assert any("dros-wireguard-reconcile" in (action.command or []) for action in result.actions)


def test_failure_after_file_write_is_propagated_and_unchanged_retry_reconciles(
    tmp_path, monkeypatch
):
    settings, spec, write = fixture(tmp_path)
    spec["peers"].pop()
    write()
    calls = []

    def fail_once(context, name, config):
        calls.append(name)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, ["wg", "addconf", name])

    monkeypatch.setattr(network_interfaces, "_wireguard_reconcile", fail_once)
    with pytest.raises(subprocess.CalledProcessError):
        run_update(settings, target="iface/wg0", verbose=0)
    assert "retired-peer" not in (settings.sys_root / "etc/wireguard/wg0.conf").read_text()
    result = run_update(settings, target="iface/wg0", verbose=0)
    assert calls == ["wg0", "wg0"]
    assert not any(action.kind == "write_file" for action in result.actions)


def test_empty_peers_config_still_reconciles_and_does_not_touch_other_interfaces(tmp_path):
    settings, spec, write = fixture(tmp_path)
    other = {"kind": "Interface", "metadata": {"name": "wg-other"}, "spec": dict(spec)}
    (settings.paths.configs / "other.yaml").write_text(yaml.safe_dump(other))
    spec["peers"] = []
    write()
    result = run_update(settings, target="iface/wg0", verbose=0)
    content = (settings.sys_root / "etc/wireguard/wg0.conf").read_text()
    assert "[Peer]" not in content
    assert any("dros-wireguard-reconcile" in (action.command or []) for action in result.actions)
    assert not any("wg-other" in " ".join(action.command or []) for action in result.actions)
    assert not (settings.sys_root / "etc/wireguard/wg-other.conf").exists()


def fake_cli(tmp_path, peers):
    """Run the actual argv/shell pipeline against isolated ip/wg executables.

    This is a process-boundary/error-propagation test, not a kernel test.
    The fake implements addconf's preservation of omitted fields, and rejects
    setconf/syncconf so that a destructive substitution cannot pass silently.
    """
    bins = tmp_path / "bin"
    bins.mkdir()
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "private_key": "loaded-from-private-key-file",
                "public_key": "local-peer",
                "listen_port": 42424,
                "fwmark": 123,
                "peers": peers,
                "commands": [],
            }
        )
    )
    (bins / "ip").write_text("#!/bin/sh\nexit 0\n")
    (bins / "ip").chmod(0o755)
    (bins / "wg").write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, sys
from pathlib import Path
path = Path(os.environ["TEST_WG_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
state["commands"].append(args)
failure = os.environ.get("TEST_WG_FAIL")
if failure == args[0] or (args[0] == "show" and failure == args[2]):
    path.write_text(json.dumps(state))
    sys.exit(7)
if args[0] == "addconf":
    peer = None
    for line in Path(args[2]).read_text().splitlines():
        if line.startswith("PublicKey = "):
            peer = line.split(" = ", 1)[1]
            if peer == state["public_key"] or peer == os.environ.get("TEST_WG_OMIT_PEER"):
                peer = None
            else:
                state["peers"].setdefault(peer, {})
        elif " = " in line:
            key, value = line.split(" = ", 1)
            if peer:
                state["peers"][peer][key] = value
            elif key == "PrivateKey":
                state["private_key"] = value
                # Synthetic fixture key mapping; the real kernel derives it.
                state["public_key"] = "public-of-" + value
                state["peers"].pop(state["public_key"], None)
            elif key == "ListenPort":
                state["listen_port"] = int(value)
elif args[0] == "show" and args[2] == "public-key":
    print(state["public_key"])
elif args[0] == "show" and args[2] == "peers":
    print("\n".join(state["peers"]))
elif args[0] == "set" and args[2] == "peer" and args[4] == "remove":
    if not os.environ.get("TEST_WG_KEEP_STALE"):
        del state["peers"][args[3]]
else:
    sys.exit(9)
path.write_text(json.dumps(state))
"""
    )
    (bins / "wg").chmod(0o755)
    env = {**os.environ, "PATH": f"{bins}:/usr/bin:/bin", "TEST_WG_STATE": str(state)}
    return state, env


def test_actual_reconcile_pipeline_removes_only_stale_peers_preserving_runtime_fields(tmp_path):
    retained = {
        "Endpoint": "203.0.113.10:61234",
        "PresharedKey": "pre-existing-psk",
        "PersistentKeepalive": "25",
        "AllowedIPs": "192.0.2.2/32",
    }
    state, env = fake_cli(tmp_path, {"retained-peer": retained, "retired-peer": {}})
    conf = tmp_path / "wg.conf"
    conf.write_text("[Interface]\n[Peer]\nPublicKey = retained-peer\nAllowedIPs = 192.0.2.2/32\n")
    command = network_interfaces._wireguard_reconcile_command("wg0", str(conf), ["retained-peer"])
    subprocess.run(command, env=env, check=True, capture_output=True, text=True)
    result = json.loads(state.read_text())
    assert result["peers"] == {"retained-peer": retained}
    assert result["private_key"] == "loaded-from-private-key-file"
    assert result["listen_port"] == 42424 and result["fwmark"] == 123
    assert ["set", "wg0", "peer", "retired-peer", "remove"] in result["commands"]
    assert not any(cmd[0] == "set" and cmd[3] == "retained-peer" for cmd in result["commands"])


@pytest.mark.parametrize(
    ("failed_command", "stage_exit"),
    [("addconf", 81), ("public-key", 82), ("peers", 83), ("set", 84)],
)
def test_pipeline_propagates_command_errors_and_unchanged_retry_removes_old_peer(
    tmp_path, failed_command, stage_exit
):
    state, env = fake_cli(tmp_path, {"retained-peer": {}, "retired-peer": {}})
    conf = tmp_path / "wg.conf"
    conf.write_text("[Interface]\n[Peer]\nPublicKey = retained-peer\n")
    command = network_interfaces._wireguard_reconcile_command("wg0", str(conf), ["retained-peer"])
    failed = subprocess.run(
        command, env={**env, "TEST_WG_FAIL": failed_command}, capture_output=True
    )
    assert failed.returncode == stage_exit
    assert "retired-peer" in json.loads(state.read_text())["peers"]
    subprocess.run(command, env=env, check=True, capture_output=True)
    assert list(json.loads(state.read_text())["peers"]) == ["retained-peer"]


def test_empty_target_peer_set_is_removed_by_pipeline(tmp_path):
    state, env = fake_cli(tmp_path, {"retired-peer": {}})
    conf = tmp_path / "wg.conf"
    conf.write_text("[Interface]\n")
    command = network_interfaces._wireguard_reconcile_command("wg0", str(conf), [])
    subprocess.run(command, env=env, check=True, capture_output=True)
    assert json.loads(state.read_text())["peers"] == {}


@pytest.mark.parametrize("peers", [["local-peer", "retained-peer"], ["local-peer"]])
def test_shared_mesh_list_can_include_this_interfaces_public_key(tmp_path, peers):
    state, env = fake_cli(tmp_path, {"retired-peer": {}})
    conf = tmp_path / "wg.conf"
    conf.write_text("[Interface]\n" + "".join(f"[Peer]\nPublicKey = {p}\n" for p in peers))
    command = network_interfaces._wireguard_reconcile_command("wg0", str(conf), peers)
    subprocess.run(command, env=env, check=True, capture_output=True)
    result = json.loads(state.read_text())
    assert set(result["peers"]) == set(peers) - {"local-peer"}
    # privateKeyFile is deliberately absent from the generated wg config.
    assert result["private_key"] == "loaded-from-private-key-file"
    assert result["public_key"] == "local-peer"
    assert result["listen_port"] == 42424 and result["fwmark"] == 123


def test_self_peer_is_identified_after_inline_private_key_rotation(tmp_path):
    state, env = fake_cli(tmp_path, {"public-of-new-key": {}, "retired-peer": {}})
    conf = tmp_path / "wg.conf"
    peers = ["local-peer", "public-of-new-key", "retained-peer"]
    conf.write_text("[Interface]\nPrivateKey = new-key\n" + "".join(
        f"[Peer]\nPublicKey = {p}\n" for p in peers
    ))
    subprocess.run(
        network_interfaces._wireguard_reconcile_command("wg0", str(conf), peers),
        env=env, check=True, capture_output=True,
    )
    result = json.loads(state.read_text())
    assert result["public_key"] == "public-of-new-key"
    assert set(result["peers"]) == {"local-peer", "retained-peer"}


def test_missing_foreign_peer_is_still_a_failure_with_self_in_mesh_list(tmp_path):
    _state, env = fake_cli(tmp_path, {})
    conf = tmp_path / "wg.conf"
    peers = ["local-peer", "required-foreign-peer"]
    conf.write_text("[Interface]\n" + "".join(f"[Peer]\nPublicKey = {p}\n" for p in peers))
    result = subprocess.run(
        network_interfaces._wireguard_reconcile_command("wg0", str(conf), peers),
        env={**env, "TEST_WG_OMIT_PEER": "required-foreign-peer"}, capture_output=True,
    )
    assert result.returncode == 86


def test_failed_peer_removal_verification_has_a_specific_stage(tmp_path):
    _state, env = fake_cli(tmp_path, {"retired-peer": {}})
    conf = tmp_path / "wg.conf"
    conf.write_text("[Interface]\n")
    result = subprocess.run(
        network_interfaces._wireguard_reconcile_command("wg0", str(conf), []),
        env={**env, "TEST_WG_KEEP_STALE": "1"}, capture_output=True,
    )
    assert result.returncode == 85


@pytest.mark.parametrize("verbose", [0, 1, 2])
@pytest.mark.parametrize(("stage_exit", "diagnostic"), [
    (81, "applying configuration"),
    (82, "reading the interface public key"),
    (83, "reading runtime peers"),
    (84, "removing a stale peer"),
    (85, "verifying that stale peers were removed"),
    (86, "a non-local peer is missing"),
    (87, "bringing up the missing interface"),
    (1, "running the reconciliation command"),
])
def test_reconcile_reports_stage_without_raw_command_or_key_output(
    tmp_path, verbose, stage_exit, diagnostic
):
    output = io.StringIO()
    secret = "SYNTHETIC-PRIVATE-KEY-MUST-NOT-APPEAR"

    def runner(command, **kwargs):
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        return subprocess.run(
            ["sh", "-c", 'printf "%s\\n" "$1" >&2; exit "$2"', "test", secret, str(stage_exit)],
            **kwargs,
        )

    executor = SystemExecutor(
        DrosSettings(sysRoot="/"), verbose=verbose,
        console=Console(file=output, force_terminal=False), runner=runner,
    )
    context = SimpleNamespace(executor=executor)
    with pytest.raises(RuntimeError) as failure:
        network_interfaces._wireguard_reconcile(
            context, "wg0", InterfaceConfig(type="wireguard", privateKeyFile="/tmp/key", peers=[])
        )
    text = str(failure.value) + output.getvalue()
    assert "Interface/wg0" in text and diagnostic in text
    assert secret not in text
    assert "set -eu" not in text
    assert "dros-wireguard-reconcile" not in text
