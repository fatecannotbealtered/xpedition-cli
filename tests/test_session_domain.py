from __future__ import annotations

import pytest

from xpedition_cli.errors import CLIError
from xpedition_cli.main import _session_domain


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("pcb", "pcb"),
        ("layout", "pcb"),
        ("PCB", "pcb"),
        ("schematic", "schematic"),
        ("designer", "schematic"),
        ("  Designer  ", "schematic"),
    ],
)
def test_kind_selects_the_application(kind: str, expected: str) -> None:
    assert _session_domain({"kind": kind}) == expected


def test_layout_is_the_default_when_nothing_hints_otherwise() -> None:
    assert _session_domain({}) == "pcb"


@pytest.mark.parametrize(
    ("project", "expected"),
    [
        (r"C:\work\board.prj", "schematic"),
        (r"C:\work\board.dproj", "schematic"),
        (r"C:\work\board.pcb", "pcb"),
    ],
)
def test_project_suffix_picks_the_application_when_kind_is_absent(
    project: str, expected: str
) -> None:
    assert _session_domain({"project": project}) == expected


def test_explicit_kind_wins_over_the_project_suffix() -> None:
    assert _session_domain({"kind": "pcb", "project": r"C:\work\board.prj"}) == "pcb"


def test_unknown_kind_is_a_validation_error_listing_the_choices() -> None:
    with pytest.raises(CLIError) as caught:
        _session_domain({"kind": "kicad"})
    assert caught.value.code == "E_VALIDATION"
    assert caught.value.details["choices"] == ["designer", "layout", "pcb", "schematic"]
