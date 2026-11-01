"""Paging a command does not do is refused, check pages count, batches can stop early."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as adapter
from xpedition_cli.backends.native_xpedition import native_fix


@pytest.mark.parametrize(
    "argv",
    [["bom", "check"], ["doctor"], ["project", "inspect"], ["schematic", "sheets"]],
)
def test_limit_on_a_command_that_does_not_page_is_refused(cli, project, argv) -> None:
    code, result = cli(*argv, "--project", str(project), "--limit", "1")
    assert code == 2 and result["error"]["code"] == "E_USAGE"
    assert "takes no --limit" in result["error"]["message"]


def test_continue_on_error_is_refused_where_there_is_no_batch(cli, project) -> None:
    code, result = cli(
        "schematic", "checks", "run", "--project", str(project), "--continue-on-error", "false"
    )
    assert code == 2 and result["error"]["code"] == "E_USAGE"


def test_a_check_page_says_how_many_it_holds(cli, adapter, project) -> None:
    adapter.on("verify", {"scheme": "full", "findings": [], "logs": []})
    code, result = cli("schematic", "checks", "run", "--project", str(project), "--limit", "1")
    assert code == 0
    assert result["data"]["count"] == len(result["data"]["findings"]) <= 1


class Collection:
    def __init__(self, items):
        self.items = list(items)
        self.Count = len(self.items)

    def Item(self, index):
        return self.items[index - 1]


class Routed:
    def __init__(self, net: str, refuse: bool = False):
        self.Net = SimpleNamespace(Name=net)
        self.refuse = refuse
        self.deleted = False

    def Delete(self):
        if self.refuse:
            raise RuntimeError("locked")
        self.deleted = True


def test_unroute_stops_at_the_first_failed_net_when_told(tmp_path, monkeypatch) -> None:
    from test_unroute_native import native_case

    app, params, events = native_case(tmp_path, monkeypatch, "partial")
    params.update(nets=["B", "A"], continue_on_error=False)
    preview = adapter._unroute_nets(params, None)
    with pytest.raises(adapter.AdapterError) as failed:
        adapter._unroute_nets(
            {**params, "apply": True, "expected": preview["unroute"]["fingerprint"]}, None
        )
    result = failed.value.details["native_result"]
    assert [item["target"] for item in result["items"]] == ["B"]
    assert result["skipped"] == ["A"] and events == ["delete:92"]
    assert len(app.ActiveDocument.state["routing_objects"]) == 2
    assert result["summary"] == {"total": 1, "succeeded": 0, "failed": 1}


@pytest.mark.parametrize(
    ("status", "fix"),
    [
        ({"automation_command_configured": False}, "install the native COM adapter"),
        (
            {
                "automation_command_configured": True,
                "reason": "configured native COM adapter was not found",
            },
            "XPEDITION_NATIVE_COMMAND",
        ),
        (
            {
                "automation_command_configured": True,
                "reason": "Xpedition SDD_HOME could not be discovered",
            },
            "SDD_HOME",
        ),
        (
            {
                "automation_command_configured": True,
                "reason": "Xpedition COM automation is not registered",
            },
            "register-xpedition-user.ps1",
        ),
    ],
)
def test_doctor_names_the_fix_for_the_reason_given(status, fix) -> None:
    assert fix in native_fix(status)
