from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dros.xfrm_delete import (
    XfrmDirection,
    XfrmRemoval,
    inspect_service,
    inventory,
    matching_policy,
    matching_state,
)


# Actual iproute2 6.1 output from an isolated Debian 12 kernel test, using only
# RFC 5737 endpoints and synthetic keys (omitted by the real `nokeys` command).
STATE = (
    "src 192.0.2.1 dst 192.0.2.2\\\tproto esp spi 0x00001001 reqid 101 mode transport"
    "\\\treplay-window 0 \\\taead rfc4106(gcm(aes)) <<Keys hidden>> 128"
    "\\\tanti-replay context: seq 0x0, oseq 0x0, bitmap 0x00000000"
    "\\\tsel src 192.0.2.1/32 dst 192.0.2.2/32 proto gre \\\n"
)
POLICY = (
    "src 192.0.2.1/32 dst 192.0.2.2/32 proto gre \\\tdir out priority 0 ptype main "
    "\\\ttmpl src 192.0.2.1 dst 192.0.2.2\\\t\tproto esp reqid 101 mode transport\\\n"
)
TARGET = XfrmDirection("out", "192.0.2.1", "192.0.2.2", "gre", 0x1001, 101)


def runner_for(text, *, status=0):
    def runner(command, **kwargs):
        assert command[:3] == ["ip", "-oneline", "xfrm"]
        if command[3] == "state":
            assert command[-1] == "nokeys"
        return subprocess.CompletedProcess(command, status, text, "")

    return runner


@pytest.mark.parametrize(
    "kind,text,matcher",
    [
        ("state", STATE, matching_state),
        ("policy", POLICY, matching_policy),
    ],
)
def test_actual_debian12_oneline_output(kind, text, matcher):
    items = inventory(runner_for(text), kind)
    assert len(items) == 1
    assert matcher(items, TARGET)
    assert "Keys" not in repr(items) and "aead" not in repr(items)


@pytest.mark.parametrize("timestamp", ["2026-10-23 16:33:44", "-", "invalid-time"])
def test_used_state_lastused_metadata_does_not_change_ownership(timestamp):
    # A used SA includes this section even without -s. The original isolated
    # fixture had no traffic and therefore missed it. Addresses remain synthetic.
    used = STATE.replace("\\\tanti-replay", f"\\\tlastused {timestamp}\\\tanti-replay")
    items = inventory(runner_for(used), "state")
    assert matching_state(items, TARGET)
    assert items == inventory(runner_for(STATE), "state")


@pytest.mark.parametrize(
    "section",
    [
        "lastused",
        "lastused unknown",
        "lastused (ERROR truncated)",
        "lastused 2026-10-23 16:33:44 mark 0x1/0xffffffff",
        "lastused 2026-10-23 16:33:44\\\tmark 0x1/0xffffffff",
        "lastused 2026-10-23 16:33:44\\\tif_id 2",
        "lastused 2026-10-23 16:33:44\\\tunknown-metadata x",
    ],
)
def test_lastused_does_not_hide_unrecognized_or_scoped_metadata(section):
    used = STATE.replace("\\\tanti-replay", f"\\\t{section}\\\tanti-replay")
    with pytest.raises(ValueError, match="ownership"):
        matching_state(inventory(runner_for(used), "state"), TARGET)


def test_used_state_still_requires_matching_reqid():
    used = STATE.replace("\\\tanti-replay", "\\\tlastused 2026-10-23 16:33:44\\\tanti-replay")
    used = used.replace("reqid 101", "reqid 999")
    with pytest.raises(ValueError, match="ownership"):
        matching_state(inventory(runner_for(used), "state"), TARGET)


@pytest.mark.parametrize("kind", ["state", "policy"])
def test_empty_inventory_is_valid(kind):
    assert inventory(runner_for(""), kind) == []


def test_multiple_oneline_states_preserve_record_boundaries():
    second = STATE.replace("192.0.2.2", "198.51.100.2").replace("0x00001001", "0x00002001")
    items = inventory(runner_for(STATE + second), "state")
    assert len(items) == 2 and matching_state(items, TARGET)
    assert matching_state(
        items, XfrmDirection("out", "192.0.2.1", "198.51.100.2", "gre", 0x2001, 101)
    )


@pytest.mark.parametrize(
    "text",
    [
        "garbage",
        STATE.replace("spi 0x00001001 ", ""),
        STATE.replace("src 192.0.2.1 dst", "dst"),
        STATE.replace("<<Keys hidden>>", "secret-test-key"),
    ],
)
def test_unknown_or_key_bearing_text_fails_without_echo(text):
    with pytest.raises(ValueError, match="invalid XFRM state inventory") as exc:
        inventory(runner_for(text), "state")
    assert "secret-test-key" not in str(exc.value)


@pytest.mark.parametrize("section", ["mark 0x1/0xffffffff", "if_id 2", "unknown-metadata x"])
def test_extra_state_scope_or_unknown_metadata_is_not_owned(section):
    items = inventory(runner_for(STATE.rstrip("\n") + "\t" + section + "\\\n"), "state")
    with pytest.raises(ValueError, match="ownership"):
        matching_state(items, TARGET)


def test_wide_state_selector_is_not_owned():
    items = inventory(runner_for(STATE.replace("192.0.2.1/32", "192.0.2.1/24")), "state")
    with pytest.raises(ValueError, match="ownership"):
        matching_state(items, TARGET)


def test_numeric_protocols_and_json_inventory_are_compatible():
    state = inventory(runner_for(STATE), "state")[0]
    state["proto"] = "50"
    state["sel"]["proto"] = 47
    parsed = inventory(runner_for(json.dumps([state])), "state")
    assert matching_state(parsed, TARGET)


def test_esp_destination_spi_collision_with_different_source_is_rejected():
    items = inventory(runner_for(STATE.replace("192.0.2.1", "198.51.100.99")), "state")
    with pytest.raises(ValueError, match="ownership"):
        matching_state(items, TARGET)


def test_policy_optional_or_multiple_templates_are_not_owned():
    text = POLICY.replace("mode transport", "mode transport level use")
    with pytest.raises(ValueError, match="ownership"):
        matching_policy(inventory(runner_for(text), "policy"), TARGET)
    text = (
        POLICY.rstrip("\n")
        + "\ttmpl src 192.0.2.1 dst 192.0.2.2\\\tproto esp reqid 101 mode transport\\\n"
    )
    with pytest.raises(ValueError, match="ownership"):
        matching_policy(inventory(runner_for(text), "policy"), TARGET)


def test_different_policy_priority_is_not_owned():
    with pytest.raises(ValueError, match="ownership"):
        matching_policy(
            inventory(runner_for(POLICY.replace("priority 0", "priority 10")), "policy"), TARGET
        )


@pytest.mark.parametrize("text", ["", "LoadState=not-found\n", "{invalid"])
def test_empty_or_incomplete_systemd_output_is_not_accepted_as_absence(text):
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, text, "")

    removal = XfrmRemoval("office", Path("/config/xfrm.yaml"), (TARGET,))
    with pytest.raises(ValueError, match="incomplete systemd inventory"):
        inspect_service(removal, runner)


def test_explicit_systemd_not_found_inventory_is_accepted():
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=not-found\nFragmentPath=\nDropInPaths=\nActiveState=inactive\nUnitFileState=\n",
            "",
        )

    inspect_service(XfrmRemoval("office", Path("/config/xfrm.yaml"), (TARGET,)), runner)
