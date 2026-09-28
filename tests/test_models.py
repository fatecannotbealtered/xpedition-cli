"""A design read back from Xpedition is normalised before any command reads it."""

from __future__ import annotations

import pytest

from xpedition_cli.errors import CLIError
from xpedition_cli.models import normalise_project


def _design(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"project": "demo", "components": [], "nets": []}
    base.update(overrides)
    return base


def test_unnamed_symbols_and_wires_are_counted_not_reported() -> None:
    """A live schematic always holds ground/power/port symbols with no refdes."""
    result = normalise_project(
        _design(
            components=[{"refdes": "R1"}, {"refdes": ""}, {"refdes": None}],
            nets=[{"name": "3V3"}, {"name": ""}],
        )
    )
    assert [c["refdes"] for c in result["components"]] == ["R1"]
    assert [n["name"] for n in result["nets"]] == ["3V3"]
    assert result["metadata"]["unnamed"]["components"] == 2
    assert result["metadata"]["unnamed"]["nets"] == 1


def test_a_strict_read_still_rejects_what_a_design_needs() -> None:
    with pytest.raises(CLIError) as caught:
        normalise_project(_design(components=[{"part_number": "RES-1K"}]), observed=False)
    assert caught.value.code == "E_PROJECT_INVALID"
    with pytest.raises(CLIError) as caught:
        normalise_project(_design(nets=[{"pins": ["R1.1"]}]), observed=False)
    assert caught.value.code == "E_PROJECT_INVALID"


def test_identifiers_become_strings_and_missing_parts_default() -> None:
    result = normalise_project(
        _design(components=[{"refdes": 7, "pins": [{"number": 1, "net": 3}]}], nets=[{"name": 3}])
    )
    component = result["components"][0]
    assert component["refdes"] == "7"
    assert component["pins"][0] == {"number": "1", "net": "3"}
    assert result["pcb"] == {
        "components": [],
        "footprints": [],
        "nets": [],
        "tracks": [],
        "vias": [],
    }
    assert result["connections"] == [] and result["sheets"] == []


@pytest.mark.parametrize("field", ["components", "nets", "connections", "sheets"])
def test_a_snapshot_whose_lists_are_not_lists_is_an_adapter_fault(field) -> None:
    with pytest.raises(CLIError) as caught:
        normalise_project(_design(**{field: {"not": "a list"}}))
    assert caught.value.code == "E_SERVER"


def test_a_snapshot_that_is_not_an_object_is_an_adapter_fault() -> None:
    with pytest.raises(CLIError) as caught:
        normalise_project(["not", "an", "object"])  # type: ignore[arg-type]
    assert caught.value.code == "E_SERVER"
