"""A dual-RC design's cramped run, with an explicit bounded spacing choice.

At the default node pitch the two channels' texts collide (10 DS-17 findings); a
`pitch` on the run spaces its parts out. These are planner tests, not CAD evidence.
"""

from __future__ import annotations

import copy
import unittest
from unittest import mock

from xpedition_cli.adapter.plan import schematic_layout as L


def dual_rc() -> dict:
    blocks = []
    for channel, row, top, refs in (
        ("A", 560, 630, ("J1", "R1", "C1", "J2")),
        ("B", 320, 390, ("J3", "R2", "C2", "J4")),
    ):
        incoming, resistor, capacitor, outgoing = refs
        blocks += [
            {
                "kind": "text",
                "text": f"CHANNEL {channel}  -  SYNTHETIC RC",
                "x": 110,
                "y": top,
                "size": 11,
            },
            {
                "kind": "connector",
                "refdes": incoming,
                "symbol": "HDR",
                "value": "HDR2",
                "x": 180,
                "y": row,
                "pins": {"1": f"label:{channel}_IN", "2": "gnd"},
            },
            {
                "kind": "chain",
                "x": 430,
                "y": row,
                "path": [
                    f"label:{channel}_IN",
                    {"refdes": resistor, "symbol": "RES", "value": "10k"},
                    f"label:{channel}_OUT",
                    {"refdes": capacitor, "symbol": "CAP", "value": "100n"},
                    "gnd",
                ],
            },
            {
                "kind": "connector",
                "refdes": outgoing,
                "symbol": "HDR",
                "value": "HDR2",
                "x": 900,
                "y": row,
                "pins": {"1": f"label:{channel}_OUT", "2": "gnd"},
            },
        ]
    return {
        "title": "DUAL RC TOOL ACCEPTANCE",
        "sheet_size": "A4",
        "status": "SYNTHETIC - NOT QUALIFIED",
        "revision": "T0",
        "date": "2026-10-17",
        "symbols": {
            "HDR": {
                "kind": "box",
                "right": [["1", "1"], ["2", "2"]],
                "pintypes": {"1": "ANALOG", "2": "ANALOG"},
            }
        },
        "sheets": [
            {
                "number": 1,
                "title": "TWO INDEPENDENT SIGNAL CHANNELS",
                "description": "Input left; output right; common GND only. R=10k, C=100n.",
                "blocks": blocks,
                "notes": [
                    "Synthetic values; no filter, EMC, current or manufacturing qualification.",
                    "All16 physical pins used; no NC. PCB routing subject to supported "
                    "effective-rule proof.",
                ],
            }
        ],
    }


def runs(design):
    return [b for b in design["sheets"][0]["blocks"] if b["kind"] in ("chain", "ladder")]


def one_run(kind="chain", **fields):
    return {
        "sheet_size": "A4",
        "sheets": [
            {
                "blocks": [
                    {
                        "kind": kind,
                        "x": 400,
                        "y": 600,
                        "path": [
                            "label:IN",
                            {"refdes": "R1", "symbol": "RES", "value": "10k"},
                            "label:OUT",
                            {"refdes": "C1", "symbol": "CAP", "value": "100n"},
                            "gnd",
                        ],
                        **fields,
                    }
                ]
            }
        ],
    }


def _not_dispatched(*args, **kwargs):
    raise AssertionError("a part was placed before the design was refused")


class ChainSpacingTest(unittest.TestCase):
    def test_dual_rc_real_planner_failure_and_explicit_120_spacing(self):
        design = dual_rc()
        original = copy.deepcopy(design)
        cramped = L.plan(design)
        self.assertEqual(len(cramped.issues), 10)
        self.assertEqual({i["check"] for i in cramped.issues}, {"DS-17"})
        self.assertTrue(any(
            i["object"] == "label A_OUT" and i.get("with") == "C1 (lines)" for i in cramped.issues
        ))
        for block in runs(design):
            block["pitch"] = 120
        roomy = L.plan(design)
        self.assertEqual(roomy.issues, [])
        self.assertTrue(cramped.nets
            == roomy.nets
            == {
                "A_IN": {"J1.1", "R1.1"},
                "A_OUT": {"R1.2", "C1.1", "J2.1"},
                "B_IN": {"J3.1", "R2.1"},
                "B_OUT": {"R2.2", "C2.1", "J4.1"},
                "GND": {"J1.2", "J2.2", "C1.2", "J3.2", "J4.2", "C2.2"},
            })
        self.assertTrue(roomy.links == cramped.links == [])
        self.assertTrue(roomy.no_connects == cramped.no_connects == [])
        self.assertEqual(len(roomy.parts), 8)
        for plan in (cramped, roomy):
            self.assertEqual({
                (p["refdes"], p["orientation"])
                for p in plan.parts
                if p["refdes"].startswith(("R", "C"))
            }, {("R1", 0), ("C1", 0), ("R2", 0), ("C2", 0)})
        # Only the requested run spacing changed; no input or existing text is auto-edited.
        for block in runs(original):
            block["pitch"] = 120
        self.assertEqual(design, original)

    def test_spacing_preserves_pin_direction_and_net_identity(self):
        for flip in [False, True]:
            with self.subTest(flip=flip):
                for kind in ["chain", "ladder"]:
                    with self.subTest(kind=kind):
                        base = one_run(kind)
                        for part in runs(base)[0]["path"][1::2]:
                            part["flip"] = flip
                        explicit_default = copy.deepcopy(base)
                        runs(explicit_default)[0]["pitch"] = 60
                        self.assertEqual(L.plan(base), L.plan(explicit_default))
                        wider = copy.deepcopy(base)
                        runs(wider)[0]["pitch"] = 120
                        old, new = L.plan(base), L.plan(wider)
                        self.assertEqual(old.nets, new.nets)
                        head, tail = ("2", "1") if flip else ("1", "2")
                        self.assertEqual(new.nets, {
                            "IN": {f"R1.{head}"},
                            "OUT": {f"R1.{tail}", f"C1.{head}"},
                            "GND": {f"C1.{tail}"},
                        })
                        orientations = {p["refdes"]: p["orientation"] for p in new.parts}
                        self.assertEqual(set(orientations.values()), {
                            ((1 if flip else 3) if kind == "ladder" else (2 if flip else 0))
                        })
                        r1 = next(p for p in new.parts if p["refdes"] == "R1")
                        c1 = next(p for p in new.parts if p["refdes"] == "C1")
                        if kind == "ladder":
                            self.assertEqual((r1["x"], r1["y"], c1["x"], c1["y"]), (400, 540, 400, 420))
                        else:
                            self.assertEqual((r1["x"], r1["y"], c1["x"], c1["y"]), (460, 600, 580, 600))
                        # Each labelled wire still terminates on the same physical pin, now at a longer
                        # distance from its node; no endpoint is swapped by a spacing-only change.
                        for plan in (old, new):
                            first_wire = next(op for op in plan.ops if op["op"] == "wire")
                            self.assertEqual(first_wire["end"], f"R1.{head}")
                            self.assertEqual(first_wire["points"][0], [400, 600])
                            self.assertTrue(all(
                                x % 10 == 0 and y % 10 == 0
                                for op in plan.ops
                                if op["op"] == "wire"
                                for x, y in op["points"]
                            ))

    def test_legal_integer_grid_pitch(self):
        for pitch in [60, 80, 100, 120, 200]:
            with self.subTest(pitch=pitch):
                self.assertEqual(len(L.plan(one_run(pitch=pitch)).parts), 2)

    def test_illegal_pitch_refuses_before_placing_parts(self):
        for pitch in [
            True,
            False,
            None,
            "120",
            120.0,
            float("nan"),
            float("inf"),
            0,
            -60,
            59,
            61,
            70,
            4681,
            10**100,
        ]:
            with self.subTest(pitch=pitch):
                with mock.patch.object(L._SheetPlanner, "part", _not_dispatched):
                    with self.assertRaisesRegex(L.DesignError, "pitch must be an integer"):
                        L.plan(one_run(pitch=pitch))

    def test_huge_run_budget_refuses_before_placing_parts(self):
        design = one_run()
        block = runs(design)[0]
        block["path"] = ["label:IN"]
        for n in range(100):
            block["path"] += [{"refdes": f"R{n}", "symbol": "RES"}, f"label:N{n}"]
        with mock.patch.object(L._SheetPlanner, "part", _not_dispatched):
            with self.assertRaisesRegex(L.DesignError, "sheet-length budget"):
                L.plan(design)

    def test_unknown_spacing_fields_are_not_silently_ignored(self):
        for key in ["node_pitch", "pitc", "spacing"]:
            with self.subTest(key=key):
                with self.assertRaisesRegex(L.DesignError, "unsupported fields"):
                    L.plan(one_run(**{key: 120}))

    def test_pitch_on_part_is_not_silently_ignored(self):
        design = one_run()
        runs(design)[0]["path"][1]["pitch"] = 120
        with self.assertRaisesRegex(L.DesignError, "part has unsupported fields"):
            L.plan(design)

    def test_larger_spacing_keeps_drawable_boundary_reminders(self):
        design = one_run(pitch=120, x=1000)
        result = L.plan(design)
        self.assertTrue(any(i["check"] == "DS-07" for i in result.issues))
