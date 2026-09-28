"""schematic render | draw | edit | check | components | nets | sheets | show | export."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fakes import Failure, schematic_snapshot

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "demo-sensor-board.json"


def _design(tmp_path: Path) -> Path:
    path = tmp_path / "design.json"
    path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


# -- render ------------------------------------------------------------------------


def test_render_pictures_each_planned_sheet_without_xpedition(cli, adapter, tmp_path) -> None:
    pytest.importorskip("PIL")
    design = _design(tmp_path)
    out = tmp_path / "preview.png"
    code, payload = cli(
        "schematic", "render", "--design", str(design), "--output", str(out), "--sheets", "1"
    )
    data = payload["data"]
    assert code == 0 and data["summary"]["sheets"] == 1
    assert Path(data["pictures"][0]["path"]).is_file()
    assert adapter.calls == []
    code, payload = cli(
        "schematic", "render", "--design", str(design), "--output", str(out), "--sheets", "1"
    )
    assert code == 6 and payload["error"]["details"]["hint"] == "--replace"
    code, _ = cli(
        "schematic",
        "render",
        "--design",
        str(design),
        "--output",
        str(out),
        "--sheets",
        "1",
        "--replace",
    )
    assert code == 0


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        (["--output", "x.jpg"], 2),
        (["--output", "x.png", "--scale", "9"], 2),
        (["--output", "x.png", "--sheets", "42"], 2),
    ],
)
def test_render_refuses_bad_options(cli, tmp_path, extra, code) -> None:
    design = _design(tmp_path)
    got, payload = cli("schematic", "render", "--design", str(design), *extra)
    assert got == code and payload["ok"] is False


# -- draw --------------------------------------------------------------------------


def test_draw_plans_offline_then_draws_with_both_gates(cli, adapter, project, tmp_path) -> None:
    design = _design(tmp_path)
    adapter.on(
        "draw", {"applied": True, "netlist": {"matches": True}, "sheets_drawn": [1, 2, 3, 4]}
    )
    args = ["schematic", "draw", "--project", str(project), "--design", str(design)]
    code, payload = cli(*args, "--dry-run")
    data = payload["data"]
    assert code == 0 and data["preview"]["dangerous"] is True
    assert data["operations"][0]["index"] == 0 and "sheet" in data["operations"][0]
    assert adapter.calls == []
    token = data["confirm_token"]
    code, payload = cli(*args, "--confirm", token)
    assert code == 5 and "--dangerous" in payload["error"]["message"]
    code, payload = cli(*args, "--confirm", token, "--dangerous")
    assert code == 0 and payload["data"]["netlist"]["matches"] is True
    assert "summary" in payload["data"]
    sent = adapter.last("draw")
    assert sent["project"] == str(project) and sent["design_sheets"] == [1, 2, 3, 4]


def test_draw_resumes_from_the_sheets_named(cli, adapter, project, tmp_path) -> None:
    design = _design(tmp_path)
    adapter.on("draw", {"applied": True})
    args = [
        "schematic",
        "draw",
        "--project",
        str(project),
        "--design",
        str(design),
        "--sheets",
        "3,4",
    ]
    _, payload = cli(*args, "--dry-run")
    preview = payload["data"]["preview"]
    assert [change["sheet"] for change in preview["changes"]] == [3, 4]
    assert preview["sheets_kept"] == [1, 2]
    cli(*args, "--confirm", payload["data"]["confirm_token"], "--dangerous", "--pace", "0.2")
    sent = adapter.last("draw")
    assert {op["number"] for op in sent["ops"] if op["op"] == "open_sheet"} == {3, 4}


def test_a_token_for_other_sheets_is_refused(cli, adapter, project, tmp_path) -> None:
    design = _design(tmp_path)
    base = ["schematic", "draw", "--project", str(project), "--design", str(design)]
    _, payload = cli(*base, "--sheets", "3", "--dry-run")
    code, payload = cli(*base, "--confirm", payload["data"]["confirm_token"], "--dangerous")
    assert code == 6 and adapter.calls == []


def test_an_undrawable_design_is_refused_with_its_reason(cli, project, tmp_path) -> None:
    design = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    # R301 on sheet 4 renamed to R201, which sheet 3 already has
    design["sheets"][3] = json.loads(json.dumps(design["sheets"][3]).replace('"R301"', '"R201"'))
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(design), encoding="utf-8")
    code, payload = cli(
        "schematic", "draw", "--project", str(project), "--design", str(broken), "--dry-run"
    )
    assert code == 2 and "design cannot be drawn" in payload["error"]["message"]


# -- edit --------------------------------------------------------------------------


class _Designer:
    """A schematic that the edit changes, so the read-back sees the change."""

    def __init__(self) -> None:
        self.design = schematic_snapshot()

    def snapshot(self, params):
        return copy.deepcopy(self.design)

    def apply(self, params):
        applied = []
        for op in params["operations"]:
            if op["type"] == "move_component":
                part = next(p for p in self.design["components"] if p["refdes"] == op["refdes"])
                part["x"], part["y"] = op["x"], op["y"]
            elif op["type"] == "place_component":
                self.design["components"].append(
                    {
                        "refdes": op["refdes"],
                        "internal_part_no": op["part_number"],
                        "x": op["x"],
                        "y": op["y"],
                        "pins": [],
                    }
                )
            applied.append({"type": op["type"], "applied": True})
        return {"applied": applied, "count": len(applied), "domain": "schematic", "saved": True}


def _operations(tmp_path: Path, *operations: dict) -> Path:
    path = tmp_path / "changes.json"
    path.write_text(json.dumps({"operations": list(operations)}), encoding="utf-8")
    return path


def test_edit_previews_against_the_design_then_writes_and_verifies(
    cli, adapter, project, tmp_path
) -> None:
    designer = _Designer()
    adapter.on("snapshot", designer.snapshot).on("apply_changeset", designer.apply)
    changes = _operations(
        tmp_path,
        {"type": "move_component", "refdes": "R1", "x": 620, "y": 300, "sheet": 2},
        {
            "type": "place_component",
            "refdes": "R9",
            "library": "PartQuest",
            "part_number": "RES-4K7",
            "x": 800,
            "y": 300,
        },
    )
    args = ["schematic", "edit", "--project", str(project), "--file", str(changes)]
    code, payload = cli(*args, "--dry-run")
    preview = payload["data"]["preview"]
    assert code == 0 and preview["operation_count"] == 2
    assert [change["action"] for change in preview["changes"]] == [
        "move_component",
        "place_component",
    ]
    assert adapter.methods() == ["snapshot"]
    code, payload = cli(*args, "--confirm", payload["data"]["confirm_token"])
    assert code == 0 and payload["data"]["verification"]["valid"] is True
    sent = adapter.last("apply_changeset")
    assert sent["domain"] == "schematic" and sent["save"] is True and len(sent["operations"]) == 2
    assert adapter.last("snapshot")["domain"] == "schematic"


def test_a_change_the_design_does_not_show_afterwards_is_reported(
    cli, adapter, project, tmp_path
) -> None:
    designer = _Designer()
    adapter.on("snapshot", designer.snapshot)
    adapter.on(
        "apply_changeset", {"applied": [{"type": "move_component"}], "saved": True}
    )  # did nothing
    changes = _operations(tmp_path, {"type": "move_component", "refdes": "R1", "x": 1, "y": 2})
    args = ["schematic", "edit", "--project", str(project), "--file", str(changes)]
    _, payload = cli(*args, "--dry-run")
    code, payload = cli(*args, "--confirm", payload["data"]["confirm_token"])
    assert code == 2 and payload["error"]["code"] == "E_PROJECT_INVALID"
    assert payload["error"]["details"]["stage"] == "verify"
    issues = payload["error"]["details"]["verification"]["issues"]
    assert {issue["field"] for issue in issues} == {"x", "y"}


def test_edit_refuses_what_the_design_contradicts_before_writing(
    cli, adapter, project, tmp_path
) -> None:
    for operation, code in (
        ({"type": "move_component", "refdes": "U9", "x": 1, "y": 2}, 3),  # no such part
        ({"type": "connect", "net": "NOPE", "pins": ["R1.1", "U1.2"]}, 3),  # no such net
        ({"type": "connect", "net": "I2C_SDA", "pins": ["R1.2", "U1.3"]}, 6),  # already so
        (
            {
                "type": "place_component",
                "refdes": "R1",
                "library": "L",
                "part_number": "P",
                "x": 1,
                "y": 1,
            },
            6,
        ),  # exists
    ):
        changes = _operations(tmp_path, operation)
        got, payload = cli(
            "schematic", "edit", "--project", str(project), "--file", str(changes), "--dry-run"
        )
        assert got == code, (operation, payload)
    assert "apply_changeset" not in adapter.methods()


@pytest.mark.parametrize(
    "operations",
    [
        [],
        [{"type": "delete_board"}],
        [{"type": "move_component", "refdes": "R1", "x": 1.5, "y": 2}],
        [{"type": "connect", "net": "N", "pins": ["R1.1", "R2.1", "U1.1"]}],
        [{"type": "connect", "net": "N", "pins": ["R1.1", "R2.1"], "points": [[1, 2, 3]]}],
        [{"type": "place_component", "refdes": "R9", "x": 1, "y": 1}],
    ],
)
def test_a_malformed_operations_file_is_refused_before_designer_is_asked(
    cli, adapter, project, tmp_path, operations
) -> None:
    changes = tmp_path / "changes.json"
    changes.write_text(json.dumps({"operations": operations}), encoding="utf-8")
    code, payload = cli(
        "schematic", "edit", "--project", str(project), "--file", str(changes), "--dry-run"
    )
    assert code == 2 and payload["error"]["code"] == "E_CHANGESET_INVALID"
    assert adapter.calls == []


def test_a_design_that_changed_since_the_dry_run_refuses_the_token(
    cli, adapter, project, tmp_path
) -> None:
    designer = _Designer()
    adapter.on("snapshot", designer.snapshot).on("apply_changeset", designer.apply)
    changes = _operations(tmp_path, {"type": "move_component", "refdes": "R1", "x": 620, "y": 300})
    args = ["schematic", "edit", "--project", str(project), "--file", str(changes)]
    _, payload = cli(*args, "--dry-run")
    designer.design["components"][0]["value"] = "changed by hand"
    code, payload = cli(*args, "--confirm", payload["data"]["confirm_token"])
    assert code == 6 and "apply_changeset" not in adapter.methods()


def test_a_failed_read_back_says_the_write_was_attempted(cli, adapter, project, tmp_path) -> None:
    reads = {"count": 0}

    def snapshot(params):
        reads["count"] += 1
        if reads["count"] == 3:  # dry run, confirm, then the read-back
            raise Failure("E_TIMEOUT", "timed out")
        return schematic_snapshot()

    adapter.on("snapshot", snapshot).on("apply_changeset", {"applied": [], "saved": True})
    changes = _operations(tmp_path, {"type": "move_component", "refdes": "R1", "x": 1, "y": 2})
    args = ["schematic", "edit", "--project", str(project), "--file", str(changes)]
    _, payload = cli(*args, "--dry-run")
    code, payload = cli(*args, "--confirm", payload["data"]["confirm_token"])
    assert code == 2 and payload["error"]["details"]["stage"] == "read_back"
    assert payload["error"]["details"]["write_attempted"] is True
    assert payload["error"]["details"]["cause"]["code"] == "E_TIMEOUT"


# -- check and reads ----------------------------------------------------------------


def test_check_merges_this_tool_s_rules_with_designer_s_verification(cli, adapter, project) -> None:
    adapter.on(
        "verify",
        {
            "scheme": "full",
            "findings": [
                {
                    "severity": "medium",
                    "source": "xpedition/verify:dangling",
                    "refdes": "U1",
                    "net": None,
                    "finding": "a dangling wire",
                    "evidence": [],
                    "suggestion": "",
                    "confidence": 1.0,
                }
            ],
            "logs": ["LogFiles/vdrc.log"],
        },
    )
    code, payload = cli("schematic", "check", "--project", str(project))
    data = payload["data"]
    sources = {finding["source"] for finding in data["findings"]}
    assert code == 0 and "xpedition/verify:dangling" in sources
    assert data["designer_verification"] == {
        "scheme": "full",
        "findings": 1,
        "logs": ["LogFiles/vdrc.log"],
    }
    assert adapter.last("verify") == {"project": str(project)}
    # the design is clean by this tool's rules: C1 decouples U1, R1/R2 pull I2C up
    assert all(not finding["source"].startswith("cli/") for finding in data["findings"])
    code, payload = cli("schematic", "check", "--project", str(project), "--limit", "0")
    assert payload["data"]["count"] == 0 and payload["data"]["has_more"] is True


def test_check_finds_what_the_netlist_decides(cli, adapter, project) -> None:
    design = schematic_snapshot()
    design["components"][1]["pins"][1]["net"] = None  # C1.2 open
    design["connections"][1]["pins"] = ["U1.2"]
    adapter.on("snapshot", lambda params: copy.deepcopy(design))
    adapter.on("verify", {"scheme": "full", "findings": [], "logs": []})
    _, payload = cli("schematic", "check", "--project", str(project))
    rules = {finding["source"] for finding in payload["data"]["findings"]}
    assert "cli/open-pin" in rules and "cli/decoupling" in rules


def test_components_nets_and_sheets_page_and_filter(cli, adapter, project) -> None:
    code, payload = cli("schematic", "components", "--project", str(project), "--query", "RES-4K7")
    assert code == 0 and [item["refdes"] for item in payload["data"]["items"]] == ["R1", "R2"]
    code, payload = cli(
        "schematic", "components", "--project", str(project), "--limit", "1", "--offset", "1"
    )
    assert payload["data"]["count"] == 1 and payload["data"]["next_offset"] == 2
    code, payload = cli("schematic", "nets", "--project", str(project))
    nets = {item["name"]: item for item in payload["data"]["items"]}
    assert nets["+3V3"]["kind"] == "power" and nets["GND"]["kind"] == "ground"
    assert nets["I2C_SDA"]["kind"] == "signal" and nets["I2C_SDA"]["pins"] == ["R1.2", "U1.3"]
    code, payload = cli("schematic", "sheets", "--project", str(project))
    assert payload["data"]["count"] == 2
    assert all(call["params"]["domain"] == "schematic" for call in adapter.calls)


def test_a_read_passes_its_timeout_to_the_snapshot(cli, adapter, project) -> None:
    cli("schematic", "sheets", "--project", str(project), "--timeout", "300")
    assert adapter.calls[-1]["timeout"] == 300.0
    cli("schematic", "sheets", "--project", str(project))
    assert adapter.calls[-1]["timeout"] == 120.0
    code, payload = cli("schematic", "sheets", "--project", str(project), "--timeout", "0.5")
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION"


def test_a_read_that_times_out_leaves_the_session_stale_and_says_what_to_do(
    cli, adapter, project, monkeypatch
) -> None:
    import subprocess

    import xpedition_cli.backends.native_xpedition as native_xpedition

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(native_xpedition.subprocess, "run", timeout)
    code, payload = cli("schematic", "components", "--project", str(project), "--timeout", "30")
    assert code == 8 and payload["error"]["code"] == "E_TIMEOUT"
    assert "--timeout above 30" in payload["error"]["details"]["hint"]
    code, payload = cli("schematic", "components", "--project", str(project))
    assert code == 6 and "stale" in payload["error"]["message"]


# -- show and export -------------------------------------------------------------------


def test_show_and_export_pass_their_options_on(cli, adapter, project, tmp_path) -> None:
    adapter.on("show", {"sheet": 2, "foreground": True})
    adapter.on("export_pdf", {"exported": True, "pages": []})
    code, _ = cli(
        "schematic",
        "show",
        "--project",
        str(project),
        "--sheet",
        "2",
        "--output",
        str(tmp_path / "s.png"),
    )
    assert code == 0 and adapter.last("show") == {
        "project": str(project),
        "sheet": 2,
        "output": str(tmp_path / "s.png"),
    }
    code, _ = cli(
        "schematic",
        "export",
        "--project",
        str(project),
        "--output",
        str(tmp_path / "b.pdf"),
        "--color",
        "2",
    )
    assert code == 0 and adapter.last("export_pdf")["color"] == 2
    for argv in (
        ["schematic", "show", "--project", str(project), "--sheet", "0"],
        ["schematic", "show", "--project", str(project), "--output", "x.jpg"],
        ["schematic", "export", "--project", str(project), "--output", "x.png"],
        ["schematic", "export", "--project", str(project), "--color", "7"],
    ):
        code, payload = cli(*argv)
        assert code == 2 and payload["error"]["code"] == "E_VALIDATION", argv
