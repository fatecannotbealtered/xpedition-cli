"""A design collection that cannot be read says which state caused it.

`review run`, `bom export` and `schematic components` all stopped with the same
bare `DesignComponents` "type mismatch" after a redraw. Two ordinary mid-design
states produce it and they have different recoveries -- an unpackaged design
needs `library build --package`, a sheet that was added or removed needs the
project reopened -- and the COM error names neither, so the reporter closed and
reopened the project twice and restarted the session against the wrong one.
"""

from __future__ import annotations

import pytest

from xpedition_cli.native_com_adapter import _designer_collection_error


class App:
    def __init__(self, active: str | None) -> None:
        self._active = active

    def GetActiveDesign(self):  # noqa: N802 - COM spelling
        if self._active is None:
            raise OSError("not available")
        return self._active


TYPE_MISMATCH = Exception(-2147352571, "类型不匹配。", None, 5)


def test_an_unpackaged_design_points_at_library_build() -> None:
    error = _designer_collection_error(App("Board1"), TYPE_MISMATCH, "DesignComponents", "Board1")
    assert "packaged" in error.details["likely_cause"]
    assert "library build --package" in error.details["hint"]


def test_a_sheet_change_points_at_reopening_instead() -> None:
    # After adding or removing a sheet, GetActiveDesign reports the schematic
    # rather than the block; re-packaging does not clear that one.
    error = _designer_collection_error(
        App("Schematic1"), TYPE_MISMATCH, "DesignComponents", "Board1"
    )
    assert "reopened" in error.details["likely_cause"]
    assert "session start" in error.details["hint"]
    assert "library build" not in error.details["hint"]


def test_an_unreadable_active_design_falls_back_to_the_packaging_cause() -> None:
    error = _designer_collection_error(App(None), TYPE_MISMATCH, "DesignComponents", "Board1")
    assert "library build --package" in error.details["hint"]


def test_the_design_and_what_was_active_are_both_reported() -> None:
    error = _designer_collection_error(
        App("Schematic1"), TYPE_MISMATCH, "DesignComponents", "Board1"
    )
    assert error.details["design"] == "Board1"
    assert error.details["active_design"] == "Schematic1"
    assert "active_design" in error.details["_untrusted"]


@pytest.mark.parametrize(
    "exc",
    [
        Exception("Class not registered"),
        Exception(
            -2147352567,
            "发生意外。",
            (0, "Xpedition Designer", "licensing", None, 10279, -2147220947),
            None,
        ),
    ],
)
def test_a_fault_with_its_own_classification_is_left_alone(exc) -> None:
    # Only the unclassified server fault gets this diagnosis bolted on.
    error = _designer_collection_error(App("Board1"), exc, "DesignComponents", "Board1")
    assert error.code != "E_SERVER"
    assert "likely_cause" not in error.details
