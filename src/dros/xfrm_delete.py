"""Secret-free, scoped XFRM inventory and uninstall identities.

Unlike the normal stop path, uninstall fails closed on inspection/deletion
errors and checks both ownership and final absence. No command flushes XFRM.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dros.config_objects import ConfigObject, ConfigStore, InterfaceConfig, XfrmTransportConfig
from dros.executor import CommandRunner
from dros.plugins.network_xfrm import (
    _direction,
    _host_prefix,
    _resolve_transport,
    _xfrm_service_name,
)


@dataclass(frozen=True)
class XfrmDirection:
    direction: str
    src: str
    dst: str
    selector_proto: str
    spi: int
    reqid: int

    @property
    def policy_identity(self) -> tuple[str, str, str, str]:
        return self.direction, self.src, self.dst, self.selector_proto

    @property
    def state_identity(self) -> tuple[str, int]:
        # ESP kernel lookup uses destination/SPI/protocol, not source.
        return self.dst, self.spi

    @property
    def policy_command(self) -> list[str]:
        return [
            "ip",
            "xfrm",
            "policy",
            "delete",
            "dir",
            self.direction,
            "src",
            _host_prefix(self.src),
            "dst",
            _host_prefix(self.dst),
            "proto",
            self.selector_proto,
        ]

    @property
    def state_command(self) -> list[str]:
        return [
            "ip",
            "xfrm",
            "state",
            "delete",
            "src",
            self.src,
            "dst",
            self.dst,
            "proto",
            "esp",
            "spi",
            f"0x{self.spi:08x}",
        ]


@dataclass(frozen=True)
class XfrmRemoval:
    name: str
    source: Path
    directions: tuple[XfrmDirection, ...]
    service_file: str | None = None

    @property
    def service(self) -> str:
        return _xfrm_service_name(self.name)


def _config(obj: ConfigObject) -> XfrmTransportConfig:
    try:
        config = XfrmTransportConfig.model_validate(obj.spec)
        # Address validation is performed by the update plugin, not its model.
        for party in (config.party_a, config.party_b):
            ipaddress.ip_address(party.public_ip)
            if party.private_ip:
                ipaddress.ip_address(party.private_ip)
        _resolve_transport(obj.name, config)
        return config
    except (ValidationError, ValueError):
        raise ValueError(
            f"XfrmTransport/{obj.name}: invalid configuration; cannot prove exclusivity"
        ) from None


def _directions(obj: ConfigObject) -> tuple[XfrmDirection, ...]:
    transport = _resolve_transport(obj.name, _config(obj))
    result = []
    for outgoing in (True, False):
        src, dst, material = _direction(transport, outgoing=outgoing)
        result.append(
            XfrmDirection(
                "out" if outgoing else "in",
                str(ipaddress.ip_address(src)),
                str(ipaddress.ip_address(dst)),
                transport.selector_proto,
                int(material.spi, 0),
                int(material.reqid, 0),
            )
        )
    return tuple(result)


def plan_xfrm(configs: ConfigStore, interface: ConfigObject, name: str) -> XfrmRemoval:
    # Import locally to avoid a module cycle with the delete orchestration.
    from dros.delete import _references, _safe_name

    _safe_name(name)
    obj = configs.get("XfrmTransport", name)
    if obj is None:
        raise ValueError(f"Interface/{interface.name}: undefined XfrmTransport/{name}")
    config = _config(obj)
    if config.activation != "manual" or config.selector.proto != "gre":
        raise ValueError(
            f"Interface/{interface.name}: automatic XFRM uninstall requires a manual GRE transport"
        )
    directions = _directions(obj)
    for other in configs.objects():
        if other.key in {interface.key, obj.key}:
            continue
        references = _references(other, name)
        if references:
            raise ValueError(
                f"XfrmTransport/{name} is shared: referenced by {other.kind}/{other.name} "
                f"at {', '.join(references)} ({other.source}); refusing uninstall"
            )
        if other.kind == "Interface":
            try:
                iface = InterfaceConfig.model_validate(other.spec)
            except ValidationError:
                raise ValueError(
                    f"Interface/{other.name}: invalid configuration; cannot prove XFRM exclusivity"
                ) from None
            if (
                iface.type == "gre"
                and iface.local_public_ip
                in {
                    directions[0].src,
                    "0.0.0.0",
                    "::",
                    None,
                }
                and iface.remote_public_ip in {directions[0].dst, "0.0.0.0", "::", None}
            ):
                raise ValueError(
                    f"XfrmTransport/{name} is shared: Interface/{other.name} uses the same GRE "
                    "endpoints, even without an explicit xfrmTransport reference; refusing uninstall"
                )
        if other.kind == "XfrmTransport":
            other_directions = _directions(other)
            if any(
                left.policy_identity == right.policy_identity
                or left.state_identity == right.state_identity
                for left in directions
                for right in other_directions
            ):
                raise ValueError(
                    f"XfrmTransport/{name} is shared: policy/state identity overlaps "
                    f"XfrmTransport/{other.name} ({other.source}); refusing uninstall"
                )
    return XfrmRemoval(name, obj.source, directions)


def _integer(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0) if value.startswith("0x") else int(value)
    raise ValueError("invalid XFRM integer")


def _address(value: Any) -> str:
    return str(ipaddress.ip_interface(value).ip)


def _proto(value: Any) -> str:
    text = str(value).lower()
    return {"47": "gre", "17": "udp", "50": "esp"}.get(text, text)


def _pairs(text: str, allowed: set[str]) -> dict[str, Any]:
    words = text.split()
    if len(words) % 2:
        raise ValueError("unrecognized XFRM fields")
    result: dict[str, Any] = {}
    for key, value in zip(words[::2], words[1::2]):
        if key not in allowed or key in result:
            raise ValueError("unrecognized or duplicate XFRM field")
        result[key] = value
    return result


def _text_inventory(text: str, kind: str) -> list[dict[str, Any]]:
    """Parse iproute2's one-line records without retaining key/algorithm output.

    Debian 12 iproute2 silently ignores -json for XFRM. Use its stable
    -oneline section delimiters; unfamiliar metadata makes an otherwise
    matching record unowned, instead of silently broadening deletion scope.
    """
    records = []
    selector_fields = {"src", "dst", "proto", "sport", "dport", "dev", "ifindex", "uid"}
    for line in text.splitlines():
        if not line.strip():
            continue
        sections = [part.strip() for part in line.split("\\") if part.strip()]
        if not sections or not sections[0].startswith("src "):
            raise ValueError("unrecognized XFRM record")
        item = _pairs(sections[0], selector_fields if kind == "policy" else {"src", "dst"})
        if not {"src", "dst"} <= item.keys():
            raise ValueError("missing XFRM addresses")
        template = None
        for section in sections[1:]:
            if kind == "state" and section.startswith("proto "):
                values = _pairs(section, {"proto", "spi", "reqid", "mode"})
                if set(values) & set(item):
                    raise ValueError("duplicate XFRM state fields")
                item.update(values)
            elif kind == "state" and section.startswith("sel "):
                if "sel" in item:
                    raise ValueError("duplicate XFRM selector")
                item["sel"] = _pairs(section[4:], selector_fields)
            elif kind == "policy" and section.startswith("dir "):
                values = _pairs(section, {"dir", "priority", "ptype", "action"})
                if set(values) & set(item):
                    raise ValueError("duplicate XFRM policy fields")
                item.update(values)
            elif kind == "policy" and section.startswith("tmpl "):
                template = _pairs(section[5:], {"src", "dst"})
                item.setdefault("tmpl", []).append(template)
            elif kind == "policy" and section.startswith("proto ") and template is not None:
                values = _pairs(section, {"proto", "spi", "reqid", "mode", "level"})
                if set(values) & set(template):
                    raise ValueError("duplicate XFRM template fields")
                template.update(values)
                if values.get("level", "required") != "required":
                    item["_unrecognized"] = True
            elif kind == "state" and (
                section.startswith("replay-window ") or section.startswith("anti-replay context: ")
            ):
                # Replay counters do not affect the deletion identity.
                continue
            elif kind == "state" and re.fullmatch(
                r"lastused (?:[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}|-|invalid-time)",
                section,
            ):
                # iproute2's XFRMA_LASTUSED/strxf_time output is usage metadata,
                # not ownership. Match the whole section to keep unknown fields
                # and trailing scope attributes on the fail-closed path.
                continue
            elif kind == "state" and section.startswith("aead "):
                # No algorithm/key text is retained in the inventory or plan.
                # Require the nokeys sentinel to fail closed if the command
                # unexpectedly returned secret-bearing output.
                if "<<Keys hidden>>" not in section:
                    raise ValueError("XFRM key redaction was not honored")
            else:
                # Marks, if_id, contexts, encap/offload and unfamiliar fields
                # must never be mistaken for DROS' plain transport objects.
                item["_unrecognized"] = True
        required = {"dir"} if kind == "policy" else {"proto", "spi", "reqid", "mode"}
        if not required <= item.keys():
            raise ValueError("missing XFRM identity fields")
        records.append(item)
    return records


def inventory(runner: CommandRunner, kind: str) -> list[dict[str, Any]]:
    if kind not in {"policy", "state"}:
        raise ValueError("unsupported XFRM inventory kind")
    command = ["ip", "-oneline", "xfrm", kind, "list"]
    if kind == "state":
        command.append("nokeys")
    result = runner(command, check=False, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(
            f"cannot inspect XFRM {kind}; live inspection requires NET_ADMIN/root. "
            "Use sudo gw delete <target> for a read-only preview; no key material is displayed"
        )
    try:
        output = result.stdout.strip()
        data = (
            json.loads(output) if output.startswith(("[", "{")) else _text_inventory(output, kind)
        )
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError()
        return data
    except (ValueError, TypeError):
        raise ValueError(f"invalid XFRM {kind} inventory; refusing uninstall") from None


def _unscoped(item: dict[str, Any]) -> bool:
    # Scoped/manual entries cannot be claimed by DROS' unmarked transport model.
    return not any(
        item.get(key)
        for key in (
            "mark",
            "if_id",
            "ifid",
            "ctx",
            "secctx",
            "offload",
            "encap",
            "output_mark",
            "_unrecognized",
        )
    )


def matching_policy(items: list[dict[str, Any]], target: XfrmDirection) -> bool:
    candidates = []
    for item in items:
        selector = item.get("sel", item)
        try:
            if (
                item.get("dir"),
                _address(selector["src"]),
                _address(selector["dst"]),
                _proto(selector.get("proto")),
            ) == target.policy_identity:
                candidates.append(item)
        except (ValueError, TypeError, KeyError):
            raise ValueError("invalid XFRM policy selector; refusing uninstall") from None
    if not candidates:
        return False
    if len(candidates) != 1:
        raise ValueError("ambiguous XFRM policy identity; refusing uninstall")
    item = candidates[0]
    selector = item.get("sel", item)
    templates = item.get("tmpl", [])
    if isinstance(templates, dict):
        templates = [templates]
    try:
        owned = (
            _unscoped(item)
            and item.get("action", "allow") == "allow"
            and item.get("ptype", "main") == "main"
            and _integer(item.get("priority", 0)) == 0
            and not any(selector.get(k) for k in ("sport", "dport", "dev", "ifindex", "uid"))
            and ipaddress.ip_network(selector["src"], strict=False).num_addresses == 1
            and ipaddress.ip_network(selector["dst"], strict=False).num_addresses == 1
            and len(templates) == 1
            and _address(templates[0]["src"]) == target.src
            and _address(templates[0]["dst"]) == target.dst
            and _proto(templates[0]["proto"]) == "esp"
            and templates[0]["mode"] == "transport"
            and _integer(templates[0]["reqid"]) == target.reqid
            and _integer(templates[0].get("spi", 0)) == 0
            and templates[0].get("level", "required") == "required"
        )
    except (ValueError, TypeError, KeyError):
        owned = False
    if not owned:
        raise ValueError("XFRM policy differs from source ownership; refusing uninstall")
    return True


def matching_state(items: list[dict[str, Any]], target: XfrmDirection) -> bool:
    candidates = []
    for item in items:
        try:
            if (_address(item["dst"]), _integer(item["spi"])) == target.state_identity and _proto(
                item["proto"]
            ) == "esp":
                candidates.append(item)
        except (ValueError, TypeError, KeyError):
            raise ValueError("invalid XFRM state identity; refusing uninstall") from None
    if not candidates:
        return False
    if len(candidates) != 1:
        raise ValueError("ambiguous XFRM state identity; refusing uninstall")
    item = candidates[0]
    selector = item.get("sel", {})
    try:
        owned = (
            _unscoped(item)
            and _address(item["src"]) == target.src
            and item["mode"] == "transport"
            and _integer(item["reqid"]) == target.reqid
            and _address(selector["src"]) == target.src
            and _address(selector["dst"]) == target.dst
            and ipaddress.ip_network(selector["src"], strict=False).num_addresses == 1
            and ipaddress.ip_network(selector["dst"], strict=False).num_addresses == 1
            and _proto(selector["proto"]) == target.selector_proto
            and not any(selector.get(k) for k in ("sport", "dport", "dev", "ifindex", "uid"))
        )
    except (ValueError, TypeError, KeyError):
        owned = False
    if not owned:
        raise ValueError("XFRM state differs from source ownership; refusing uninstall")
    return True


def _check_policy_consumers(policies: list[dict[str, Any]], removal: XfrmRemoval) -> None:
    owned_identities = {item.policy_identity for item in removal.directions}
    for policy in policies:
        selector = policy.get("sel", policy)
        try:
            identity = (
                policy.get("dir"),
                _address(selector["src"]),
                _address(selector["dst"]),
                _proto(selector.get("proto")),
            )
        except (ValueError, TypeError, KeyError):
            raise ValueError("invalid XFRM policy selector; refusing uninstall") from None
        if identity in owned_identities:
            continue
        templates = policy.get("tmpl", [])
        if isinstance(templates, dict):
            templates = [templates]
        for template in templates:
            try:
                shared = any(
                    _proto(template["proto"]) == "esp"
                    and template["mode"] == "transport"
                    and _address(template["src"]) in {direction.src, "0.0.0.0", "::"}
                    and _address(template["dst"]) in {direction.dst, "0.0.0.0", "::"}
                    and _integer(template.get("reqid", 0)) in {0, direction.reqid}
                    and _integer(template.get("spi", 0)) in {0, direction.spi}
                    for direction in removal.directions
                )
            except (ValueError, TypeError, KeyError):
                raise ValueError("invalid XFRM policy template; cannot prove exclusivity") from None
            if shared:
                raise ValueError(
                    f"XfrmTransport/{removal.name} is shared: an additional kernel policy "
                    f"uses its state (dir={identity[0]}, src={selector['src']}, "
                    f"dst={selector['dst']}, proto={identity[3]}); refusing uninstall"
                )


def inspect_xfrm(removal: XfrmRemoval, runner: CommandRunner) -> tuple[list[bool], list[bool]]:
    policies = inventory(runner, "policy")
    states = inventory(runner, "state")
    _check_policy_consumers(policies, removal)
    return (
        [matching_policy(policies, direction) for direction in removal.directions],
        [matching_state(states, direction) for direction in removal.directions],
    )


def inspect_service(removal: XfrmRemoval, runner: CommandRunner) -> None:
    result = runner(
        [
            "systemctl",
            "show",
            removal.service,
            "--property=LoadState,FragmentPath,DropInPaths,ActiveState,UnitFileState",
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(f"cannot inspect systemd unit {removal.service}")
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    required = {"LoadState", "FragmentPath", "DropInPaths", "ActiveState", "UnitFileState"}
    if not required <= values.keys() or not values["LoadState"] or not values["ActiveState"]:
        raise ValueError(f"{removal.service}: incomplete systemd inventory; refusing uninstall")
    fragment = values.get("FragmentPath", "")
    if values.get("DropInPaths") or fragment not in {"", removal.service_file}:
        raise ValueError(
            f"{removal.service}: unexpected unit location/drop-ins; refusing uninstall"
        )
    if removal.service_file is not None and (
        values["LoadState"] != "loaded" or fragment != removal.service_file
    ):
        raise ValueError(
            f"{removal.service}: expected owned unit is not loaded; refusing uninstall"
        )
    if removal.service_file is not None and values["ActiveState"] not in {"inactive", "failed"}:
        raise ValueError(
            f"{removal.service}: active historical unit must be stopped explicitly before delete; "
            "its ExecStop acquires the gateway apply lock"
        )
    if removal.service_file is None and (
        values.get("LoadState") != "not-found"
        or values.get("ActiveState") not in {"inactive", "failed"}
        or values.get("UnitFileState") not in {"", "not-found"}
    ):
        raise ValueError(
            f"{removal.service}: runtime unit has no matching owned file; refusing uninstall"
        )


def uninstall_xfrm(removal: XfrmRemoval, runner: CommandRunner) -> None:
    from dros.delete import _run

    policies, states = inspect_xfrm(removal, runner)
    for direction, present in zip(removal.directions, policies):
        if present:
            _run(runner, direction.policy_command)
    for direction, present in zip(removal.directions, states):
        if present:
            _run(runner, direction.state_command)
    policies, states = inspect_xfrm(removal, runner)
    if any(policies) or any(states):
        raise RuntimeError("XFRM policy/state remains after uninstall; generated files retained")
