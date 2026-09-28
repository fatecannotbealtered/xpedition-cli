"""The checks an agent relies on to call a step good must not pass what is not.

Each of these used to answer "fine" about a result that was not: a design whose two
sheets both had an R201, a drawn schematic carrying a net the plan never asked for,
a library build with parts but no cells for them, and native reads that answered
from empty defaults. The export package's own checks are in test_fab_package.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xpedition_cli import main as cli
from xpedition_cli import native_com_adapter as adapter
from xpedition_cli import schematic_layout

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "demo-sensor-board.json"


def _design() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


# -- the plan -------------------------------------------------------------------------


def test_a_reference_designator_used_on_two_sheets_is_refused() -> None:
    design = _design()
    # R301 on sheet 4 renamed to R201, which sheet 3 already has: Designer numbers one
    # design, and the expected netlist put R201.2 on two nets at once
    design["sheets"][3] = json.loads(json.dumps(design["sheets"][3]).replace('"R301"', '"R201"'))
    with pytest.raises(schematic_layout.DesignError, match="R201.*sheet 3 and sheet 4"):
        schematic_layout.plan(design)


def test_the_plan_tells_the_read_back_its_parts_and_no_connect_pins() -> None:
    params = schematic_layout.plan_to_params(_design(), "X.prj")
    verify = params["verify"]
    assert len(verify["parts"]) == 30 and verify["parts"] == sorted(verify["parts"])
    assert verify["no_connect"] == ["U101.4"]
    parts = {part["refdes"]: part["sheet"] for part in schematic_layout.plan(_design()).parts}
    assert parts["J101"] == 2 and parts["R201"] == 3


# -- the read-back after a draw -------------------------------------------------------

VERIFY = {
    "nets": {"VCC": ["R1.1", "U1.1"], "GND": ["R2.2", "U1.2"]},
    "links": [["R1.2", "R2.1"]],
    "parts": ["R1", "R2", "U1"],
    "no_connect": ["U1.3"],
}


def _snapshot(*extra_components: dict) -> dict:
    return {
        "components": [
            {"refdes": "R1", "pins": [{"number": "1", "net": "VCC"}, {"number": "2"}]},
            {"refdes": "R2", "pins": [{"number": "1"}, {"number": "2", "net": "GND"}]},
            {
                "refdes": "U1",
                "pins": [
                    {"number": "1", "net": "VCC"},
                    {"number": "2", "net": "GND"},
                    {"number": "3"},
                ],
            },
            *extra_components,
        ]
    }


# R1.2 and R2.1 meet in an unnamed junction the plan asked for (a `none` ladder node)
JUNCTION = {"R1.2": "$1N7", "R2.1": "$1N7"}


def test_a_drawing_that_is_exactly_the_plan_matches() -> None:
    result = adapter._compare_netlist(_snapshot(), VERIFY, JUNCTION)
    assert result["matches"] is True
    assert result["extra_nets"] == [] and result["unplanned_components"] == []
    assert result["no_connects_joined"] == []


def test_wiring_the_plan_never_asked_for_does_not_match() -> None:
    # a template's leftover part and wire, joined to a pin the plan marked no-connect:
    # every planned net still reads back exactly, which used to be all that was checked
    leftover = {"refdes": "J9", "pins": [{"number": "1", "net": "OLD_NET"}]}
    identities = {**JUNCTION, "U1.3": "OLD_NET", "J9.1": "OLD_NET"}
    result = adapter._compare_netlist(_snapshot(leftover), VERIFY, identities)
    assert result["differences"] == [] and result["links_broken"] == []
    assert result["matches"] is False
    assert result["extra_nets"] == [{"net": "OLD_NET", "pins": ["J9.1", "U1.3"]}]
    assert result["unplanned_components"] == ["J9"]
    assert result["no_connects_joined"] == [
        {"pin": "U1.3", "net": "OLD_NET", "pins": ["J9.1", "U1.3"]}
    ]


def test_a_planned_part_wired_into_an_unnamed_net_does_not_match() -> None:
    identities = {"R1.2": "$1N7", "R2.1": "$1N7", "U1.1": "$1N7"}
    result = adapter._compare_netlist(_snapshot(), VERIFY, identities)
    assert result["matches"] is False
    assert result["extra_nets"] == [{"net": "$1N7", "pins": ["R1.2", "R2.1", "U1.1"]}]


def test_a_request_from_before_parts_were_sent_still_compares() -> None:
    older = {"nets": VERIFY["nets"], "links": VERIFY["links"]}
    leftover = {"refdes": "J9", "pins": [{"number": "1", "net": "OLD_NET"}]}
    result = adapter._compare_netlist(_snapshot(leftover), older, JUNCTION)
    # without the plan's part list nothing is called unplanned
    assert result["matches"] is True and result["unplanned_components"] == []


# -- the library build ----------------------------------------------------------------


def test_a_library_build_whose_parts_have_no_cells_has_not_succeeded(tmp_path, monkeypatch):
    project = tmp_path / "Demo.prj"
    project.write_text("", encoding="utf-8")
    root = tmp_path / "Library"
    root.mkdir()
    lmc = root / "Library.lmc"
    lmc.write_text("", encoding="utf-8")

    def no_designer(client, attach_only=False):
        raise adapter.AdapterError("E_BACKEND_UNAVAILABLE", "Designer is not running")

    monkeypatch.setattr(adapter, "_central_library", lambda path: (lmc, root))
    monkeypatch.setattr(adapter, "_viewdraw_application", no_designer)
    monkeypatch.setattr(
        adapter, "_run_library_tool", lambda tool, arguments: {"exit_code": 0, "dialogs": []}
    )
    monkeypatch.setattr(adapter, "_tool_log", lambda path: {"errors": []})
    monkeypatch.setattr(adapter, "_ensure_prj_pdb", lambda path, entry: True)
    monkeypatch.setattr(adapter, "_sync_prj_cells", lambda path, library_root: [])
    monkeypatch.setattr(
        adapter,
        "_register_cell_partitions",
        lambda path, library_root, wanted: ([], ["Package_SO"]),
    )
    result = adapter._library_import(
        {
            "project": str(project),
            "partition": "PartQuest",
            "padstacks": "padstacks",
            "cells": "cells",
            "parts": "parts",
            "cell_partitions": ["Package_SO"],
        },
        None,
    )
    # every converter step passed; the parts still have no footprint to package with
    assert result["failed"] == [] and result["cells_missing"] == ["Package_SO"]
    assert result["ok"] is False and "library kicad-import" in result["hint"]


# -- native reads the snapshot cannot make --------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ["pcb", "layers"],
        ["pcb", "stackup"],
        ["pcb", "zones"],
        ["pcb", "keepouts"],
        ["library", "parts"],
        ["library"],
        ["constraints", "list"],
        ["analysis", "results"],
        ["analysis", "drc"],
        ["manufacturing", "artifacts"],
        ["manufacturing", "verify"],
    ],
)
def test_a_native_read_the_snapshot_cannot_make_is_refused_not_empty(
    tmp_path, monkeypatch, capsys, command
):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))

    def unexpected(*args, **kwargs):
        raise AssertionError("the refusal comes before any project is read")

    monkeypatch.setattr(cli, "_backend", unexpected)
    code = cli.main(
        [*command, "--backend", "native_xpedition", "--project", str(tmp_path / "B.prj")]
    )
    result = json.loads(capsys.readouterr().out)
    assert code == 4 and result["error"]["code"] == "E_BACKEND_UNAVAILABLE"
    assert result["error"]["details"]["supported_backends"] == ["mock"]
    assert result["error"]["details"]["hint"]


def test_native_reads_the_snapshot_does_make_are_not_refused() -> None:
    for command in (["pcb", "components"], ["bom", "export"], ["manufacturing", "bom"]):
        positionals, options = cli.parse_argv([*command, "--backend", "native_xpedition"])
        assert positionals == command and options["backend"] == "native_xpedition"


def test_native_board_info_says_what_it_did_not_read(monkeypatch) -> None:
    from xpedition_cli.models import normalise_project

    board = normalise_project(
        {
            "project": "Board",
            "pcb": {"components": [{"refdes": "R1"}], "nets": [{"name": "GND"}]},
            "metadata": {"backend": "native_xpedition", "layer_count": 4},
        },
        observed=True,
    )

    class FakeNative:
        name = "native_xpedition"

        def load(self, path, domain=None):
            return board, None

    monkeypatch.setattr(cli, "_backend", lambda options: FakeNative())
    info = cli.dispatch(["pcb", "info"], {"backend": "native_xpedition", "project": "B.pcb"})
    assert info["component_count"] == 1 and info["layer_count"] == 4
    # unknown, not zero: a board with keep-outs used to report none
    assert info["zone_count"] is None and info["keepout_count"] is None
    assert info["not_read"] == ["zones", "keepouts", "stackup"]
