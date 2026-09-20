"""DS-07 checks the area a design may draw in, not just the border.

Three regions inside the border are already taken: the sheet title strip along
the top, the notes band across the lower left, and Designer's title block in the
lower right. Checking only the border passed a sheet with a whole stage drawn
through the notes and capacitors sitting on the title block, reported with
`issues: []`. The planner puts the title and the notes itself, so those two are
computed rather than measured.
"""

from __future__ import annotations

from xpedition_cli.schematic_layout import MARGIN, plan, usable_rectangle


def sheet(parts_y: int, notes: list[str] | None = None) -> dict:
    return {
        "sheet_size": "A4",
        "sheets": [
            {
                "number": 1,
                "title": "One",
                "notes": notes or [],
                "blocks": [
                    {
                        "kind": "ladder",
                        "x": 300,
                        "y": parts_y,
                        "path": [
                            "power:+3V3",
                            {"refdes": "R1", "symbol": "RES", "value": "10k"},
                            "gnd",
                        ],
                    }
                ],
            }
        ],
    }


def ds07(result) -> list[dict]:
    return [issue for issue in result.issues if issue["check"] == "DS-07"]


def test_the_notes_band_is_reserved_and_grows_with_the_notes() -> None:
    bare = usable_rectangle(1169, 827, 0)
    with_notes = usable_rectangle(1169, 827, 4)
    assert with_notes[1] > bare[1], "each note takes another 20 units off the bottom"
    assert with_notes[1] - bare[1] == 80


def test_the_title_strip_is_reserved_at_the_top() -> None:
    x1, y1, x2, y2 = usable_rectangle(1169, 827, 0)
    assert y2 < 827 - MARGIN, "the sheet title and description sit above the drawable area"
    assert (x1, x2) == (MARGIN, 1169 - MARGIN)


def test_a_part_in_the_notes_band_is_reported() -> None:
    # Low enough to land in the band a four-note sheet reserves.
    result = plan(sheet(parts_y=MARGIN + 60, notes=["a", "b", "c", "d"]))
    found = ds07(result)
    assert found, "a part drawn through the notes must not pass"
    assert found[0]["usable"], "the report carries the rectangle it was measured against"


def test_a_part_in_the_open_area_is_not_reported() -> None:
    assert ds07(plan(sheet(parts_y=400))) == []


def test_the_plan_publishes_the_rectangle_per_sheet() -> None:
    summary = plan(sheet(parts_y=400, notes=["one"])).summary()
    assert summary["usable"]["1"] == list(usable_rectangle(1169, 827, 1, "A4"))


def test_the_rectangle_tracks_the_sheet_size() -> None:
    a4 = usable_rectangle(1169, 827, 0)
    a3 = usable_rectangle(1654, 1169, 0)
    assert a3[2] > a4[2] and a3[3] > a4[3]


def test_a_measured_border_tightens_the_computed_rectangle() -> None:
    # A4's title block and frame are measured; the computed rectangle alone is
    # looser than what the border actually leaves.
    computed = usable_rectangle(1169, 827, 0)
    measured = usable_rectangle(1169, 827, 0, "A4")
    assert measured[0] >= computed[0] and measured[1] >= computed[1]
    assert measured[2] <= computed[2] and measured[3] <= computed[3]


def test_an_unmeasured_size_keeps_the_computed_rectangle() -> None:
    assert usable_rectangle(1700, 1100, 0, "B") == usable_rectangle(1700, 1100, 0)
