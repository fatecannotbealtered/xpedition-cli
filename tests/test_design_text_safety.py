"""Design text never writes outside its file, or records of its own into one.

A design file and a library to import from are inputs an agent may be handed.
Their names and texts end up as file and folder names in the symbol library and as
lines of symbol and HKP files, so a path in a name, or a quote or line break in a
text, is refused.
"""

from __future__ import annotations

import copy

import pytest
from test_schematic_layout import _design

from xpedition_cli import native_com_adapter as adapter
from xpedition_cli import schematic_layout as L


def renamed(design: dict, old: str, new: str) -> dict:
    """The design with symbol `old` defined and used under the name `new`."""
    result = copy.deepcopy(design)
    result["symbols"][new] = result["symbols"].pop(old)
    for sheet in result["sheets"]:
        for block in sheet["blocks"]:
            if block.get("symbol") == old:
                block["symbol"] = new
    return result


@pytest.mark.parametrize("name", ["../../../ESCAPE", "sub/LDO", "C:LDO", "..", "LDO BIG"])
def test_a_symbol_name_that_is_not_a_plain_file_name_is_refused(name) -> None:
    with pytest.raises(L.DesignError, match="not a plain name"):
        L.plan(renamed(_design(), "LDO", name))


@pytest.mark.parametrize("partition", ["../../ESCAPE", "Case/sub", "1Case", ""])
def test_a_symbol_partition_that_is_not_a_plain_identifier_is_refused(partition) -> None:
    design = {**_design(), "partition": partition or " "}
    with pytest.raises(L.DesignError, match="not a plain identifier"):
        L.plan(design)


def test_the_adapter_refuses_a_partition_that_is_a_path(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo.prj"
    project.write_text("", encoding="utf-8")
    monkeypatch.setattr(adapter, "_symbol_library_root", lambda path: tmp_path / "lib")
    params = {
        "project": str(project),
        "ops": [{"op": "noop"}],
        "library": "../../ESCAPE",
        "symbols": {"R": "V 53"},
    }
    with pytest.raises(adapter.AdapterError) as caught:
        adapter._draw(params, None)
    assert caught.value.code == "E_VALIDATION"
    assert not (tmp_path / "ESCAPE").exists()


def test_a_line_break_in_a_pin_name_is_refused() -> None:
    design = _design()
    design["symbols"]["LDO"]["left"][0][1] = "VIN\nL 0 0 6 0 3 0 0 0 INJECTED"
    with pytest.raises(L.DesignError, match="line break"):
        L.plan(design)


def test_the_demo_names_still_plan() -> None:
    assert L.plan(_design()).ops


@pytest.mark.parametrize(
    "partition", ["..\\Evil", "Lib/Parts", "电阻", "Trailing.", " Leading", 'Q"uote']
)
def test_the_adapter_refuses_a_unit_partition_that_is_not_a_plain_name(tmp_path, partition) -> None:
    project = tmp_path / "demo.prj"
    project.write_text('KEY CentralLibrary "Lib.lmc"\n', encoding="utf-8")
    with pytest.raises(adapter.AdapterError) as caught:
        adapter._library_import(
            {"project": str(project), "units": [{"partition": partition, "parts": "x"}]},
            client=None,
        )
    assert caught.value.code == "E_USAGE"


def test_the_adapter_writes_a_symbol_file_only_inside_its_folder(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo.prj"
    project.write_text("", encoding="utf-8")
    monkeypatch.setattr(adapter, "_symbol_library_root", lambda path: tmp_path / "lib")
    params = {"project": str(project), "ops": [{"op": "noop"}], "symbols": {"../../evil": "V 53"}}
    with pytest.raises(adapter.AdapterError) as caught:
        adapter._draw(params, None)
    assert caught.value.code == "E_VALIDATION"
    assert not (tmp_path / "evil.1").exists() and not (tmp_path / "lib" / "evil.1").exists()
