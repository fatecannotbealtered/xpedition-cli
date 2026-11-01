"""Plural flags, and the second gate on writes that destroy work (CLI-SPEC §15).

`pcb routing unroute` deletes routing that is not archived, `pcb create --replace` removes
a board's layout and ends the Layout holding it, and a confirmed `pcb placement auto`
deletes every trace and via first. Each needs `--dangerous` next to its token; the
gate is checked before the token is spent, so adding the flag is enough to retry.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import handler_harness as cli
import pytest

from xpedition_cli import native_com_adapter as adapter
from xpedition_cli.backends import native_xpedition

BOARD_PRJ = (
    "SECTION DesignInfo\n"
    'KEY CentralLibrary "RcLib\\TemplateLibrary.lmc"\n'
    "ENDSECTION\n"
    "SECTION iCDB\n"
    "LIST Designs\n"
    'VALUE "Schematic1"\n'
    'VALUE "Board1"\n'
    "ENDLIST\n"
    "ENDSECTION\n"
    "SECTION Board1\n"
    'KEY ConfigType "PCB"\n'
    'KEY PCBDesignPath "{pcb}"\n'
    "ENDSECTION\n"
)


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


# ---- plural flags ----


def test_a_plural_flag_repeats_and_mixes_with_a_comma_list() -> None:
    _, options = cli.parse_argv(
        [
            "pcb",
            "routing",
            "unroute",
            "--project",
            "b.pcb",
            "--nets",
            "A,B",
            "--nets",
            "C",
            "--nets",
            "A",
        ]
    )
    assert options["nets"] == "A,B,C,A"
    _, options = cli.parse_argv(["context", "--fields", "version", "--fields", "config"])
    assert options["fields"] == "version,config"


def test_a_singular_flag_given_twice_must_agree() -> None:
    _, options = cli.parse_argv(["context", "--project", "a.prj", "--project", "a.prj"])
    assert options["project"] == "a.prj"
    with pytest.raises(cli.CLIError) as caught:
        cli.parse_argv(["context", "--project", "a.json", "--project", "b.json"])
    assert caught.value.code == "E_USAGE"


def test_dangerous_is_refused_on_a_query(capsys) -> None:
    code, result = run(capsys, "project", "inspect", "--project", "a.prj", "--dangerous")
    assert code == 2 and result["error"]["code"] == "E_USAGE"


def test_reference_marks_the_dangerous_writes(capsys) -> None:
    _, result = run(capsys, "reference", "--compact")
    commands = {c["path"]: c for c in result["data"]["commands"]}
    dangerous = {path for path, c in commands.items() if c.get("dangerous")}
    assert dangerous == {
        "kb bind",
        "kb unbind",
        "pcb routing unroute",
        "pcb create",
        "pcb placement auto",
        "pcb routing auto",
        "pcb update",
        "library import",
        "library create",
        "project restore",
        "schematic build",
    }
    for path in dangerous:
        command = commands[path]
        assert command["permission_tier"] == "dangerous" and command["type"] == "write"
        assert command["dangerous_when"]
        assert any("--dangerous" in example for example in command["examples"])
    for path, command in commands.items():
        takes = any(param["name"] == "dangerous" for param in command["params"])
        assert takes == (path in dangerous or path in {"pcb routing trace", "pcb routing apply"}), (
            path
        )
    assert result["data"]["risk_tier"] == "T2"
    nets = next(p for p in commands["pcb routing unroute"]["params"] if p["name"] == "nets")
    assert nets["multiple"] is True


# ---- the gate, with the adapter call recorded instead of run ----


@pytest.fixture
def native(tmp_path, monkeypatch):
    binary = tmp_path / "native-adapter.exe"
    binary.write_bytes(b"placeholder")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(binary))
    monkeypatch.setattr(native_xpedition.NativeBackend, "require_implemented", lambda self: None)
    state = {"calls": [], "routing": {"traces": 3, "vias": 1}}

    def fake_run(argv, **kwargs):
        request = json.loads(kwargs["input"])
        state["calls"].append(request)
        method, params = request["method"], request["params"]
        if method == "arrange_components":
            data = {"digest": "d1", "routing": state["routing"], "plan": [], "labels": []}
        elif method == "unroute_nets":
            data = {"nets": params["nets"], "to_delete": {"traces": 2, "vias": 0}}
            data["targets"] = [{"target": n, "traces": 1, "vias": 0} for n in params["nets"]]
            data["unroute"] = {
                "fingerprint": "synthetic-gate-plan",
                "selected_objects": [{"uid": "1"}, {"uid": "2"}],
            }
        else:
            data = {"applied": True}
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": data}), "")

    monkeypatch.setattr(native_xpedition.subprocess, "run", fake_run)
    return state


def board(tmp_path, pcb: str = "PCB\\demo.pcb"):
    project = tmp_path / "demo.prj"
    project.write_text(BOARD_PRJ.format(pcb=pcb), encoding="utf-8")
    return ["--project", str(project)]


def applied(state) -> list[dict]:
    return [c for c in state["calls"] if c["params"].get("apply") is True]


def test_unroute_needs_dangerous_and_the_same_token_then_works(capsys, tmp_path, native) -> None:
    args = ["pcb", "routing", "unroute", *board(tmp_path), "--nets", "SCL", "--nets", "SDA,SCL"]
    _, dry = run(capsys, *args, "--dry-run")
    preview = dry["data"]["preview"]
    assert preview["dangerous"] is True and preview["total"] == 2
    token = dry["data"]["confirm_token"]
    code, refused = run(capsys, *args, "--confirm", token)
    assert code == 5 and refused["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    assert applied(native) == []
    code, result = run(capsys, *args, "--dangerous", "--confirm", token)
    assert code == 0, result
    [request] = applied(native)
    # each net once, in the order given
    assert request["params"]["nets"] == ["SCL", "SDA"]


def test_unroute_with_an_empty_net_list_is_a_validation_error(capsys, tmp_path, native) -> None:
    code, result = run(
        capsys, "pcb", "routing", "unroute", *board(tmp_path), "--nets", ",", "--dry-run"
    )
    assert code == 2 and result["error"]["code"] == "E_VALIDATION"


def test_arrange_on_a_routed_board_needs_dangerous(capsys, tmp_path, native) -> None:
    args = ["pcb", "placement", "auto", *board(tmp_path)]
    _, dry = run(capsys, *args, "--dry-run")
    assert dry["data"]["preview"]["dangerous"] is True
    assert dry["data"]["preview"]["risk"]["tier"] == "T2"
    token = dry["data"]["confirm_token"]
    code, refused = run(capsys, *args, "--confirm", token)
    assert code == 5 and refused["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    code, _ = run(capsys, *args, "--dangerous", "--confirm", token)
    assert code == 0 and len(applied(native)) == 1


def test_arrange_on_an_unrouted_board_needs_no_second_gate(capsys, tmp_path, native) -> None:
    native["routing"] = {"traces": 0, "vias": 0}
    args = ["pcb", "placement", "auto", *board(tmp_path)]
    _, dry = run(capsys, *args, "--dry-run")
    assert dry["data"]["preview"]["dangerous"] is False
    code, _ = run(capsys, *args, "--confirm", dry["data"]["confirm_token"])
    assert code == 0 and len(applied(native)) == 1


@pytest.mark.parametrize("verb", ["annotate"])
def test_unroute_before_route_or_annotate_needs_dangerous(capsys, tmp_path, native, verb) -> None:
    command = ["pcb", "routing", "auto"] if verb == "route" else ["pcb", "update"]
    plain = [*command, *board(tmp_path)]
    _, dry = run(capsys, *plain, "--dry-run")
    assert dry["data"]["preview"]["dangerous"] is False
    code, _ = run(capsys, *plain, "--confirm", dry["data"]["confirm_token"])
    assert code == 0
    args = [*plain, "--unroute"]
    _, dry = run(capsys, *args, "--dry-run")
    assert dry["data"]["preview"]["dangerous"] is True
    code, refused = run(capsys, *args, "--confirm", dry["data"]["confirm_token"])
    assert code == 5 and refused["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    code, _ = run(capsys, *args, "--dangerous", "--confirm", dry["data"]["confirm_token"])
    assert code == 0


def test_create_replace_needs_dangerous(capsys, tmp_path, native) -> None:
    args = ["pcb", "create", *board(tmp_path), "--replace"]
    _, dry = run(capsys, *args, "--dry-run")
    assert dry["data"]["preview"]["dangerous"] is True
    token = dry["data"]["confirm_token"]
    code, refused = run(capsys, *args, "--confirm", token)
    assert code == 5 and refused["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    assert native["calls"] == []
    code, _ = run(capsys, *args, "--dangerous", "--confirm", token)
    assert code == 0 and native["calls"][-1]["method"] == "pcb_create"


# ---- the adapter's per-net result, against a stand-in board ----


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


def unroute(monkeypatch, doc, **params) -> dict:
    monkeypatch.setattr(adapter, "_layout_board_path", lambda params: "demo.pcb")
    monkeypatch.setattr(adapter, "_application", lambda client, attach_only: object())
    monkeypatch.setattr(adapter, "_open_layout_document", lambda app, path: (doc, []))
    monkeypatch.setattr(adapter, "_licensed_document", lambda app: doc)
    monkeypatch.setattr(adapter, "_find_net", lambda doc, name: name)
    monkeypatch.setattr(adapter, "_regenerate_planes", lambda doc: True)
    monkeypatch.setattr(adapter, "_routing_counts", lambda doc: {"traces": 0, "vias": 0})
    return adapter._unroute_nets(params, None)


def test_every_named_net_gets_a_result_in_input_order(tmp_path, monkeypatch) -> None:
    import copy

    from test_unroute_native import native_case

    app, params, _ = native_case(tmp_path, monkeypatch, "partial")
    geo = app.ActiveDocument.state["geometry"]
    for comp in geo["components"]:
        pin = copy.deepcopy(comp["geometry"][0])
        pin.update(number="3", net="C")
        pin["center_mm"][1] = 6
        pin["copper"][0]["geometry"][0]["bounds_mm"][1] += 6
        pin["copper"][0]["geometry"][0]["bounds_mm"][3] += 6
        comp["geometry"].append(pin)
    row = {"net": "C", "uid": "13", "opens": 1, "routed": False}
    geo["model"]["connectivity"]["nets"].append(row)
    geo["model"]["unrouted"].append(copy.deepcopy(row))
    params.update(nets=["B", "A", "C", "B"], continue_on_error=True)
    preview = adapter._unroute_nets(params, None)
    with pytest.raises(adapter.AdapterError) as failed:
        adapter._unroute_nets(
            {**params, "apply": True, "expected": preview["unroute"]["fingerprint"]}, None
        )
    result = failed.value.details["native_result"]
    assert [item["target"] for item in result["items"]] == ["B", "A", "C"]
    blocked, removed, empty = result["items"]
    assert (
        blocked["ok"] is False and blocked["deleted"] == 0 and blocked["error"]["not_deleted"] == 1
    )
    assert removed["ok"] is True and removed["deleted"] == 1
    assert empty == {"target": "C", "traces": 0, "vias": 0, "ok": True, "deleted": 0}
    assert result["summary"] == {"total": 3, "succeeded": 2, "failed": 1}


def test_unroute_all_is_explicitly_outside_current_verified_subset(monkeypatch) -> None:
    monkeypatch.setattr(adapter, "_application", lambda *a, **k: pytest.fail("no native access"))
    with pytest.raises(adapter.AdapterError) as refused:
        adapter._unroute_nets({"all": True}, None)
    assert refused.value.code == "E_UNSUPPORTED"
    assert refused.value.details["write_attempted"] is False
