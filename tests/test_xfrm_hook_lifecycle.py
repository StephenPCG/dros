from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from dros.events import enqueue_event, process_event, process_event_queue
from dros.settings import DrosPaths, DrosSettings


def settings_for(tmp_path: Path) -> DrosSettings:
    settings = DrosSettings(
        sysRoot="/",
        paths=DrosPaths(configs=tmp_path / "configs", run=tmp_path / "run", logs=tmp_path / "logs"),
    )
    settings.paths.configs.mkdir()
    write_object(
        settings,
        "XfrmTransport",
        "office",
        {
            "localParty": "partyA",
            "partyA": {"publicIp": "192.0.2.10"},
            "partyB": {"publicIp": "198.51.100.20"},
            "spi": {"partyAToPartyB": "0x100", "partyBToPartyA": "0x101"},
            "reqid": {"partyAToPartyB": 100, "partyBToPartyA": 101},
            "keys": {"partyAToPartyB": "0x" + "11" * 20, "partyBToPartyA": "0x" + "22" * 20},
        },
    )
    return settings


def write_object(settings, kind, name, spec):
    path = settings.paths.configs / f"{kind}-{name}.yaml"
    path.write_text(yaml.safe_dump({"kind": kind, "metadata": {"name": name}, "spec": spec}))
    return path


def add_interface(settings, name="gre-office", *, kind="gre"):
    spec = {"type": kind, "xfrmTransport": "office"}
    if kind == "gre":
        spec.update(
            {
                "localPublicIp": "192.0.2.10",
                "remotePublicIp": "198.51.100.20",
                "localVip": "10.0.0.1",
            }
        )
    return write_object(settings, "Interface", name, spec)


def link(name="gre-office", *, kind="gre", remote="198.51.100.20"):
    return {
        "ifname": name,
        "linkinfo": {
            "info_kind": kind,
            "info_data": {
                "local": "192.0.2.10",
                "remote": remote,
            },
        },
    }


class Kernel:
    def __init__(self, links):
        self.links = links
        self.commands = []
        self.inventory_result = None

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command == ["ip", "-json", "-details", "link", "show"]:
            if self.inventory_result is not None:
                return self.inventory_result
            return subprocess.CompletedProcess(command, 0, json.dumps(self.links), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    @property
    def mutations(self):
        return [command for command in self.commands if command[:2] != ["ip", "-json"]]


@pytest.mark.parametrize("interfaces", [False, True])
def test_start_does_not_reinstall_after_interface_uninstall(tmp_path, interfaces):
    settings = settings_for(tmp_path)
    if interfaces:
        source = add_interface(settings)
        before = source.read_bytes()
    kernel = Kernel([])
    process_event(settings, "xfrm-start", iface="office", runner=kernel)
    assert not kernel.mutations
    if interfaces:
        assert source.read_bytes() == before


@pytest.mark.parametrize("kind", ["gre", "wireguard"])
def test_start_with_matching_live_interface_keeps_gre_and_wireguard_hooks(tmp_path, kind):
    settings = settings_for(tmp_path)
    add_interface(settings, kind=kind)
    kernel = Kernel([link(kind=kind)])
    process_event(settings, "xfrm-start", iface="xfrm/office", runner=kernel)
    assert len([c for c in kernel.mutations if c[:4] == ["ip", "xfrm", "state", "add"]]) == 2
    assert len([c for c in kernel.mutations if c[:4] == ["ip", "xfrm", "policy", "add"]]) == 2


@pytest.mark.parametrize("shared", [False, True])
def test_delayed_stop_does_not_remove_live_transport(tmp_path, shared):
    settings = settings_for(tmp_path)
    add_interface(settings)
    if shared:
        add_interface(settings, "gre-second")
    kernel = Kernel([link("gre-second" if shared else "gre-office")])
    process_event(settings, "xfrm-stop", iface="office", runner=kernel)
    assert not kernel.mutations


def test_stop_after_last_interface_disappears_cleans_transport(tmp_path):
    settings = settings_for(tmp_path)
    add_interface(settings)
    kernel = Kernel([])
    process_event(settings, "xfrm-stop", iface="office", runner=kernel)
    assert len(kernel.mutations) == 4
    assert all("xfrm" in command[2] and "delete" in command[2] for command in kernel.mutations)


@pytest.mark.parametrize("event", ["xfrm-start", "xfrm-stop"])
@pytest.mark.parametrize("runtime", [link(kind="bridge"), link(remote="198.51.100.99")])
def test_reused_interface_name_or_drift_fails_without_transport_mutation(tmp_path, event, runtime):
    settings = settings_for(tmp_path)
    add_interface(settings)
    kernel = Kernel([runtime])
    with pytest.raises(ValueError, match="runtime (type|endpoints) differ"):
        process_event(settings, event, iface="office", runner=kernel)
    assert not kernel.mutations


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess([], 1, "", "permission denied"),
        subprocess.CompletedProcess([], 0, "invalid json", ""),
        subprocess.CompletedProcess([], 0, '{"ifname": "gre-office"}', ""),
        subprocess.CompletedProcess([], 0, '["gre-office"]', ""),
    ],
)
@pytest.mark.parametrize("event", ["xfrm-start", "xfrm-stop"])
def test_inventory_failure_fails_closed(tmp_path, result, event):
    settings = settings_for(tmp_path)
    add_interface(settings)
    kernel = Kernel([])
    kernel.inventory_result = result
    with pytest.raises((ValueError, RuntimeError), match="(inventory|cannot inspect)"):
        process_event(settings, event, iface="office", runner=kernel)
    assert not kernel.mutations


def test_queued_hooks_cannot_revive_deleted_interface_and_do_not_break_next_update(tmp_path):
    settings = settings_for(tmp_path)
    source = add_interface(settings)
    before = source.read_bytes()
    kernel = Kernel([])
    enqueue_event(settings, "xfrm-start", "office")
    enqueue_event(settings, "xfrm-stop", "office")
    assert process_event_queue(settings, runner=kernel) == 2
    assert not any(c[:4] == ["ip", "xfrm", "state", "add"] for c in kernel.commands)
    assert source.read_bytes() == before

    # Explicit update has recreated the interface; the retained source should
    # allow its next up event, while an older down event must no longer stop it.
    kernel.links = [link()]
    kernel.commands.clear()
    enqueue_event(settings, "xfrm-start", "office")
    enqueue_event(settings, "xfrm-stop", "office")
    assert process_event_queue(settings, runner=kernel) == 2
    assert len(kernel.mutations) == 8  # start's four cleanup + four add commands only
    assert sum(c[:4] == ["ip", "xfrm", "state", "add"] for c in kernel.mutations) == 2


def test_disabled_transport_start_does_not_inspect_or_mutate_kernel(tmp_path):
    settings = settings_for(tmp_path)
    add_interface(settings)
    path = settings.paths.configs / "XfrmTransport-office.yaml"
    data = yaml.safe_load(path.read_text())
    data["spec"]["enabled"] = False
    path.write_text(yaml.safe_dump(data))
    kernel = Kernel([link()])
    process_event(settings, "xfrm-start", iface="office", runner=kernel)
    assert not kernel.commands


def test_disabled_interface_still_guards_live_transport_against_old_stop(tmp_path):
    settings = settings_for(tmp_path)
    path = add_interface(settings)
    data = yaml.safe_load(path.read_text())
    data["metadata"]["disabled"] = True
    path.write_text(yaml.safe_dump(data))
    kernel = Kernel([link()])
    process_event(settings, "xfrm-start", iface="office", runner=kernel)
    assert not kernel.commands
    process_event(settings, "xfrm-stop", iface="office", runner=kernel)
    assert kernel.commands and not kernel.mutations


def test_offline_hook_never_reads_host_kernel(tmp_path):
    settings = settings_for(tmp_path).model_copy(update={"sys_root": tmp_path / "sysroot"})
    add_interface(settings)

    def forbidden(*args, **kwargs):
        pytest.fail("offline hook accessed host kernel")

    assert not process_event(settings, "xfrm-start", iface="office", runner=forbidden).actions
    stopped = process_event(settings, "xfrm-stop", iface="office", runner=forbidden)
    assert len(stopped.actions) == 4 and all(action.skipped for action in stopped.actions)
