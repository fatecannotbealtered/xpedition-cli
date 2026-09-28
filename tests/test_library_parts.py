"""Parts files: the symbol, footprint and pin map of each part, and what the library holds."""

from __future__ import annotations

import copy

import pytest
from fakes import LIBRARY_PARTS

from xpedition_cli import library_parts as P
from xpedition_cli import library_read as R
from xpedition_cli import schematic_layout as L


def _spec(*parts: dict, partition: str = "PartQuest") -> dict:
    return {"partition": partition, "parts": [copy.deepcopy(p) for p in parts]}


RES, MCU = LIBRARY_PARTS["parts"]


def _held(spec: dict) -> R.Library:
    """What the library holds after importing `spec`: its texts parsed back."""
    plan = P.plan(spec)
    texts = plan.texts()
    return R.Library(
        parts=R.parse_parts(texts["parts"], spec["partition"]),
        cells=R.parse_cells(texts["cells"], spec["partition"]),
        padstacks=R.parse_padstacks(texts["padstacks"]),
        symbols=[
            R.parse_symbol(text, spec["partition"], name) for name, text in plan.symbols.items()
        ],
    )


def test_a_builtin_symbol_is_the_one_a_design_draws() -> None:
    plan = P.plan(_spec(RES))
    design_symbol = L._Library({}).symbol("RES")
    assert list(plan.symbols) == [design_symbol.name]
    assert plan.parts[0]["symbol"] == f"PartQuest:{design_symbol.name}"


def test_the_texts_parse_back_to_the_parts_with_their_pin_maps() -> None:
    held = _held(_spec(RES, MCU))
    mcu = held.find_parts("MCU-8")[0]
    assert mcu["cell"] == "SOIC127P600X175-8N" and mcu["description"].startswith("micro")
    assert {row["name"]: row["number"] for row in mcu["pins"]}["SWCLK"] == "5"
    resistor = held.find_parts("RES-10K")[0]
    assert resistor["properties"]["Value"] == "10k"
    assert R.check(held) == []


def test_identical_content_is_kept_and_other_content_is_a_replacement() -> None:
    held = _held(_spec(RES, MCU))
    plan = P.plan(_spec(RES), held)
    assert plan.actions["parts"] == [{"number": "RES-10K", "action": "replace"}]
    assert {a["action"] for a in plan.actions["cells"]} == {"keep"}
    assert {a["action"] for a in plan.actions["padstacks"]} == {"keep"}
    assert plan.symbols == {} and plan.texts()["cells"] == ""
    assert plan.replaces == ["part RES-10K"]
    moved = copy.deepcopy(MCU)
    moved["number"] = "MCU-9"
    moved["footprint"]["pitch"] = 1.25
    plan = P.plan(_spec(moved), held)
    cells = {a["name"]: a for a in plan.actions["cells"]}
    assert cells["SOP125P600X175-8N"]["action"] == "add"
    moved["footprint"]["name"] = "SOIC127P600X175-8N"
    plan = P.plan(_spec(moved), held)
    replaced = next(a for a in plan.actions["cells"] if a["name"] == "SOIC127P600X175-8N")
    assert replaced["action"] == "replace" and replaced["used_by"] == ["MCU-8"]
    assert "cell SOIC127P600X175-8N" in plan.replaces


def test_a_placeholder_from_library_build_is_marked_as_such() -> None:
    held = _held(_spec(RES))
    held.parts[0]["description"] = "RES 10k (placeholder part)"
    plan = P.plan(_spec(RES), held)
    assert plan.actions["parts"][0]["action"] == "replace_placeholder"


def test_the_symbol_and_cell_pins_must_match_unless_a_pinmap_says_how() -> None:
    fet = {
        "number": "FET-1",
        "prefix": "Q",
        "symbol": {"kind": "NMOS"},
        "footprint": {
            "pads": [
                {"pin": "G", "x": -1, "y": 0, "width": 0.6},
                {"pin": "S", "x": 1, "y": -0.5, "width": 0.6},
                {"pin": "D", "x": 1, "y": 0.5, "width": 0.6},
            ],
            "height": 1,
        },
    }
    with pytest.raises(P.PartsFileError, match="do not match"):
        P.plan(_spec(fet))
    fet["pinmap"] = {"1": "G", "2": "S", "3": "D"}
    plan = P.plan(_spec(fet))
    assert {row["name"]: row["cell_pin"] for row in plan.parts[0]["pins"]} == {
        "G": "G",
        "S": "S",
        "D": "D",
    }
    fet["pinmap"] = {"9": "G"}
    with pytest.raises(P.PartsFileError, match="pinmap names symbol pins"):
        P.plan(_spec(fet))


def test_value_must_be_one_the_library_keeps() -> None:
    plan = P.plan(_spec({**RES, "value": "100nF"}))
    assert plan.library.parts["RES-10K"].properties["Value"] == "100n"
    with pytest.raises(P.PartsFileError, match="stored as 0"):
        P.plan(_spec({**RES, "value": "10k 1%"}))
    plan = P.plan(_spec({**MCU}))
    assert "Value" not in plan.library.parts["MCU-8"].properties


@pytest.mark.parametrize(
    "change, message",
    [
        ({"number": ""}, "number is the part number"),
        ({"prefix": "U1"}, "prefix is the reference designator"),
        ({"symbol": {"kind": "SPRING"}}, "symbol kind"),
        ({"symbol": {"kind": "HOLE"}}, "no pins"),
        ({"description": 'say "hi"'}, "double quote"),
        ({"footprint": {"family": "chip", "size": "9999"}}, "size is one of"),
    ],
)
def test_bad_parts_are_refused_with_the_reason(change: dict, message: str) -> None:
    with pytest.raises(P.PartsFileError, match=message):
        P.plan(_spec({**RES, **change}))


def test_the_file_itself_is_checked() -> None:
    with pytest.raises(P.PartsFileError, match="non-empty"):
        P.validate({"parts": []})
    with pytest.raises(P.PartsFileError, match="repeat"):
        P.validate({"parts": [RES, RES]})
    with pytest.raises(P.PartsFileError, match="plain identifier"):
        P.validate({"partition": "My Parts", "parts": [RES]})
    with pytest.raises(P.PartsFileError, match="in partition PartQuest already"):
        P.plan(_spec(RES, partition="Other"), _held(_spec(RES)))


def test_a_cell_the_library_holds_is_used_as_it_is() -> None:
    held = _held(_spec(MCU))
    other = {**MCU, "number": "MCU-8B", "footprint": {"cell": "SOIC127P600X175-8N"}}
    plan = P.plan(_spec(other, partition="More"), held)
    assert plan.actions["cells"] == [
        {"name": "SOIC127P600X175-8N", "action": "use", "partition": "PartQuest"}
    ]
    assert plan.library.cell_partitions == {"PartQuest"} and plan.texts()["cells"] == ""
    with pytest.raises(P.PartsFileError, match="no cell"):
        P.plan(_spec({**other, "footprint": {"cell": "NOPE"}}), held)


def test_views_are_what_the_import_would_read() -> None:
    views = P.views(P.plan(_spec(MCU)))
    view = views[0]
    assert view["number"] == "MCU-8" and len(view["cell"]["pins"]) == 8
    assert view["symbol"]["pins"][0]["name"] == "VDD"
    stack = view["cell"]["pins"][0]["padstack"]
    assert R.padstack_geometry(view["padstacks"], stack)["pad"]["width"] == 1.95


def test_the_input_schema_describes_the_file() -> None:
    schema = P.input_schema()
    item = schema["properties"]["parts"]["items"]
    assert item["required"] == ["number", "prefix", "symbol", "footprint"]
    families = item["properties"]["footprint"]["oneOf"][0]["properties"]["family"]["enum"]
    assert "gullwing" in families and "nolead" in families
