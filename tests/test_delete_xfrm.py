from __future__ import annotations

import json
import subprocess
from io import StringIO
from pathlib import Path

import pytest
import yaml
from rich.console import Console

from dros import delete
from dros.plugins.network_xfrm import _render_xfrm_service
from dros.update import run_update
from test_delete import no_commands, settings_for, tree, write_object


def transport_spec():
    return {
        "localParty": "partyA",
        "activation": "manual",
        "partyA": {"publicIp": "192.0.2.2"},
        "partyB": {"publicIp": "198.51.100.1"},
        "spi": {"partyAToPartyB": 101, "partyBToPartyA": 102},
        "reqid": {"partyAToPartyB": 201, "partyBToPartyA": 202},
        "keys": {"partyAToPartyB": "secret-out", "partyBToPartyA": "secret-in"},
    }


def setup_pair(tmp_path, *, unit=False):
    settings = settings_for(tmp_path)
    source = write_object(
        settings,
        spec={
            "type": "gre",
            "localVip": "192.0.2.1",
            "localPublicIp": "192.0.2.2",
            "remotePublicIp": "198.51.100.1",
            "xfrmTransport": "old-vpn",
        },
    )
    transport_source = write_object(settings, "XfrmTransport", "old-vpn", transport_spec())
    run_update(settings, target="iface/gre-old", verbose=0)
    if unit:
        path = settings.sys_root / "etc/systemd/system/dros-xfrm-old-vpn.service"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_xfrm_service("old-vpn"))
    return settings, source, transport_source


def test_exclusive_preview_has_scoped_commands_and_retains_both_sources(tmp_path):
    settings, source, transport_source = setup_pair(tmp_path, unit=True)
    before = tree(tmp_path)
    plan = delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    output = StringIO()
    delete.print_delete_plan(plan, Console(file=output, width=200))
    assert tree(tmp_path) == before
    text = output.getvalue()
    assert "exclusive XfrmTransport/old-vpn" in text
    assert str(source) in text and str(transport_source) in text
    assert "ip xfrm policy delete dir out src 192.0.2.2/32 dst 198.51.100.1/32 proto gre" in text
    assert "ip xfrm state delete src 198.51.100.1 dst 192.0.2.2 proto esp spi 0x00000066" in text
    assert "systemctl disable --now dros-xfrm-old-vpn.service" in text
    assert "nokeys" in text and "flush" not in text and "secret-" not in text
    delete.apply_delete_plan(settings, plan, runner=no_commands)
    assert source.read_bytes() == before[str(source.relative_to(tmp_path))]
    assert transport_source.read_bytes() == before[str(transport_source.relative_to(tmp_path))]
    assert not list((settings.sys_root / "etc/network/interfaces.d").glob("*gre-old*"))
    assert not (settings.sys_root / "etc/systemd/system/dros-xfrm-old-vpn.service").exists()
    assert (
        not delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
        .resources[0]
        .files
    )


@pytest.mark.parametrize("disabled,batch", [(False, False), (True, False), (False, True)])
def test_shared_transport_rejects_even_disabled_or_in_same_batch(tmp_path, disabled, batch):
    settings, source, _ = setup_pair(tmp_path)
    document = yaml.safe_load(source.read_text())
    document["metadata"] = {"name": "gre-another", "disabled": disabled}
    other = settings.paths.configs / "another.yaml"
    other.write_text(yaml.safe_dump(document))
    before = tree(tmp_path)
    with pytest.raises(ValueError, match="shared.*Interface/gre-"):
        delete.build_delete_plan(
            settings, "iface" if batch else "iface/gre-old", runner=no_commands
        )
    assert tree(tmp_path) == before


def test_other_resource_reference_and_disabled_conflict_are_rejected(tmp_path):
    settings, _, _ = setup_pair(tmp_path)
    cron = write_object(settings, "CronJob", "restart", {"command": "gw start xfrm/old-vpn"})
    with pytest.raises(ValueError, match="CronJob/restart"):
        delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    cron.unlink()
    spec = transport_spec()
    spec["spi"] = {"partyAToPartyB": 301, "partyBToPartyA": 302}
    conflict = write_object(settings, "XfrmTransport", "disabled-vpn", spec)
    data = yaml.safe_load(conflict.read_text())
    data["metadata"]["disabled"] = True
    conflict.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="identity overlaps XfrmTransport/disabled-vpn"):
        delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)


def test_state_identity_conflict_ignores_source_like_kernel(tmp_path):
    settings, _, _ = setup_pair(tmp_path)
    spec = transport_spec()
    spec["partyA"]["publicIp"] = "203.0.113.25"
    write_object(settings, "XfrmTransport", "different-src", spec)
    with pytest.raises(ValueError, match="identity overlaps"):
        delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)


@pytest.mark.parametrize(
    "change,match",
    [
        ({"activation": "system"}, "manual GRE"),
        ({"selector": {"proto": "udp"}}, "manual GRE"),
        ({"partyA": {"publicIp": "203.0.113.3"}}, "endpoints do not match"),
    ],
)
def test_unsupported_or_mismatched_transport_fails_before_effects(tmp_path, change, match):
    settings, _, source = setup_pair(tmp_path)
    data = yaml.safe_load(source.read_text())
    data["spec"].update(change)
    source.write_text(yaml.safe_dump(data))
    before = tree(tmp_path)
    with pytest.raises(ValueError, match=match):
        delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    assert tree(tmp_path) == before


def policy(direction):
    return {
        "src": direction.src + "/32",
        "dst": direction.dst + "/32",
        "proto": "gre",
        "dir": direction.direction,
        "tmpl": [
            {
                "src": direction.src,
                "dst": direction.dst,
                "proto": "esp",
                "mode": "transport",
                "reqid": direction.reqid,
            }
        ],
    }


def state(direction):
    return {
        "src": direction.src,
        "dst": direction.dst,
        "proto": "esp",
        "spi": hex(direction.spi),
        "mode": "transport",
        "reqid": direction.reqid,
        "sel": {"src": direction.src + "/32", "dst": direction.dst + "/32", "proto": "gre"},
    }


class Kernel:
    def __init__(self, removal, *, unit=False):
        self.link = True
        self.policies = [policy(d) for d in removal.directions]
        self.states = [state(d) for d in removal.directions]
        self.unit = unit
        self.stopped = True
        self.commands = []
        self.fail = None
        self.keep = None

    def __call__(self, cmd, **kwargs):
        self.commands.append(cmd)
        data = ""
        status = 0
        if cmd == self.fail:
            return subprocess.CompletedProcess(cmd, 1, "", "must never print secret-key")
        if cmd[:4] == ["ip", "-json", "-details", "link"]:
            data = json.dumps(
                [
                    {
                        "ifname": "gre-old",
                        "ifindex": 3,
                        "linkinfo": {
                            "info_kind": "gre",
                            "info_data": {"local": "192.0.2.2", "remote": "198.51.100.1"},
                        },
                    }
                ]
                if self.link
                else []
            )
        elif cmd[:2] == ["ifdown", "--force"]:
            self.link = False
        elif cmd[:4] == ["ip", "-oneline", "xfrm", "policy"]:
            data = json.dumps(self.policies)
        elif cmd[:4] == ["ip", "-oneline", "xfrm", "state"]:
            assert cmd[-1] == "nokeys"
            data = json.dumps(self.states)
        elif cmd[:4] == ["ip", "xfrm", "policy", "delete"]:
            if cmd != self.keep:
                self.policies = [p for p in self.policies if p["dir"] != cmd[5]]
        elif cmd[:4] == ["ip", "xfrm", "state", "delete"]:
            if cmd != self.keep:
                self.states = [s for s in self.states if int(s["spi"], 0) != int(cmd[-1], 0)]
        elif cmd[:2] == ["systemctl", "show"]:
            if self.unit:
                data = (
                    "LoadState=loaded\nFragmentPath=/etc/systemd/system/dros-xfrm-old-vpn.service\n"
                    "DropInPaths=\nActiveState="
                    + ("inactive" if self.stopped else "active")
                    + "\nUnitFileState="
                    + ("disabled" if self.stopped else "enabled")
                    + "\n"
                )
            else:
                data = "LoadState=not-found\nFragmentPath=\nDropInPaths=\nActiveState=inactive\nUnitFileState=\n"
        elif cmd[:3] == ["systemctl", "disable", "--now"]:
            self.stopped = True
        elif cmd == ["systemctl", "daemon-reload"]:
            self.unit = False
        else:
            raise AssertionError(f"unexpected command {cmd}")
        return subprocess.CompletedProcess(cmd, status, data, "")


def live_plan(settings, monkeypatch, kernel):
    original = delete._target_path
    live = settings.model_copy(update={"sys_root": Path("/")})
    monkeypatch.setattr(delete, "_target_path", lambda _, path: original(settings, path))
    return live, delete.build_delete_plan(live, "iface/gre-old", runner=kernel)


@pytest.mark.parametrize("unit", [False, True])
def test_live_delete_orders_shutdown_exact_entries_and_file_cleanup(tmp_path, monkeypatch, unit):
    settings, source, transport_source = setup_pair(tmp_path, unit=unit)
    offline = delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    kernel = Kernel(offline.resources[0].xfrm, unit=unit)
    live, plan = live_plan(settings, monkeypatch, kernel)
    before = (source.read_bytes(), transport_source.read_bytes())
    delete.apply_delete_plan(live, plan, runner=kernel)
    assert not kernel.link and not kernel.policies and not kernel.states
    assert (source.read_bytes(), transport_source.read_bytes()) == before
    commands = kernel.commands
    assert commands.index(["ifdown", "--force", "gre-old"]) < next(
        i for i, cmd in enumerate(commands) if cmd[:4] == ["ip", "xfrm", "policy", "delete"]
    )
    assert all("flush" not in cmd for cmd in commands)
    again = delete.build_delete_plan(live, "iface/gre-old", runner=kernel)
    delete.apply_delete_plan(live, again, runner=kernel)


@pytest.mark.parametrize("failure", ["delete", "verify"])
def test_live_xfrm_failure_retains_generated_and_source_files(tmp_path, monkeypatch, failure):
    settings, _, _ = setup_pair(tmp_path)
    offline = delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    kernel = Kernel(offline.resources[0].xfrm)
    live, plan = live_plan(settings, monkeypatch, kernel)
    command = plan.resources[0].xfrm.directions[0].policy_command
    if failure == "delete":
        kernel.fail = command
    else:
        kernel.keep = command
    before = tree(tmp_path)
    with pytest.raises(RuntimeError, match="source YAML is retained") as error:
        delete.apply_delete_plan(live, plan, runner=kernel)
    assert "secret-key" not in str(error.value)
    assert tree(tmp_path) == before
    assert not kernel.link


@pytest.mark.parametrize(
    "kind,change",
    [
        ("state", {"src": "203.0.113.1"}),
        ("state", {"reqid": 999}),
        ("state", {"mark": {"value": 7}}),
        ("state", {"mode": "tunnel"}),
        ("policy", {"tmpl": []}),
        ("policy", {"mark": {"value": 7}}),
    ],
)
def test_ambiguous_or_foreign_live_state_aborts_before_ifdown(tmp_path, monkeypatch, kind, change):
    settings, _, _ = setup_pair(tmp_path)
    plan = delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    kernel = Kernel(plan.resources[0].xfrm)
    getattr(kernel, "states" if kind == "state" else "policies")[0].update(change)
    before = tree(tmp_path)
    with pytest.raises(ValueError, match="ownership"):
        live_plan(settings, monkeypatch, kernel)
    assert kernel.link and tree(tmp_path) == before
    assert ["ifdown", "--force", "gre-old"] not in kernel.commands


def test_inventory_failure_and_unowned_service_block_preview(tmp_path, monkeypatch):
    settings, _, _ = setup_pair(tmp_path)
    plan = delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    kernel = Kernel(plan.resources[0].xfrm)
    kernel.fail = ["ip", "-oneline", "xfrm", "state", "list", "nokeys"]
    with pytest.raises(RuntimeError, match="cannot inspect XFRM state"):
        live_plan(settings, monkeypatch, kernel)
    assert kernel.link
    kernel.fail = None
    kernel.unit = True
    with pytest.raises(ValueError, match="unexpected unit location"):
        live_plan(settings, monkeypatch, kernel)
    assert kernel.link


def test_source_and_installed_hooks_must_match_before_detaching(tmp_path):
    settings, source, _ = setup_pair(tmp_path)
    document = yaml.safe_load(source.read_text())
    document["spec"].pop("xfrmTransport")
    source.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError, match="installed configuration differs"):
        delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)


def test_implicit_same_endpoint_consumer_is_shared(tmp_path):
    settings, source, _ = setup_pair(tmp_path)
    data = yaml.safe_load(source.read_text())
    data["spec"].pop("xfrmTransport")
    write_object(settings, name="gre-implicit", spec=data["spec"])
    with pytest.raises(ValueError, match="same GRE endpoints"):
        delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)


@pytest.mark.parametrize("target_present", [True, False])
def test_unconfigured_runtime_gre_consumer_blocks_even_absent_target(
    tmp_path, monkeypatch, target_present
):
    settings, _, _ = setup_pair(tmp_path)
    plan = delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    kernel = Kernel(plan.resources[0].xfrm)
    kernel.link = target_present

    def runner(cmd, **kwargs):
        result = kernel(cmd, **kwargs)
        if cmd[:4] == ["ip", "-json", "-details", "link"]:
            links = json.loads(result.stdout)
            links.append(
                {
                    "ifname": "unmanaged",
                    "ifindex": 22,
                    "linkinfo": {
                        "info_kind": "gretap",
                        "info_data": {
                            "local": "192.0.2.2",
                            "remote": "198.51.100.1",
                        },
                    },
                }
            )
            return subprocess.CompletedProcess(cmd, 0, json.dumps(links), "")
        return result

    with pytest.raises(ValueError, match="shared: runtime Interface/unmanaged"):
        live_plan(settings, monkeypatch, runner)
    assert ["ifdown", "--force", "gre-old"] not in kernel.commands


def test_entire_xfrm_batch_is_preflighted_before_any_file_removal(tmp_path):
    settings, _, _ = setup_pair(tmp_path)
    write_object(settings, name="zzz-physical", spec={"type": "eth"})
    before = tree(tmp_path)
    with pytest.raises(ValueError, match="does not support type eth"):
        delete.build_delete_plan(settings, "iface", runner=no_commands)
    assert tree(tmp_path) == before


def test_unmanaged_systemd_file_is_never_removed(tmp_path):
    settings, _, _ = setup_pair(tmp_path, unit=True)
    file = settings.sys_root / "etc/systemd/system/dros-xfrm-old-vpn.service"
    file.write_text(
        file.read_text().replace("ExecStart=", "ExecStartPre=/usr/bin/true\nExecStart=")
    )
    before = tree(tmp_path)
    with pytest.raises(ValueError, match="installed configuration differs"):
        delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    assert tree(tmp_path) == before


def test_wireguard_xfrm_delete_is_explicitly_out_of_scope(tmp_path):
    settings = settings_for(tmp_path)
    write_object(settings, name="wg-old", spec={"type": "wireguard", "xfrmTransport": "old-vpn"})
    write_object(settings, "XfrmTransport", "old-vpn", transport_spec())
    with pytest.raises(ValueError, match="GRE only"):
        delete.build_delete_plan(settings, "iface/wg-old", runner=no_commands)


def test_systemd_stop_failure_retains_files_and_aborts_xfrm_cleanup(tmp_path, monkeypatch):
    settings, _, _ = setup_pair(tmp_path, unit=True)
    offline = delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    kernel = Kernel(offline.resources[0].xfrm, unit=True)
    live, plan = live_plan(settings, monkeypatch, kernel)
    kernel.fail = ["systemctl", "disable", "--now", "dros-xfrm-old-vpn.service"]
    before = tree(tmp_path)
    with pytest.raises(RuntimeError, match="source YAML is retained"):
        delete.apply_delete_plan(live, plan, runner=kernel)
    assert tree(tmp_path) == before
    assert kernel.states and kernel.policies


@pytest.mark.parametrize("disabled_first", [False, True])
def test_disabled_overlay_cannot_hide_effective_runtime_dependency(tmp_path, disabled_first):
    from dros.config_objects import ConfigObjectLoadError, load_config_objects

    settings, _, _ = setup_pair(tmp_path)
    overrides = tmp_path / "overrides"
    overrides.mkdir()
    settings.paths.configs = [settings.paths.configs, overrides]
    document = {
        "kind": "Interface",
        "metadata": {"name": "consumer", "disabled": disabled_first},
        "spec": {"type": "gre", "xfrmTransport": "old-vpn"},
    }
    (settings.paths.configs[0] / "consumer.yaml").write_text(yaml.safe_dump(document))
    document["metadata"]["disabled"] = not disabled_first
    document["spec"].pop("xfrmTransport")
    (overrides / "consumer.yaml").write_text(yaml.safe_dump(document))
    assert (
        load_config_objects(settings).require("Interface", "consumer").metadata["disabled"] is False
    )
    with pytest.raises(ConfigObjectLoadError, match="disabled overlay"):
        load_config_objects(settings, include_disabled=True)
    with pytest.raises(ConfigObjectLoadError, match="disabled overlay"):
        delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)


def test_active_historical_unit_fails_before_gre_is_stopped(tmp_path, monkeypatch):
    settings, _, _ = setup_pair(tmp_path, unit=True)
    offline = delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    kernel = Kernel(offline.resources[0].xfrm, unit=True)
    kernel.stopped = False
    with pytest.raises(ValueError, match="active historical unit must be stopped explicitly"):
        live_plan(settings, monkeypatch, kernel)
    assert kernel.link
    assert ["ifdown", "--force", "gre-old"] not in kernel.commands


@pytest.mark.parametrize("wildcard", [False, True])
def test_extra_kernel_policy_using_same_state_rejects_before_ifdown(
    tmp_path, monkeypatch, wildcard
):
    settings, _, _ = setup_pair(tmp_path)
    offline = delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    kernel = Kernel(offline.resources[0].xfrm)
    extra = policy(offline.resources[0].xfrm.directions[0])
    extra["dir"] = "fwd"
    if wildcard:
        extra["tmpl"][0].update(src="0.0.0.0", dst="0.0.0.0", reqid=0)
    kernel.policies.append(extra)
    with pytest.raises(ValueError, match="additional kernel policy uses its state"):
        live_plan(settings, monkeypatch, kernel)
    assert kernel.link and len(kernel.policies) == 3
