from __future__ import annotations

import pytest

from xpedition_cli.errors import CLIError
from xpedition_cli.models import normalise_project


def _project(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"project": "demo", "components": [], "nets": []}
    base.update(overrides)
    return base


def test_authored_project_still_rejects_a_component_without_a_refdes() -> None:
    with pytest.raises(CLIError) as caught:
        normalise_project(_project(components=[{"part_number": "RES-1K"}]))
    assert caught.value.code == "E_PROJECT_INVALID"


def test_authored_project_still_rejects_a_net_without_a_name() -> None:
    with pytest.raises(CLIError) as caught:
        normalise_project(_project(nets=[{"pins": ["R1.1"]}]))
    assert caught.value.code == "E_PROJECT_INVALID"


def test_observed_project_keeps_reading_past_unnamed_symbols_and_wires() -> None:
    """A live schematic always holds ground/power/port symbols with no refdes."""
    result = normalise_project(
        _project(
            components=[{"refdes": "R1"}, {"refdes": ""}, {"refdes": None}],
            nets=[{"name": "3V3"}, {"name": ""}],
        ),
        observed=True,
    )
    assert [c["refdes"] for c in result["components"]] == ["R1"]
    assert [n["name"] for n in result["nets"]] == ["3V3"]
    assert result["metadata"]["unnamed"] == {
        "components": 2,
        "nets": 1,
        "note": result["metadata"]["unnamed"]["note"],
    }


def test_observed_project_without_anomalies_reports_no_unnamed_block() -> None:
    result = normalise_project(
        _project(components=[{"refdes": "R1"}], nets=[{"name": "3V3"}]), observed=True
    )
    assert "unnamed" not in result["metadata"]


def test_observed_mode_is_opt_in() -> None:
    """The default stays strict so a malformed authored file is still caught."""
    with pytest.raises(CLIError):
        normalise_project(_project(components=[{"refdes": ""}]))
