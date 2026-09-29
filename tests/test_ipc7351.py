"""IPC-7351B land patterns: the sums, the families, and the cells they become."""

from __future__ import annotations

import pytest

from xpedition_cli import ipc7351 as I
from xpedition_cli import library_hkp as H


def _cell(spec: dict, kind: str = "") -> tuple[H.Cell, H.LibraryPlan]:
    plan = H.LibraryPlan()
    return I.footprint(spec, H._Stock(plan), kind=kind), plan


def _pad(plan: H.LibraryPlan, cell: H.Cell, number: str) -> tuple[H.CellPin, H.Pad]:
    pin = next(p for p in cell.pins if p.number == number)
    return pin, plan.pads[plan.padstacks[pin.padstack].pad]


SOIC8 = {
    "family": "gullwing",
    "pins": 8,
    "pitch": 1.27,
    "span": {"nominal": 6.0, "tolerance": 0.2},
    "terminal": [0.4, 1.27],
    "lead_width": [0.31, 0.51],
    "body": [3.9, 4.9],
    "height": 1.75,
}


def test_the_sums_give_the_lands_the_kicad_library_has() -> None:
    # SOIC-8 (JEDEC MS-012): KiCad's lands are 1.95 x 0.6 at x = +-2.475
    g, z, x = I.lands("gullwing", "N", I.Span(5.8, 6.2), I.Span(0.4, 1.27), I.Span(0.31, 0.51))
    assert (g, z, x) == (3.0, 6.9, 0.6)
    assert (z - g) / 2 == pytest.approx(1.95) and (z + g) / 4 == pytest.approx(2.475)
    # TSSOP-20 (MO-153): 1.475 x 0.4 at x = +-2.8625
    g, z, x = I.lands("gullwing", "N", I.Span(6.4, 6.4), I.Span(0.45, 0.75), I.Span(0.17, 0.3))
    assert (z - g) / 2 == pytest.approx(1.475) and (z + g) / 4 == pytest.approx(2.8625)
    assert x == pytest.approx(0.4)


def test_density_levels_grow_the_lands_from_least_to_most() -> None:
    sizes = [
        I.lands("gullwing", level, I.Span(5.8, 6.2), I.Span(0.4, 1.27), I.Span(0.31, 0.51))
        for level in ("L", "N", "M")
    ]
    outer = [z for _g, z, _x in sizes]
    assert outer == sorted(outer) and outer[0] < outer[2]


def test_a_soic_is_numbered_down_the_left_and_up_the_right() -> None:
    cell, plan = _cell(SOIC8)
    assert cell.name == "SOIC127P600X175-8N" and cell.group == "IC_SOIC"
    first, pad = _pad(plan, cell, "1")
    assert (first.x, first.y) == (-2.475, 1.905)
    assert (pad.width, pad.height) == pytest.approx((1.95, 0.6))
    assert pad.shape == "RADIUS_CORNER_RECTANGLE" and pad.radius == 0.15
    last, _ = _pad(plan, cell, "8")
    assert (last.x, last.y) == (2.475, 1.905)
    fourth, _ = _pad(plan, cell, "4")
    assert fourth.y == -1.905


def test_omitted_positions_keep_their_place_and_the_rest_are_renumbered() -> None:
    sot23 = {
        "family": "gullwing",
        "pins": 3,
        "positions": 6,
        "omit": [2, 4, 6],
        "pitch": 0.95,
        "span": [2.1, 2.64],
        "terminal": [0.4, 0.6],
        "lead_width": [0.3, 0.5],
        "body": [1.3, 2.9],
        "height": 1.12,
    }
    cell, _ = _cell(sot23)
    where = {p.number: (p.x < 0, p.y) for p in cell.pins}
    assert where == {"1": (True, 0.95), "2": (True, -0.95), "3": (False, 0.0)}
    with pytest.raises(I.FootprintError, match="positions"):
        _cell({**sot23, "omit": [2]})


def test_a_qfn_runs_counter_clockwise_and_clears_its_exposed_pad() -> None:
    qfn = {
        "family": "nolead",
        "pins": 16,
        "pitch": 0.5,
        "body": [3.0, 3.0],
        "terminal": [0.3, 0.5],
        "lead_width": [0.18, 0.3],
        "exposed_pad": {"size": [1.7, 1.7]},
        "height": 0.9,
    }
    cell, plan = _cell(qfn)
    assert cell.name == "QFN50P300X300X90-17N"
    side = {p.number: p for p in cell.pins}
    assert side["1"].x < 0 and side["1"].y > side["4"].y  # down the left
    assert side["5"].y < 0 and side["5"].x < side["8"].x  # along the bottom
    assert side["9"].x > 0 and side["9"].y < side["12"].y  # up the right
    assert side["13"].y > 0 and side["13"].x > side["16"].x  # back along the top
    exposed, pad = _pad(plan, cell, "17")
    assert (exposed.x, exposed.y) == (0.0, 0.0) and pad.width == 1.7
    stack = plan.padstacks[exposed.padstack]
    paste = plan.pads[stack.paste]
    assert paste.width * paste.height == pytest.approx(0.7 * 1.7 * 1.7, rel=0.01)
    # every terminal land ends 0.2 mm or more from the exposed pad
    for pin in cell.pins:
        if pin.number == "17":
            continue
        _, land = _pad(plan, cell, pin.number)
        half_w, half_h = land.width / 2, land.height / 2
        gap_x = abs(pin.x) - half_w - 0.85
        gap_y = abs(pin.y) - half_h - 0.85
        assert max(gap_x, gap_y) >= 0.2 - 1e-6, pin.number


def test_chips_take_their_table_by_size_and_name_themselves_by_kind() -> None:
    small, plan = _cell({"family": "chip", "size": "0402"}, "C")
    large, plan2 = _cell({"family": "chip", "size": "0603"}, "R")
    assert small.name == "CAPC1005X35N" and large.name == "RESC1608X50N"
    # the small-chip table rounds Z and G to 0.02 mm, the other to 0.05 mm
    length, width, terminal, _height = I.CHIP_SIZES["0402"]
    g, z, _x = I.lands("chip_small", "N", I.Span(*length), I.Span(*terminal), I.Span(*width))
    assert round(g / 0.02, 6) == round(g / 0.02) and round(z / 0.02, 6) == round(z / 0.02)
    length, width, terminal, _height = I.CHIP_SIZES["0603"]
    g, z, _x = I.lands("chip", "N", I.Span(*length), I.Span(*terminal), I.Span(*width))
    assert round(g / 0.05, 6) == round(g / 0.05) and round(z / 0.05, 6) == round(z / 0.05)
    _, pad = _pad(plan, small, "1")
    assert pad.width == pytest.approx(0.55)
    assert [p.number for p in small.pins] == ["1", "2"] and small.pins[0].x < 0


def test_through_hole_holes_and_lands_follow_the_lead() -> None:
    header, plan = _cell(
        {"family": "through", "pins": 4, "pitch": 2.54, "lead": {"square": 0.64}, "height": 8.5}
    )
    first, pad = _pad(plan, header, "1")
    hole = plan.holes[plan.padstacks[first.padstack].hole]
    # the square lead's diagonal is 0.905 mm: 0.2 mm over it, up to 0.05, is 1.15
    assert hole.diameter == pytest.approx(1.15) and pad.width == pytest.approx(1.75)
    assert pad.shape == "RECTANGLE" and header.mount == "THROUGH"
    assert [p.y for p in header.pins] == [3.81, 1.27, -1.27, -3.81]
    dip, _ = _cell(
        {
            "family": "through",
            "pins": 8,
            "rows": 2,
            "pitch": 2.54,
            "row_pitch": 7.62,
            "lead": [0.36, 0.56],
            "height": 5.3,
        }
    )
    where = {p.number: (p.x, p.y) for p in dip.pins}
    assert where["1"] == (-3.81, 3.81) and where["4"] == (-3.81, -3.81)
    assert where["5"] == (3.81, -3.81) and where["8"] == (3.81, 3.81)


def test_lands_given_one_by_one_may_share_a_pin() -> None:
    cell, _ = _cell(
        {
            "pads": [
                {"pin": "1", "x": -1, "y": 0, "width": 0.6, "height": 0.8},
                {"pin": "2", "x": 1, "y": 0.5, "width": 0.6, "height": 0.4},
                {"pin": "2", "x": 1, "y": -0.5, "width": 0.6, "height": 0.4},
            ],
            "height": 0.8,
            "name": "SPLIT",
        }
    )
    assert cell.name == "SPLIT" and [p.number for p in cell.pins] == ["1", "2", "2"]
    with pytest.raises(I.FootprintError, match="drill inside its land"):
        _cell({"pads": [{"pin": "1", "x": 0, "y": 0, "width": 0.6, "drill": 0.8}], "height": 1})


def test_the_silkscreen_stays_off_the_lands_and_the_courtyard_holds_everything() -> None:
    cell, plan = _cell(SOIC8)
    lands = []
    for pin in cell.pins:
        pad = plan.pads[plan.padstacks[pin.padstack].pad]
        lands.append(
            (
                pin.x - pad.width / 2,
                pin.y - pad.height / 2,
                pin.x + pad.width / 2,
                pin.y + pad.height / 2,
            )
        )
    silk = [g for g in cell.graphics if g.block == "SILKSCREEN_OUTLINE"]
    assert silk
    for graphic in silk:
        for x, y in graphic.points:
            for x1, y1, x2, y2 in lands:
                inside_x = x1 - 0.2 < x < x2 + 0.2
                inside_y = y1 - 0.2 < y < y2 + 0.2
                assert not (inside_x and inside_y), (x, y)
    courtyard = next(g for g in cell.graphics if g.block == "PLACEMENT_OUTLINE")
    xs = [x for x, _ in courtyard.points]
    ys = [y for _, y in courtyard.points]
    for graphic in cell.graphics:
        for x, y in graphic.points:
            assert min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys)
    for x1, y1, x2, y2 in lands:
        assert min(xs) <= x1 and x2 <= max(xs) and min(ys) <= y1 and y2 <= max(ys)


def test_bad_specs_say_what_is_wrong() -> None:
    with pytest.raises(I.FootprintError, match="height"):
        _cell({**SOIC8, "height": None})
    with pytest.raises(I.FootprintError, match="pitch"):
        _cell({**SOIC8, "pitch": 0.4})
    with pytest.raises(I.FootprintError, match="family"):
        _cell({"family": "bga"})
    with pytest.raises(I.FootprintError, match="maximum"):
        I.span([2, 1], "span")
    assert I.span({"nominal": 1.0, "tolerance": 0.1}, "x") == I.Span(0.9, 1.1)


USB_LEGS = {
    "name": "USB-LEGS",
    "height": 3.2,
    "pads": [
        {"pin": "A1", "x": -3.0, "y": 0.0, "width": 0.6, "height": 1.0},
        # the shield's legs: plated slots in oblong lands, both on one pin
        {
            "pin": "S1",
            "x": -4.3,
            "y": 1.5,
            "width": 1.0,
            "height": 2.1,
            "shape": "oblong",
            "drill": [0.6, 1.7],
        },
        {
            "pin": "S1",
            "x": 4.3,
            "y": 1.5,
            "width": 1.0,
            "height": 2.1,
            "shape": "oblong",
            "drill": [0.6, 1.7],
        },
    ],
    # the locating pegs: unplated holes that are no pin
    "holes": [{"x": -2.9, "y": 2.0, "drill": 0.65}, {"x": 2.9, "y": 2.0, "drill": 0.65}],
}


def test_lands_take_slots_and_a_cell_takes_unplated_holes_of_its_own() -> None:
    plan = H.LibraryPlan(partition="P")
    stock = H._Stock(plan)
    cell = I.footprint(USB_LEGS, stock, name="USB-LEGS")
    plan.cells[cell.name] = cell
    leg = plan.padstacks[next(p for p in cell.pins if p.number == "S1").padstack]
    assert leg.kind == "PIN_THROUGH" and plan.holes[leg.hole].slot == (0.6, 1.7)
    assert plan.holes[leg.hole].plated and cell.mount == "MIXED"
    assert len(cell.holes) == 2 and {h.x for h in cell.holes} == {-2.9, 2.9}
    peg = plan.padstacks[cell.holes[0].padstack]
    assert peg.kind == "MOUNTING_HOLE" and not plan.holes[peg.hole].plated
    # what the converters are handed reads back the same
    from xpedition_cli import library_read as R

    stacks = R.parse_padstacks(H.render_padstacks(plan))
    slot = stacks["holes"][leg.hole]
    assert (slot["shape"], slot["width"], slot["height"], slot["plated"]) == (
        "SLOT",
        0.6,
        1.7,
        True,
    )
    assert stacks["holes"][peg.hole]["plated"] is False
    read = R.parse_cells(H.render_cells(plan))[0]
    assert sorted(h["x"] for h in read["holes"]) == [-2.9, 2.9]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"drill": [0.6, 2.5]}, "inside its land"),
        ({"drill": [0.6]}, "slot's [width, height]"),
        ({"plated": "yes"}, "plated is true or false"),
    ],
)
def test_a_land_whose_hole_cannot_be_is_refused(change, message) -> None:
    spec = {**USB_LEGS, "pads": [{**USB_LEGS["pads"][1], **change}]}
    with pytest.raises(I.FootprintError, match=message.replace("[", r"\[").replace("]", r"\]")):
        I.footprint(spec, H._Stock(H.LibraryPlan(partition="P")), name="X")


def test_a_hole_needs_a_drill() -> None:
    spec = {**USB_LEGS, "holes": [{"x": 0, "y": 0}]}
    with pytest.raises(I.FootprintError, match="needs a drill"):
        I.footprint(spec, H._Stock(H.LibraryPlan(partition="P")), name="X")
