from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan import board_layout as L

BOARD = (0.0, 0.0, 60.0, 45.0)  # the spacing keeps room for every label
SIZES = {
    "0402": (1.6, 0.55),
    "SOT23": (2.9, 3.0),
    "TSSOP10": (6.1, 6.5),
    "SOIC8": (6.0, 6.2),
    "HDR4": (10.16, 2.54),
    "TP": (1.2, 1.2),
}


def _part(refdes: str, cell: str) -> L.Part:
    width, height = SIZES[cell]
    return L.Part(refdes, width, height, f"CLI_{cell}")


def _net(name: str, pins: list[str], power: bool = False) -> L.Net:
    return L.Net(name, [tuple(pin.split(".")) for pin in pins], power)  # type: ignore[arg-type]


def _board_parts() -> tuple[list[L.Part], list[L.Net]]:
    parts = [
        _part("U302", "TSSOP10"),
        _part("U401", "SOIC8"),
        _part("R301", "0402"),
        _part("R302", "0402"),
        _part("C302", "0402"),
        _part("C401", "0402"),
        _part("R401", "0402"),
        _part("Q401", "SOT23"),
        _part("J301", "HDR4"),
        _part("J401", "HDR4"),
        _part("TP401", "TP"),
        _part("TP402", "TP"),
        _part("R5", "0402"),
    ]
    nets = [
        _net("GND", ["U302.10", "U401.6", "C302.2", "C401.2", "J301.4", "TP402.1"], True),
        _net("+3V3", ["U302.1", "U401.8", "C302.1", "C401.1", "TP401.1"], True),
        _net("RST_N", ["R301.2", "U302.3"]),
        _net("LED", ["R302.1", "U302.8"]),
        _net("V_TH", ["R401.2", "U401.2"]),
        _net("CHG", ["Q401.1", "U401.1"]),
        _net("SDA_HOST", ["J301.1", "R301.1"]),
        _net("SW", ["J401.1", "Q401.3"]),
        _net("X", ["R5.1", "U401.4"]),
    ]
    return parts, nets


def _pinned(refdes: str, cell: str, pins: dict[str, tuple[float, float]]) -> L.Part:
    part = _part(refdes, cell)
    part.pins = pins
    return part


class BoardLayoutTest(unittest.TestCase):
    def test_parts_cluster_around_the_ic_they_connect_to(self):
        parts, nets = _board_parts()
        plan = L.arrange(parts, BOARD, nets, {"3": "SENSE", "4": "POWER"})
        self.assertEqual(plan.clusters["U302"], ["U302", "J301", "R301", "R302", "C302"])
        self.assertEqual(plan.clusters["U401"], ["U401", "Q401", "J401", "R5", "R401", "C401", "TP401", "TP402"])
        by_refdes = {item.refdes: item for item in plan.placements}
        self.assertEqual(len(by_refdes), len(parts))
        # a decoupling capacitor sits next to its IC, closer than a signal resistor
        u302 = by_refdes["U302"]
        dist = lambda a: ((a.x - u302.x) ** 2 + (a.y - u302.y) ** 2) ** 0.5  # noqa: E731
        self.assertLessEqual(dist(by_refdes["C302"]), dist(by_refdes["R301"]) + 1e-6)  # decoupling first
        self.assertTrue(all(item.inside for item in plan.placements), msg=plan.summary()["outside"])
        self.assertEqual(plan.summary()["labels"], ["SENSE", "POWER"])
        self.assertEqual([label.group for label in plan.labels], ["3", "4"])

    def test_connectors_stand_on_the_side_edges_and_test_points_along_the_bottom(self):
        parts, nets = _board_parts()
        plan = L.arrange(parts, BOARD, nets)
        by_refdes = {item.refdes: item for item in plan.placements}
        for refdes in ("J301", "J401"):
            item = by_refdes[refdes]
            self.assertEqual(item.role, "connector")
            self.assertIn(item.rotation, (90.0, 270.0))
            self.assertEqual(item.width, SIZES["HDR4"][1])
            self.assertEqual(item.height, SIZES["HDR4"][0])
            self.assertGreaterEqual(item.x - item.width / 2, L.MARGIN - 1e-6)
            self.assertTrue(item.x - item.width / 2 < L.MARGIN + 1e-6
                or item.x + item.width / 2 > 60 - L.MARGIN - 1e-6)
        for refdes in ("TP401", "TP402"):
            item = by_refdes[refdes]
            self.assertEqual(item.role, "testpoint")
            self.assertLess(abs(item.y - (L.MARGIN + item.height / 2)), 1e-6)
        self.assertLess(by_refdes["TP401"].x, by_refdes["TP402"].x)
        # no two parts overlap
        boxes = [
            (p.x - p.width / 2, p.y - p.height / 2, p.x + p.width / 2, p.y + p.height / 2, p.refdes)
            for p in plan.placements
        ]
        for index, first in enumerate(boxes):
            for second in boxes[index + 1 :]:
                overlap = (
                    first[0] < second[2] - 1e-6
                    and second[0] < first[2] - 1e-6
                    and first[1] < second[3] - 1e-6
                    and second[1] < first[3] - 1e-6
                )
                self.assertFalse(overlap, msg=(first[4], second[4]))

    def test_plan_without_ics_and_overflow_are_reported(self):
        loose = [_part("R201", "0402"), _part("R202", "0402")]
        plan = L.arrange(loose, BOARD, [])
        self.assertEqual(sorted(plan.clusters), ["sheet 2"])
        self.assertEqual(len(plan.placements), 2)
        self.assertEqual(plan.summary()["labels"], ["SHEET 2"])
        tall = [L.Part(f"U3{index:02d}", 20.0, 20.0) for index in range(1, 7)]
        crowded = L.arrange(tall, BOARD, [])
        self.assertTrue(crowded.summary()["outside"])
        self.assertEqual(len(crowded.digest()), 16)
        self.assertEqual(L.arrange(tall, BOARD, []).digest(), crowded.digest())
        self.assertEqual(L.group_of("C201"), "2")
        self.assertEqual(L.role_of("BT201"), "connector")
        self.assertEqual(L.role_of("H401"), "testpoint")
        self.assertEqual(L.role_of("U1"), "anchor")

    def test_parts_keep_room_for_their_labels_share_a_pitch_and_snap_to_the_grid(self):
        parts, nets = _board_parts()
        plan = L.arrange(parts, BOARD, nets, grid=0.5)
        by_refdes = {item.refdes: item for item in plan.placements}
        # the footprint's own size is reported, the label room stays internal
        self.assertEqual((by_refdes["R301"].width, by_refdes["R301"].height), SIZES["0402"])
        for item in plan.placements:
            self.assertLess(abs(item.x / 0.5 - round(item.x / 0.5)), 1e-6, msg=item)
            self.assertLess(abs(item.y / 0.5 - round(item.y / 0.5)), 1e-6, msg=item)
        # connectors still stand on the margin, turned so their label faces inward
        for refdes in ("J301", "J401"):
            item = by_refdes[refdes]
            self.assertGreaterEqual(item.x - item.width / 2, L.MARGIN - 1e-6)
            self.assertTrue(item.x - item.width / 2 < L.MARGIN + 0.5
                or item.x + item.width / 2 > 60 - L.MARGIN - 0.5)
        self.assertTrue(by_refdes["J301"].rotation == 270.0 or by_refdes["J301"].rotation == 90.0)
        # a column beside an IC keeps one pitch
        anchor = by_refdes["U302"]
        column = sorted(
            (p for p in plan.placements if p.cluster == "U302" and p.role == "part" and p.x > anchor.x),
            key=lambda p: -p.y,
        )
        if len(column) >= 3:
            steps = {round(a.y - b.y, 3) for a, b in zip(column, column[1:], strict=False)}
            self.assertEqual(len(steps), 1, msg=steps)
        # nothing sits in the label room above another part of the same cluster
        for first in plan.placements:
            for second in plan.placements:
                if first is second or first.cluster != second.cluster:
                    continue
                x_overlap = (
                    first.x - first.width / 2 < second.x + second.width / 2 - 1e-6
                    and second.x - second.width / 2 < first.x + first.width / 2 - 1e-6
                )
                if x_overlap and second.y > first.y:
                    clearance = (second.y - second.height / 2) - (first.y + first.height / 2)
                    self.assertGreaterEqual(clearance, L.TEXT_ALLOWANCE - 1e-6, msg=(
                        first.refdes,
                        second.refdes,
                        clearance,
                    ))

    def test_corner_keepout_moves_connectors_and_test_points_away_from_the_corners(self):
        parts, nets = _board_parts()
        plan = L.arrange(parts, BOARD, nets, grid=0.5, corner_keepout=7.0)
        for item in plan.placements:
            if item.role == "connector":
                self.assertLessEqual(item.y + item.height / 2, 45 - 7.0 + 1e-6)
                self.assertGreaterEqual(item.y - item.height / 2, 7.0 - 1e-6)
            if item.role == "testpoint":
                self.assertGreaterEqual(item.x - item.width / 2, 7.0 - 1e-6)
                self.assertLessEqual(item.x + item.width / 2, 60 - 7.0 + 1e-6)

    def test_satellites_stand_beside_the_pin_they_connect_to_and_face_it(self):
        # a 10-pin IC: pins 1-5 down the left edge, 6-10 up the right edge
        ic_pins = {str(n): (-2.8, 2.0 - (n - 1) * 1.0) for n in range(1, 6)}
        ic_pins.update({str(n): (2.8, -2.0 + (n - 6) * 1.0) for n in range(6, 11)})
        chip = {"1": (-0.5, 0.0), "2": (0.5, 0.0)}
        parts = [
            _pinned("U302", "TSSOP10", ic_pins),
            _pinned("R301", "0402", chip),
            _pinned("R302", "0402", chip),
            _pinned("C302", "0402", chip),
            _pinned("R303", "0402", chip),
        ]
        nets = [
            _net("GND", ["U302.5", "C302.2"], True),
            _net("+3V3", ["U302.1", "C302.1"], True),
            _net("RST_N", ["R301.1", "U302.2"]),
            _net("LED", ["R302.1", "U302.9"]),
            _net("SCL", ["R303.1", "U302.7"]),
        ]
        plan = L.arrange(parts, BOARD, nets, grid=0.5)
        by_refdes = {item.refdes: item for item in plan.placements}
        ic = by_refdes["U302"]
        # R301 connects to pin 2 on the left edge: it stands left of the IC, level with it
        r301 = by_refdes["R301"]
        self.assertLess(r301.x, ic.x - 3.0)
        self.assertLess(abs(r301.y - (ic.y + 1.0)), 1.5)
        # its pin 1 (at -x) must face the IC on its right, so the part is turned around
        self.assertEqual(r301.rotation, 180.0)
        # R302 and R303 connect to right-edge pins: right of the IC, R302 above R303
        r302, r303 = by_refdes["R302"], by_refdes["R303"]
        self.assertGreater(r302.x, ic.x + 3.0)
        self.assertGreater(r303.x, ic.x + 3.0)
        self.assertGreater(r302.y, r303.y)
        self.assertEqual(r302.rotation, 0.0)  # pin 1 already faces left
        # the decoupling capacitor sits at the supply pin (left edge, pin 1)
        c302 = by_refdes["C302"]
        self.assertLess(c302.x, ic.x)
        self.assertGreater(c302.y, r301.y)
        # nothing overlaps
        items = [i for i in plan.placements]
        for first in items:
            for second in items:
                if first is second:
                    continue
                gap_x = abs(first.x - second.x) - (first.width + second.width) / 2
                gap_y = abs(first.y - second.y) - (first.height + second.height) / 2
                self.assertTrue(gap_x > -1e-6 or gap_y > -1e-6, msg=(first.refdes, second.refdes))

    def test_pack_keeps_order_and_spacing_and_centres_on_targets(self):
        packed = L._pack([("a", 0.0, 2.0), ("b", 0.2, 2.0), ("c", 10.0, 2.0)], gap=1.0, grid=0.5)
        self.assertTrue(packed["a"] < packed["b"] < packed["c"])
        self.assertGreaterEqual(packed["b"] - packed["a"], 3.0 - 1e-9)
        self.assertLess(abs(packed["c"] - 10.0), 1.5)  # the far part stays near its target
        for value in packed.values():
            self.assertLess(abs(value / 0.5 - round(value / 0.5)), 1e-9)

    def test_turn_and_facing_rotation(self):
        self.assertEqual(L._turn((1.0, 0.0), 90), (0.0, 1.0))
        self.assertEqual(L._turn((1.0, 0.0), 180), (-1.0, 0.0))
        chip = L.Part("R1", 1.6, 0.55, pins={"1": (-0.5, 0.0), "2": (0.5, 0.0)})
        self.assertEqual(L._facing_rotation(chip, "1", "right"), 0.0)  # pin 1 at -x faces the IC on the left
        self.assertEqual(L._facing_rotation(chip, "2", "right"), 180.0)
        self.assertEqual(L._facing_rotation(chip, "1", "top"), 90.0)  # pin 1 turned to face down
        self.assertEqual(L._facing_rotation(chip, "2", "top"), 270.0)
        three = L.Part("Q1", 2.9, 3.0, pins={"1": (-0.95, -1.0), "2": (0.95, -1.0), "3": (0.0, 1.0)})
        self.assertEqual(L._facing_rotation(three, "3", "bottom"), 0.0)  # more than two pins: not turned

    def test_connectors_move_to_the_other_edge_when_one_edge_is_full(self):
        parts, nets = _board_parts()
        # four connectors of 10 mm on a 40 mm board with 7 mm corner keepouts: two per edge
        parts += [_part("J302", "HDR4"), _part("J303", "HDR4")]
        nets += [
            _net("A", ["J302.1", "U302.4"]),
            _net("B", ["J303.1", "U302.5"]),
        ]
        plan = L.arrange(parts, (0.0, 0.0, 60.0, 40.0), nets, grid=0.5, corner_keepout=7.0)
        connectors = [item for item in plan.placements if item.role == "connector"]
        self.assertTrue(all(item.inside for item in connectors), msg=[c.refdes for c in connectors if not c.inside])
        left = [c for c in connectors if c.x < 30]
        right = [c for c in connectors if c.x >= 30]
        self.assertTrue(left)
        self.assertTrue(right)
        for column in (left, right):
            column.sort(key=lambda c: -c.y)
            for upper, lower in zip(column, column[1:], strict=False):
                self.assertGreaterEqual(upper.y - upper.height / 2, lower.y + lower.height / 2 - 1e-6)

    def test_rows_step_past_a_column_that_reaches_the_corner(self):
        ic_pins = {"1": (-2.8, 0.0), "2": (2.8, 0.0), "3": (0.0, 3.0)}
        chip = {"1": (-0.5, 0.0), "2": (0.5, 0.0)}
        parts = [_pinned("U302", "TSSOP10", ic_pins)]
        nets = []
        # five parts on the right column overhang the IC; one part above must clear them
        for index in range(1, 6):
            parts.append(_pinned(f"R30{index}", "0402", chip))
            nets.append(_net(f"N{index}", [f"R30{index}.1", "U302.2"]))
        parts.append(_pinned("C301", "0402", chip))
        nets.append(_net("TOP", ["C301.1", "U302.3"]))
        plan = L.arrange(parts, (0.0, 0.0, 80.0, 60.0), nets, grid=0.5)
        items = {item.refdes: item for item in plan.placements}
        for first in items.values():
            for second in items.values():
                if first is second:
                    continue
                gap_x = abs(first.x - second.x) - (first.width + second.width) / 2
                gap_y = abs(first.y - second.y) - (first.height + second.height) / 2
                self.assertTrue(gap_x > -1e-6 or gap_y > -1e-6, msg=(first.refdes, second.refdes))
        self.assertGreater(items["C301"].y, items["U302"].y)

    def test_pin_side_follows_the_column_the_pin_stands_in(self):
        # a wide SOIC: the corner pins are nearer the top and bottom edges than the sides
        pins = {str(n): (-2.66, 2.0 - (n - 1)) for n in range(1, 6)}
        pins.update({str(n): (2.66, -2.0 + (n - 6)) for n in range(6, 11)})
        soic = L.Part("U1", 7.4, 5.4, pins=pins)
        self.assertEqual(L._pin_side(soic, "5"), ("left", -2.0))
        self.assertEqual(L._pin_side(soic, "6"), ("right", -2.0))
        self.assertEqual(L._pin_side(soic, "10"), ("right", 2.0))
        # a part with pins on four sides
        qfn = L.Part(
            "U2",
            4.0,
            4.0,
            pins={
                "1": (-1.5, 0.5),
                "2": (-1.5, -0.5),
                "3": (-0.5, -1.5),
                "4": (0.5, -1.5),
                "5": (1.5, -0.5),
                "6": (1.5, 0.5),
                "7": (0.5, 1.5),
                "8": (-0.5, 1.5),
            },
        )
        self.assertEqual(L._pin_side(qfn, "3"), ("bottom", -0.5))
        self.assertEqual(L._pin_side(qfn, "7"), ("top", 0.5))

    def test_place_labels_puts_designators_beside_their_parts_without_overlaps(self):
        labels = [
            {
                "refdes": "R1",
                "width": 2.0,
                "height": 1.0,
                "part": [10.0, 10.0, 13.0, 11.4],
                "x": 11.5,
                "y": 10.7,
            },
            {
                "refdes": "R2",
                "width": 2.0,
                "height": 1.0,
                "part": [10.0, 12.0, 13.0, 13.4],
                "x": 11.5,
                "y": 12.7,
            },
            {
                "refdes": "U1",
                "width": 2.0,
                "height": 1.0,
                "part": [20.0, 10.0, 26.0, 16.0],
                "x": 23.0,
                "y": 13.0,
            },
        ]
        obstacles = [(9.0, 15.0, 15.0, 17.0)]  # something above the resistor stack
        placed = L.place_labels(labels, obstacles, (0.0, 0.0, 40.0, 30.0), gap=0.3)
        by = {item["refdes"]: item for item in placed}
        self.assertTrue(all(item["placed"] for item in placed))
        boxes = {}
        for item in placed:
            spec = next(lab for lab in labels if lab["refdes"] == item["refdes"])
            boxes[item["refdes"]] = (
                item["x"] - spec["width"] / 2,
                item["y"] - spec["height"] / 2,
                item["x"] + spec["width"] / 2,
                item["y"] + spec["height"] / 2,
            )
        # no label on a part (its own included) or on another label or the obstacle
        parts = {lab["refdes"]: tuple(lab["part"]) for lab in labels}
        for ref, box in boxes.items():
            for rect in list(parts.values()) + obstacles + [b for r, b in boxes.items() if r != ref]:
                self.assertFalse(L._overlaps(box, rect, 0.3 - 1e-6), msg=(ref, box, rect))
        # U1's label sits above the part (the first choice, nothing in the way)
        self.assertGreater(by["U1"]["y"], 16.0)
        # a label that fits nowhere keeps its place and says so
        crowded = [
            {
                "refdes": "X1",
                "width": 30.0,
                "height": 20.0,
                "part": [1.0, 1.0, 2.0, 2.0],
                "x": 1.5,
                "y": 1.5,
            }
        ]
        kept = L.place_labels(crowded, [], (0.0, 0.0, 10.0, 10.0))
        self.assertIs(kept[0]["placed"], False)
        self.assertEqual((kept[0]["x"], kept[0]["y"]), (1.5, 1.5))
