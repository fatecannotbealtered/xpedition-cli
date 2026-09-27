"""Paging a command does not do is refused, review pages count, batches can stop early."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from xpedition_cli import main as cli
from xpedition_cli import native_com_adapter as adapter


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "project.json"
    bom = [{"refdes": f"R{i}", "part_number": "", "quantity": 1} for i in (1, 2, 3)]
    path.write_text(json.dumps({"project": "demo", "bom": bom}), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(
    "argv",
    [
        ["bom", "validate", "--backend", "mock"],
        ["doctor"],
        ["project", "info", "--backend", "mock"],
    ],
)
def test_limit_on_a_command_that_does_not_page_is_refused(capsys, project, argv) -> None:
    code, result = run(capsys, *argv, "--project", project, "--limit", "1")
    assert code == 2 and result["error"]["code"] == "E_USAGE"
    assert "takes no --limit" in result["error"]["message"]


def test_continue_on_error_is_refused_where_there_is_no_batch(capsys, project) -> None:
    argv = ["review", "run", "--backend", "mock", "--project", project]
    code, result = run(capsys, *argv, "--continue-on-error", "false")
    assert code == 2 and result["error"]["code"] == "E_USAGE"


def test_a_draw_confirm_without_dangerous_is_refused_before_the_token_is_spent(
    capsys, tmp_path
) -> None:
    from pathlib import Path

    design = Path(__file__).resolve().parents[1] / "examples" / "demo-sensor-board.json"
    prj = tmp_path / "p.prj"
    prj.write_text("", encoding="utf-8")
    argv = ["schematic", "draw", "--project", str(prj), "--design", str(design)]
    _, dry = run(capsys, *argv, "--dry-run")
    assert dry["data"]["preview"]["dangerous"] is True
    code, result = run(capsys, *argv, "--confirm", dry["data"]["confirm_token"])
    assert code == 5 and result["error"]["code"] == "E_CONFIRMATION_REQUIRED"


def test_a_review_page_says_how_many_it_holds(capsys, project) -> None:
    code, result = run(
        capsys, "review", "run", "--backend", "mock", "--project", project, "--limit", "1"
    )
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


def test_unroute_stops_at_the_first_failed_net_when_told(monkeypatch) -> None:
    scl, sda = Routed("SCL", refuse=True), Routed("SDA")
    doc = SimpleNamespace(Traces=Collection([scl, sda]), Vias=Collection([]), Save=lambda: None)
    monkeypatch.setattr(adapter, "_layout_board_path", lambda params: "demo.pcb")
    monkeypatch.setattr(adapter, "_application", lambda client, attach_only: object())
    monkeypatch.setattr(adapter, "_open_layout_document", lambda app, path: (doc, []))
    monkeypatch.setattr(adapter, "_licensed_document", lambda app: doc)
    monkeypatch.setattr(adapter, "_find_net", lambda doc, name: name)
    monkeypatch.setattr(adapter, "_regenerate_planes", lambda doc: True)
    monkeypatch.setattr(adapter, "_routing_counts", lambda doc: {"traces": 1, "vias": 0})
    params = {"nets": ["SCL", "SDA"], "apply": True, "continue_on_error": False}
    result = adapter._unroute_nets(params, None)
    assert [item["target"] for item in result["items"]] == ["SCL"]
    assert result["skipped"] == ["SDA"] and sda.deleted is False
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
    assert fix in cli._native_fix(status)
