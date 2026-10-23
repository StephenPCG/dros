from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from dros import delete
from dros.cli import main as cli
from dros.settings import DrosPaths, DrosSettings
from dros.update import run_update


def settings_for(tmp_path: Path) -> DrosSettings:
    settings = DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(configs=tmp_path / "configs", run=tmp_path / "run", logs=tmp_path / "logs"),
    )
    settings.paths.configs.mkdir()
    return settings


def write_object(settings, kind="Interface", name="gre-old", spec=None):
    spec = (
        spec
        if spec is not None
        else {
            "type": "gre",
            "localVip": "192.0.2.1",
            "localPublicIp": "192.0.2.2",
            "remotePublicIp": "198.51.100.1",
        }
    )
    path = settings.paths.configs / f"object-{len(list(settings.paths.configs.iterdir()))}.yaml"
    path.write_text(yaml.safe_dump({"kind": kind, "metadata": {"name": name}, "spec": spec}))
    return path


def tree(root: Path):
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def no_commands(*args, **kwargs):
    pytest.fail("offline/preview invoked an unexpected command")


@pytest.mark.parametrize("writable", [False, True])
def test_offline_cli_preview_never_elevates_or_writes(tmp_path, monkeypatch, capsys, writable):
    settings = settings_for(tmp_path)
    source = write_object(settings)
    run_update(settings, target="iface/gre-old", verbose=0)
    before = tree(tmp_path)
    monkeypatch.setattr(cli, "_load_cli_settings", lambda: settings)
    monkeypatch.setattr(cli, "_try_load_cli_settings_for_logging", no_commands)
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(cli, "path_writable_for_current_user", lambda path: writable)
    monkeypatch.setattr(cli, "reexec_with_sudo", no_commands)
    monkeypatch.setattr(cli, "_ensure_bootstrap_privileges", no_commands)
    monkeypatch.setattr(cli, "_manual_cli_lock", no_commands)
    assert cli.main(["delete", "iface/gre-old"]) == 0
    assert tree(tmp_path) == before
    output = capsys.readouterr().out
    assert "ifdown --force gre-old" in output
    assert "ip link delete dev gre-old" in output
    assert "delete file:" in output and "Preview only" in output
    assert source.read_bytes() == before[str(source.relative_to(tmp_path))]


@pytest.mark.parametrize("command", ["delete", "remove"])
@pytest.mark.parametrize("euid", [0, 1000])
@pytest.mark.parametrize("equals_settings", [False, True])
@pytest.mark.parametrize("explicit_preview", [False, True])
def test_live_cli_preview_preserves_arguments_and_remains_read_only_after_sudo(
    tmp_path, monkeypatch, capsys, command, euid, equals_settings, explicit_preview
):
    offline = settings_for(tmp_path)
    write_object(offline)
    settings = offline.model_copy(update={"sys_root": Path("/")})
    settings_path = str(tmp_path / "settings with spaces.yaml")
    args = (
        ([f"--settings={settings_path}"] if equals_settings else ["--settings", settings_path])
        + [command, "iface/gre-old"]
        + (["--no-apply"] if explicit_preview else [])
    )
    calls = []
    planned = []
    before = tree(tmp_path)
    monkeypatch.setattr(cli, "_load_cli_settings", lambda: settings)
    monkeypatch.setattr(cli, "_try_load_cli_settings_for_logging", no_commands)
    monkeypatch.setattr(cli, "_manual_cli_lock", no_commands)
    monkeypatch.setattr(cli, "apply_delete_plan", no_commands)
    monkeypatch.setattr(cli.os, "geteuid", lambda: euid)

    def reexec(command_args):
        calls.append(command_args)
        assert command_args == [sys.executable, "-m", "dros.cli.main", *args]
        assert not planned  # Elevation must happen before privileged inspection.
        monkeypatch.setattr(cli.os, "geteuid", lambda: 0)
        # Exercise the root entrypoint too, without invoking actual sudo.
        raise SystemExit(cli.main(command_args[3:]))

    def plan(actual_settings, target):
        assert cli.os.geteuid() == 0
        assert actual_settings is settings and target == "iface/gre-old"
        assert cli._current_settings_path == settings_path
        planned.append(target)
        return delete.DeletePlan((), offline=False)

    monkeypatch.setattr(cli, "reexec_with_sudo", reexec)
    monkeypatch.setattr(cli, "build_delete_plan", plan)
    assert cli.main(args) == 0
    assert len(calls) == (0 if euid == 0 else 1)
    assert planned == ["iface/gre-old"]
    assert "Preview only" in capsys.readouterr().out
    assert tree(tmp_path) == before


@pytest.mark.parametrize(
    "failure", [RuntimeError("sudo unavailable"), PermissionError("denied"), SystemExit(1)]
)
def test_live_preview_sudo_failure_stops_before_inspection_or_mutation(
    tmp_path, monkeypatch, failure
):
    settings = settings_for(tmp_path).model_copy(update={"sys_root": Path("/")})
    before = tree(tmp_path)
    monkeypatch.setattr(cli, "_load_cli_settings", lambda: settings)
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    for name in (
        "_try_load_cli_settings_for_logging",
        "_manual_cli_lock",
        "build_delete_plan",
        "apply_delete_plan",
    ):
        monkeypatch.setattr(cli, name, no_commands)

    def fail(command):
        raise failure

    monkeypatch.setattr(cli, "reexec_with_sudo", fail)
    assert cli.main(["delete", "iface/gre-old"]) == 1
    assert tree(tmp_path) == before


@pytest.mark.parametrize("live", [False, True])
def test_cli_apply_keeps_privilege_and_lock_before_execution(tmp_path, monkeypatch, live):
    settings = settings_for(tmp_path)
    if live:
        settings = settings.model_copy(update={"sys_root": Path("/")})
    calls = []
    plan = delete.DeletePlan((), offline=not live)
    monkeypatch.setattr(cli, "_load_cli_settings", lambda: settings)
    monkeypatch.setattr(cli, "_try_load_cli_settings_for_logging", no_commands)
    monkeypatch.setattr(
        cli, "_ensure_bootstrap_privileges", lambda settings: calls.append("privileges")
    )

    @contextmanager
    def lock(settings):
        calls.append("lock")
        yield
        calls.append("unlock")

    def build(settings, target):
        calls.append("plan")
        return plan

    monkeypatch.setattr(cli, "_manual_cli_lock", lock)
    monkeypatch.setattr(cli, "build_delete_plan", build)
    monkeypatch.setattr(cli, "apply_delete_plan", lambda *args, **kwargs: calls.append("apply"))
    assert cli.main(["delete", "iface/gre-old", "--apply"]) == 0
    assert calls == ["privileges", "lock", "plan", "apply", "unlock"]


def test_offline_apply_removes_only_owned_files_and_is_repeatable(tmp_path):
    settings = settings_for(tmp_path)
    source = write_object(settings)
    write_object(settings, name="gre-keep")
    run_update(settings, target="iface", verbose=0)
    before = tree(tmp_path)
    plan = delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    removed = {
        str((settings.sys_root / file.path.lstrip("/")).relative_to(tmp_path))
        for resource in plan.resources
        for file in resource.files
    }
    delete.apply_delete_plan(settings, plan, runner=no_commands)
    assert tree(tmp_path) == {key: value for key, value in before.items() if key not in removed}
    assert source.exists()
    again = delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    assert not again.resources[0].files
    delete.apply_delete_plan(settings, again, runner=no_commands)


@pytest.mark.parametrize(
    "kind,spec,path",
    [
        ("Gateway", {"dev": "gre-old"}, "spec.dev"),
        ("Gateway", {"nexthops": [{"dev": "gre-old"}]}, "spec.nexthops[0].dev"),
        ("Interface", {"type": "vlan", "parent": "gre-old", "id": 2}, "spec.parent"),
        ("Interface", {"type": "bridge", "ports": ["gre-old"]}, "spec.ports[0]"),
        ("Interface", {"type": "pppoe", "device": "gre-old"}, "spec.device"),
        ("DnsmasqDNS", {"interfaces": ["gre-old"]}, "spec.interfaces[0]"),
        ("DnsmasqDNS", {"rawConfig": "interface=gre-old"}, "spec.rawConfig"),
        ("WgsdCoreDNS", {"interfaces": [{"name": "gre-old"}]}, "spec.interfaces[0].name"),
        ("Firewall", {"natRules": [{"iif": "interface/gre-old"}]}, "spec.natRules[0].iif"),
        ("RouteTable", {"routes": [{"raw": "default dev gre-old"}]}, "spec.routes[0].raw"),
        ("ConfigMap", {"files": {"hook": "ip link set gre-old down"}}, "spec.files.hook"),
        ("IPv6PD", {"uplink": "gre-old"}, "spec.uplink"),
        ("DnsmasqDNS", {"servers": ["/old.example/192.0.2.1:53"]}, "spec.servers[0]"),
        ("Collectd", {"ping": {"hosts": ["192.0.2.1"]}}, "spec.ping.hosts[0]"),
    ],
)
def test_reverse_dependencies_block_without_changing_anything(tmp_path, kind, spec, path):
    settings = settings_for(tmp_path)
    write_object(settings)
    write_object(settings, kind, "dependent", spec)
    before = tree(tmp_path)
    with pytest.raises(ValueError, match="referenced by") as error:
        delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)
    assert path in str(error.value)
    assert tree(tmp_path) == before


def test_identifier_substrings_are_not_dependencies(tmp_path):
    settings = settings_for(tmp_path)
    write_object(settings)
    write_object(settings, "Gateway", "keep", {"dev": "gre-oldish"})
    assert delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)


def test_gateway_is_logical_only_and_rejects_route_references(tmp_path):
    settings = settings_for(tmp_path)
    write_object(settings, "Gateway", "old-site", {"dev": "gre-old"})
    route = write_object(
        settings,
        "RouteTable",
        "routes",
        {"routes": [{"to": "192.0.2.0/24", "gateway": "old-site"}]},
    )
    with pytest.raises(ValueError, match=r"spec.routes\[0\].gateway"):
        delete.build_delete_plan(settings, "gateway/old-site", runner=no_commands)
    route.unlink()
    plan = delete.build_delete_plan(settings, "gateway/old-site", runner=no_commands)
    assert not plan.resources[0].files and plan.resources[0].interface_type is None
    delete.apply_delete_plan(settings, plan, runner=no_commands)


def test_kind_selection_sorts_dependents_first(tmp_path):
    settings = settings_for(tmp_path)
    write_object(settings, name="br0", spec={"type": "bridge"})
    write_object(settings, name="br0.20", spec={"type": "vlan", "parent": "br0", "id": 20})
    plan = delete.build_delete_plan(settings, "iface", runner=no_commands)
    assert [item.name for item in plan.resources] == ["br0.20", "br0"]


@pytest.mark.parametrize(
    "target", ["", "all", "iface/", "iface/../../etc", "route", "Firewall/default"]
)
def test_unsupported_and_ambiguous_targets_fail(tmp_path, target):
    settings = settings_for(tmp_path)
    write_object(settings)
    with pytest.raises(ValueError):
        delete.build_delete_plan(settings, target, runner=no_commands)


@pytest.mark.parametrize(
    "name", ["../other", "-all", "foo;touch /tmp/bad", "foo$(id)", "a b", "x" * 16]
)
def test_unsafe_names_fail_before_running_commands(tmp_path, name):
    settings = settings_for(tmp_path)
    write_object(settings, name=name)
    with pytest.raises(ValueError, match="unsafe"):
        delete.build_delete_plan(settings, "iface", runner=no_commands)


def test_unsupported_member_prevents_entire_batch(tmp_path):
    settings = settings_for(tmp_path)
    write_object(settings)
    write_object(settings, name="eth0", spec={"type": "eth"})
    run_update(settings, verbose=0)
    before = tree(tmp_path)
    with pytest.raises(ValueError, match="does not support type eth"):
        delete.build_delete_plan(settings, "iface", runner=no_commands)
    assert tree(tmp_path) == before


def test_xfrm_lifecycle_cannot_be_silently_skipped(tmp_path):
    settings = settings_for(tmp_path)
    write_object(settings, spec={"type": "gre", "xfrmTransport": "vpn"})
    with pytest.raises(ValueError, match="undefined XfrmTransport"):
        delete.build_delete_plan(settings, "iface", runner=no_commands)


@pytest.mark.parametrize(
    "damage", ["content", "symlink", "parent_symlink", "hardlink", "directory"]
)
def test_refuses_unowned_drifted_and_linked_managed_files(tmp_path, damage):
    settings = settings_for(tmp_path)
    write_object(settings)
    run_update(settings, verbose=0)
    file = next((settings.sys_root / "etc/network/interfaces.d").glob("*-gre-old.cfg"))
    if damage == "content":
        file.write_text(file.read_text() + "# externally changed\n")
    elif damage == "symlink":
        outside = tmp_path / "outside"
        outside.write_text(file.read_text())
        file.unlink()
        file.symlink_to(outside)
    elif damage == "parent_symlink":
        directory = file.parent
        outside = tmp_path / "outside-dir"
        directory.rename(outside)
        directory.symlink_to(outside)
    elif damage == "hardlink":
        (tmp_path / "hardlink").hardlink_to(file)
    else:
        file.unlink()
        file.mkdir()
    with pytest.raises(ValueError):
        delete.build_delete_plan(settings, "iface/gre-old", runner=no_commands)


def test_all_file_hashes_checked_before_apply_mutates_first_file(tmp_path):
    settings = settings_for(tmp_path)
    write_object(settings)
    write_object(settings, name="gre-second")
    run_update(settings, verbose=0)
    plan = delete.build_delete_plan(settings, "iface", runner=no_commands)
    second_file = settings.sys_root / plan.resources[1].files[0].path.lstrip("/")
    second_file.write_text(second_file.read_text() + "# drift\n")
    before = tree(tmp_path)
    with pytest.raises(ValueError, match="changed since planning"):
        delete.apply_delete_plan(settings, plan, runner=no_commands)
    assert tree(tmp_path) == before


def test_cron_removes_own_generated_file_not_other_plugin_cron(tmp_path):
    settings = settings_for(tmp_path)
    write_object(settings, "CronJob", "nightly", {"schedule": "0 0 * * *", "command": "true"})
    run_update(settings, verbose=0)
    path = settings.sys_root / "etc/cron.d/dros-nightly"
    plan = delete.build_delete_plan(settings, "cron/nightly", runner=no_commands)
    delete.apply_delete_plan(settings, plan, runner=no_commands)
    assert not path.exists()
    path.write_text(delete.GENERATED_HEADER + "\n# Resource: Interface/other wgsdClient\n")
    with pytest.raises(ValueError, match="different resource"):
        delete.build_delete_plan(settings, "cron/nightly", runner=no_commands)


def test_active_nft_rules_require_shared_firewall_reconciliation(tmp_path):
    settings = settings_for(tmp_path)
    write_object(
        settings,
        name="wg-old",
        spec={"type": "wireguard", "privateKey": "test-key", "listenPort": 12345},
    )
    run_update(settings, verbose=0)
    with pytest.raises(ValueError, match="active shared-firewall"):
        delete.build_delete_plan(settings, "iface/wg-old", runner=no_commands)


class Kernel:
    def __init__(self):
        self.links = [{"ifname": "gre-old", "ifindex": 100, "linkinfo": {"info_kind": "gre"}}]
        self.commands = []
        self.fail_down = False
        self.fail_delete = False

    def run(self, command, **kwargs):
        self.commands.append(command)
        if command == ["ip", "-json", "-details", "link", "show"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.links), "")
        if command == ["ifdown", "--force", "gre-old"]:
            return subprocess.CompletedProcess(
                command, int(self.fail_down), "", "private-hook-output"
            )
        if command == ["ip", "link", "delete", "dev", "gre-old"]:
            if not self.fail_delete:
                self.links = []
            return subprocess.CompletedProcess(command, int(self.fail_delete), "", "")
        pytest.fail(f"unexpected command: {command}")


def real_mode_fixture(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    write_object(settings)
    run_update(settings, verbose=0)
    original_target = delete._target_path
    monkeypatch.setattr(
        delete, "_target_path", lambda _settings, path: original_target(settings, path)
    )
    return settings.model_copy(update={"sys_root": Path("/")}), Kernel()


def test_real_mode_preview_reads_kernel_and_apply_verifies_then_retries_safely(
    tmp_path, monkeypatch
):
    settings, kernel = real_mode_fixture(tmp_path, monkeypatch)
    before = tree(tmp_path)
    plan = delete.build_delete_plan(settings, "iface/gre-old", runner=kernel.run)
    assert tree(tmp_path) == before
    assert kernel.commands == [["ip", "-json", "-details", "link", "show"]]
    delete.apply_delete_plan(settings, plan, runner=kernel.run)
    assert not kernel.links
    assert ["ip", "link", "delete", "dev", "gre-old"] in kernel.commands
    kernel.commands.clear()
    again = delete.build_delete_plan(settings, "iface/gre-old", runner=kernel.run)
    delete.apply_delete_plan(settings, again, runner=kernel.run)
    assert all(command[-1] == "show" for command in kernel.commands)


@pytest.mark.parametrize("failure", ["down", "delete"])
def test_command_failure_retains_configuration_and_reports_partial_apply(
    tmp_path, monkeypatch, failure
):
    settings, kernel = real_mode_fixture(tmp_path, monkeypatch)
    kernel.fail_down = failure == "down"
    kernel.fail_delete = failure == "delete"
    plan = delete.build_delete_plan(settings, "iface/gre-old", runner=kernel.run)
    before = tree(tmp_path)
    with pytest.raises(RuntimeError, match="earlier actions may have completed") as error:
        delete.apply_delete_plan(settings, plan, runner=kernel.run)
    assert "private-hook-output" not in str(error.value)
    assert tree(tmp_path) == before


@pytest.mark.parametrize(
    "problem", ["type", "unowned", "dependent", "same_netns_parent", "inventory"]
)
def test_runtime_preflight_fails_closed(tmp_path, monkeypatch, problem):
    settings, kernel = real_mode_fixture(tmp_path, monkeypatch)
    if problem == "type":
        kernel.links[0]["linkinfo"]["info_kind"] = "bridge"
    elif problem == "unowned":
        next((tmp_path / "sysroot/etc/network/interfaces.d").glob("*.cfg")).unlink()
    elif problem == "dependent":
        kernel.links.append({"ifname": "foreign-vlan", "ifindex": 101, "link_index": 100})
    elif problem == "same_netns_parent":
        kernel.links.append({"ifname": "foreign-vlan", "ifindex": 101, "link": "gre-old"})
    else:
        kernel.run = lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "")
    before = tree(tmp_path)
    with pytest.raises((RuntimeError, ValueError)):
        delete.build_delete_plan(settings, "iface/gre-old", runner=kernel.run)
    assert tree(tmp_path) == before
