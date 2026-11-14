"""The board edits of adapter/layout_edit.py on fake COM objects."""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from test_layout_fakes import BOARD, Collection, FakeApp, FakeDoc, Geometry, context, world  # noqa: E402

from xpedition_cli._driver import AdapterError  # noqa: E402
from xpedition_cli.adapter import base  # noqa: E402
from xpedition_cli.adapter import layout_edit as E  # noqa: E402
from xpedition_cli.adapter.plan import openings as OP  # noqa: E402
from xpedition_cli.adapter.plan import stitching as ST  # noqa: E402
from xpedition_cli.adapter.plan import trace_plan as TP  # noqa: E402


def key(domain: str) -> str:
    return base.key(BOARD, domain)


class Item(SimpleNamespace):
    def __init__(self, owner: list, **fields) -> None:
        super().__init__(**fields)
        self.owner = owner

    def Delete(self) -> None:  # noqa: N802
        self.owner.remove(self)


def board() -> FakeDoc:
    doc = FakeDoc(nets=["A", "B", "GND"])
    for net, layer in (("A", 1), ("A", 1), ("A", 2), ("B", 1)):
        doc.traces.append(Item(doc.traces, Net=SimpleNamespace(Name=net), Layer=layer))
    for net in ("A", "GND", "GND"):
        doc.vias.append(Item(doc.vias, Net=SimpleNamespace(Name=net), Name="026VIA"))
    doc.shapes.append(Item(doc.shapes, Net=SimpleNamespace(Name="GND"), Layer=2))
    return doc


class TraceDeleteTest(unittest.TestCase):
    def test_preview_counts_apply_deletes_and_read_back_finds_none(self):
        doc = board()
        with world(FakeApp(doc)):
            out = E.TraceDelete().preview(context("trace delete", {"nets": "A"}))
            facts = E.TraceDelete().apply(context("trace delete", {"nets": "A"}))
            back = E.TraceDelete().read_back(context("trace delete", targets=[key("/traces/A")], fields=["count"]))
        self.assertEqual([(c["target"], c["before"], c["after"]) for c in out["changes"]], [(key("/traces/A"), 3, 0)])
        self.assertEqual((facts["change"], facts["result"]["deleted"]), ("applied", 3))
        self.assertEqual(back["observed"], {key("/traces/A"): {"count": 0}})
        self.assertEqual([t.Net.Name for t in doc.traces], ["B"])
        self.assertEqual(len(doc.vias), 3)  # vias stay

    def test_layers_limit_what_goes(self):
        doc = board()
        with world(FakeApp(doc)):
            out = E.TraceDelete().preview(context("trace delete", {"nets": "A,B", "layers": "1"}))
            E.TraceDelete().apply(context("trace delete", {"nets": "A,B", "layers": "1"}))
        self.assertEqual([(c["target"], c["before"]) for c in out["changes"]], [(key("/traces/A/1"), 2), (key("/traces/B/1"), 1)])
        self.assertEqual([(t.Net.Name, t.Layer) for t in doc.traces], [("A", 2)])

    def test_with_a_region_only_the_traces_wholly_inside_it_go(self):
        from test_layout_fakes import Extrema
        doc = board()
        for trace, box in zip(doc.traces, ((2.0, 3.0, 2.5, 4.0), (2.0, 3.0, 9.0, 4.0), (2.2, 3.5, 2.8, 4.5), (2.0, 3.0, 2.5, 4.0)),
                              strict=True):
            trace.Extrema = Extrema(box)  # A: a stub inside, a trace leaving the rectangle, one on layer 2; B inside
        inputs = {"nets": "A", "from_x_mm": 1.0, "to_x_mm": 3.0, "from_y_mm": 2.0, "to_y_mm": 5.0}
        with world(FakeApp(doc)):
            out = E.TraceDelete().preview(context("trace delete", inputs))
            facts = E.TraceDelete().apply(context("trace delete", inputs))
        self.assertEqual([(c["before"], c["after"]) for c in out["changes"]], [(3, 1)])
        self.assertEqual(facts["result"]["deleted"], 2)
        self.assertEqual([(t.Net.Name, t.Extrema.box[2]) for t in doc.traces], [("A", 9.0), ("B", 2.5)])

    def test_nothing_to_delete_and_unknown_names(self):
        with world(FakeApp(board())):
            self.assertEqual(E.TraceDelete().preview(context("trace delete", {"nets": "GND"}))["changes"], [])
            for inputs, wanted in (({"nets": "NOPE"}, "E_NOT_FOUND"), ({"nets": "A", "layers": "9"}, "E_VALIDATION"),
                                   ({"nets": " , "}, "E_VALIDATION")):
                with self.subTest(inputs=inputs), self.assertRaises(AdapterError) as caught:
                    E.TraceDelete().preview(context("trace delete", inputs))
                self.assertEqual(caught.exception.data["code"], wanted)


class ViaDeleteTest(unittest.TestCase):
    def test_the_vias_of_a_net_go_with_their_padstack(self):
        doc = board()
        for via in doc.vias:
            via.Name = "026VIA"
        with world(FakeApp(doc)):
            out = E.ViaDelete().preview(context("via delete", {"nets": "GND"}))
            facts = E.ViaDelete().apply(context("via delete", {"nets": "GND"}))
            back = E.ViaDelete().read_back(context("via delete", targets=[key("/vias/GND")], fields=["count", "padstack"]))
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in out["changes"]], [("count", 2, 0), ("padstack", "026VIA", None)])
        self.assertEqual(facts["result"]["deleted"], 2)
        self.assertEqual(back["observed"], {key("/vias/GND"): {"count": 0, "padstack": None}})
        self.assertEqual([v.Net.Name for v in doc.vias], ["A"])


    def test_with_a_region_only_the_vias_inside_it_go(self):
        doc = board()
        for via, x in zip(doc.vias, (1.0, 2.0, 30.0), strict=True):  # A at 1, GND at 2 and 30
            via.Name = "026VIA"
            via.GetPositionX = lambda unit, x=x: x
            via.GetPositionY = lambda unit: 5.0
        inputs = {"nets": "GND", "from_x_mm": 10.0}
        with world(FakeApp(doc)):
            out = E.ViaDelete().preview(context("via delete", inputs))
            facts = E.ViaDelete().apply(context("via delete", inputs))
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in out["changes"]], [("count", 2, 1)])  # the padstack stays
        self.assertIn("within x from 10 mm", out["action"])
        self.assertEqual(facts["result"]["deleted"], 1)
        self.assertEqual([(v.Net.Name, v.GetPositionX(0)) for v in doc.vias], [("A", 1.0), ("GND", 2.0)])


class PlaneDeleteTest(unittest.TestCase):
    def test_a_plane_shape_goes_and_a_missing_one_is_not_found(self):
        doc = board()
        with world(FakeApp(doc)):
            out = E.PlaneDelete().preview(context("plane delete", {"net": "GND", "layer": 2}))
            facts = E.PlaneDelete().apply(context("plane delete", {"net": "GND", "layer": 2}))
            with self.assertRaises(AdapterError) as missing:
                E.PlaneDelete().preview(context("plane delete", {"net": "GND", "layer": 2}))
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in out["changes"]], [("exists", True, False)])
        self.assertEqual((facts["change"], facts["result"]["deleted"]), ("applied", 1))
        self.assertEqual(doc.shapes, [])
        self.assertEqual(missing.exception.data["code"], "E_NOT_FOUND")


class PlaneDeleteFillTest(unittest.TestCase):
    def test_a_hatched_plane_takes_its_fill_with_it(self):
        doc = board()
        hatched = {"exists": True, "state": "dynamic", "hatch": "orthogonal", "hatch_width_mm": 0.1, "hatch_distance_mm": 0.5}
        with world(FakeApp(doc)), mock.patch.object(E.L, "plane_values", lambda doc, net, layer: hatched):
            out = E.PlaneDelete().preview(context("plane delete", {"net": "GND", "layer": 2}))
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in out["changes"]],
                         [("exists", True, False), ("hatch", "orthogonal", None), ("hatch_width_mm", 0.1, None),
                          ("hatch_distance_mm", 0.5, None)])

    def test_a_plane_that_is_gone_reads_back_without_a_fill(self):
        doc = board()
        doc.shapes.clear()
        with world(FakeApp(doc)):
            values = E.L.plane_values(doc, "GND", 2)
        self.assertEqual((values["exists"], values["hatch"], values["hatch_width_mm"]), (False, None, None))


class ViaCreateTest(unittest.TestCase):
    """The plan and the placing; the board's geometry, the library row and the clearances are given."""

    def setUp(self):
        self.doc = board()
        self.doc.placed_vias = []

        def put_via(x, y, stack, net, component, fixed, unit):
            self.doc.placed_vias.append((x, y, stack.Name, net.Name))
            self.doc.vias.append(Item(self.doc.vias, Net=SimpleNamespace(Name=net.Name), Name=stack.Name))

        self.doc.PutVia = put_via
        self.doc.TransactionStart = lambda mode: self.doc.calls.append(("TransactionStart", mode))
        self.doc.TransactionEnd = lambda keep: self.doc.calls.append(("TransactionEnd", keep))
        self.doc.PutPadstack = lambda first, last, name, mirrored, central: SimpleNamespace(Name=name)
        outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
        geometry = ST.Board(outline=outline, planes={2: [outline], 3: [outline]}, pads=[ST.polygon(circle=[5.0, 2.5, 0.3])])
        self.patches = [mock.patch.object(E, "_stitch_board", lambda doc, net: geometry),
                        mock.patch.object(E, "_via_row", lambda path, name: {"type": "via", "pad_mm": 0.4, "drill_mm": 0.2}),
                        mock.patch.object(E, "_clearances", lambda path: ST.Clearances(0.15, 0.15, 0.2)),
                        mock.patch.object(E, "_class_via", lambda doc, net: "026VIA")]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_grid_is_planned_placed_with_drc_off_and_read_back(self):
        inputs = {"net": "GND", "pitch_mm": 1.0}
        with world(FakeApp(self.doc)):
            preview = E.ViaCreate().preview(context("via create", inputs))
            facts = E.ViaCreate().run(context("via create", inputs), None)
            back = E.ViaCreate().read_back(context("via create", targets=[key("/vias/GND")], fields=["count"]))
        (change,) = preview["changes"]
        self.assertEqual((change["target"], change["before"]), (key("/vias/GND"), 2))
        placed = facts["result"]["placed"]
        self.assertEqual(change["after"], 2 + placed)
        self.assertGreater(placed, 0)
        self.assertEqual(back["observed"], {key("/vias/GND"): {"count": 2 + placed}})
        self.assertIn(("TransactionStart", 0), self.doc.calls)  # online DRC off: the plan kept the clearances
        self.assertTrue(all(name == "026VIA" for _x, _y, name, _net in self.doc.placed_vias))  # the class's via by default
        self.assertNotIn((5.0, 2.5), [(x, y) for x, y, _n, _net in self.doc.placed_vias])  # never on the pad

    def test_a_grid_keeps_to_its_region_and_one_via_takes_no_region(self):
        inputs = {"net": "GND", "pitch_mm": 1.0, "to_x_mm": 4.0}
        with world(FakeApp(self.doc)):
            preview = E.ViaCreate().preview(context("via create", inputs))
            facts = E.ViaCreate().run(context("via create", inputs), None)
            with self.assertRaises(AdapterError) as caught:
                E.ViaCreate().preview(context("via create", {"net": "GND", "x_mm": 2.0, "y_mm": 2.0, "to_x_mm": 4.0}))
        self.assertGreater(facts["result"]["placed"], 0)
        self.assertTrue(all(x <= 4.0 for x, _y, _name, _net in self.doc.placed_vias))
        self.assertIn("within x up to 4 mm", preview["action"])
        self.assertEqual(caught.exception.data["code"], "E_VALIDATION")

    def test_a_new_padstack_is_one_of_the_changes_a_save_reads_back(self):
        with world(FakeApp(self.doc)):
            other = E.ViaCreate().preview(context("via create", {"net": "GND", "x_mm": 2.0, "y_mm": 2.0, "padstack": "VIA_D0.2_P0.4"}))
            self.doc.vias[:] = [via for via in self.doc.vias if via.Net.Name != "GND"]
            first = E.ViaCreate().preview(context("via create", {"net": "GND", "x_mm": 2.0, "y_mm": 2.0}))
        stacks = {c["field"]: (c["before"], c["after"]) for c in other["changes"]}
        self.assertEqual(stacks["padstack"], ("026VIA", "mixed: 026VIA, VIA_D0.2_P0.4"))
        stacks = {c["field"]: (c["before"], c["after"]) for c in first["changes"]}
        self.assertEqual(stacks, {"count": (0, 1), "padstack": (None, "026VIA")})  # the vias a via delete took all of come back

    def test_a_via_in_a_bend_zone_is_refused(self):
        outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
        flex = ST.Board(outline=outline, planes={2: [outline], 3: [outline]}, bends=[[(4.0, 0.0), (6.0, 0.0), (6.0, 5.0), (4.0, 5.0)]])
        with world(FakeApp(self.doc)), mock.patch.object(E, "_stitch_board", lambda doc, net: flex):
            blocked = E.ViaCreate().preview(context("via create", {"net": "GND", "x_mm": 5.0, "y_mm": 1.0}))
            grid = E.ViaCreate().preview(context("via create", {"net": "GND", "pitch_mm": 1.0}))
        self.assertIn("in a bend zone", blocked["blocked_by"][0])
        self.assertIn("in a bend zone", grid["action"])

    def test_a_via_asked_for_beside_a_bend_zone_stands(self):
        # the tool's own vias keep 1 mm from a bend zone; one asked for at a place only stays out of it
        outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
        flex = ST.Board(outline=outline, planes={2: [outline], 3: [outline]}, bends=[[(4.0, 0.0), (6.0, 0.0), (6.0, 5.0), (4.0, 5.0)]])
        with world(FakeApp(self.doc)), mock.patch.object(E, "_stitch_board", lambda doc, net: flex):
            beside = E.ViaCreate().preview(context("via create", {"net": "GND", "x_mm": 3.5, "y_mm": 1.0}))
        self.assertFalse(beside.get("blocked_by"))

    def test_one_via_and_one_that_cannot_stand(self):
        with world(FakeApp(self.doc)):
            facts = E.ViaCreate().run(context("via create", {"net": "GND", "x_mm": 2.0, "y_mm": 2.0, "padstack": "VIA_D0.2_P0.4"}), None)
            blocked = E.ViaCreate().preview(context("via create", {"net": "GND", "x_mm": 5.0, "y_mm": 2.6}))
        self.assertEqual(self.doc.placed_vias, [(2.0, 2.0, "VIA_D0.2_P0.4", "GND")])
        self.assertEqual(facts["result"]["placed"], 1)
        self.assertFalse(blocked["can_apply"])
        self.assertIn("too close to a pad", blocked["blocked_by"][0])

    def test_a_via_the_board_has_needs_no_central_library(self):
        from xpedition_cli.adapter import layout_output as O

        def unreadable(path, name):
            raise AdapterError("E_NOT_FOUND", "The central library Y:\\Library.lmc that the project names does not exist.")

        self.doc.vias.append(Item(self.doc.vias, Net=SimpleNamespace(Name="GND"), Name="026VIA"))
        with world(FakeApp(self.doc)), mock.patch.object(E, "_via_row", unreadable), mock.patch.object(O, "_via_radius", lambda via, scale: 0.2):
            facts = E.ViaCreate().run(context("via create", {"net": "GND", "x_mm": 2.0, "y_mm": 2.0}), None)
            with self.assertRaises(AdapterError) as caught:
                E.ViaCreate().preview(context("via create", {"net": "GND", "x_mm": 2.0, "y_mm": 2.0, "padstack": "VIA_NEW"}))
        self.assertEqual(facts["result"]["placed"], 1)
        self.assertEqual(caught.exception.data["code"], "E_NOT_FOUND")
        self.assertIn("no via of the board uses VIA_NEW, the design's own library has no padstack VIA_NEW, and the central "
                      "library Y:", str(caught.exception))

    def test_a_via_only_the_designs_own_library_holds_needs_no_central_library(self):
        def unreadable(path, name):
            raise AdapterError("E_NOT_FOUND", "The central library Y:\\Library.lmc that the project names does not exist.")

        pulled = []

        def put_padstack(first, last, name, mirrored, central):
            pulled.append((name, central))
            return SimpleNamespace(Name=name)

        self.doc.PadstackNames = ("026VIA", "LOCAL_VIA")
        self.doc.PutPadstack = put_padstack
        with world(FakeApp(self.doc)), mock.patch.object(E, "_via_row", unreadable), \
                mock.patch.object(E, "_padstack_pad_radius", lambda stack, scale: 0.2):
            facts = E.ViaCreate().run(context("via create", {"net": "GND", "x_mm": 2.0, "y_mm": 2.0, "padstack": "LOCAL_VIA"}), None)
        self.assertEqual((facts["result"]["placed"], self.doc.placed_vias[-1][2]), (1, "LOCAL_VIA"))
        self.assertTrue(pulled)
        self.assertTrue(all(central is False for _name, central in pulled))  # from the design's library, not the central one

    def test_inputs_must_name_one_way_to_place(self):
        with world(FakeApp(self.doc)):
            for inputs, wanted in (({"net": "GND"}, "E_VALIDATION"), ({"net": "GND", "x_mm": 1.0}, "E_VALIDATION"),
                                   ({"net": "GND", "x_mm": 1.0, "y_mm": 1.0, "pitch_mm": 1.0}, "E_VALIDATION"),
                                   ({"net": "NOPE", "pitch_mm": 1.0}, "E_NOT_FOUND")):
                with self.subTest(inputs=inputs), self.assertRaises(AdapterError) as caught:
                    E.ViaCreate().preview(context("via create", inputs))
                self.assertEqual(caught.exception.data["code"], wanted)

    def test_a_net_with_copper_on_one_layer_cannot_be_stitched(self):
        outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
        with world(FakeApp(self.doc)), mock.patch.object(E, "_stitch_board", lambda doc, net: ST.Board(outline=outline, planes={2: [outline]})):
            with self.assertRaises(AdapterError) as caught:
                E.ViaCreate().preview(context("via create", {"net": "GND", "pitch_mm": 1.0}))
        self.assertEqual(caught.exception.data.get("conflict"), "application_state")


class SolderMaskTest(unittest.TestCase):
    def setUp(self):
        self.doc = board()

        def put_gfx(fab, side, width, n, points, filled, component, unit):
            xs, ys = points[0], points[1]
            box = (min(xs), min(ys), max(xs), max(ys))
            self.doc.gfxs.append(Item(self.doc.gfxs, Type=fab, Side=side, Component=None, box=box))

        self.doc.PutFabricationLayerGfx = put_gfx
        self.doc.TransactionStart = lambda mode: None
        self.doc.TransactionEnd = lambda keep: None
        self.copper = OP.LayerCopper(regions=[("GND", [[(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)]])],
                                     traces=[("SIG", 0.2, [(3.0, 4.0), (4.5, 4.0)])])
        self.bends: list = []
        self.patches = [mock.patch.object(E, "layer_copper", lambda doc, layer: self.copper),
                        mock.patch.object(E.O, "bend_boxes", lambda doc, flex=None: self.bends),
                        mock.patch.object(E, "cover_layer_sides", lambda doc: set()),
                        mock.patch.object(E.L, "extrema_mm", lambda gfx: gfx.box)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_an_opening_over_copper_is_drawn_read_back_and_deleted(self):
        rect = {"x1_mm": 1.0, "y1_mm": 3.0, "x2_mm": 4.0, "y2_mm": 1.0}
        with world(FakeApp(self.doc)):
            preview = E.SolderMaskCreate().preview(context("solder-mask create", rect))
            facts = E.SolderMaskCreate().apply(context("solder-mask create", rect))
            target = preview["changes"][0]["target"]
            back = E.SolderMaskCreate().read_back(context("solder-mask create", targets=[target], fields=["exists"]))
            again = E.SolderMaskCreate().preview(context("solder-mask create", rect))
            gone = E.SolderMaskDelete().apply(context("solder-mask delete", {"x_mm": 2.0, "y_mm": 2.0}))
            after = E.SolderMaskCreate().read_back(context("solder-mask create", targets=[target], fields=["exists"]))
        self.assertEqual(target, key("/solder-mask/top/1,1,4,3"))
        self.assertIn("baring the copper of GND", preview["action"])
        self.assertEqual((facts["result"]["created"], self.doc.gfxs == []), (True, True))
        self.assertEqual(back["observed"][target], {"exists": True})
        self.assertEqual(again["changes"], [])
        self.assertEqual(gone["result"]["rect_mm"], [1.0, 1.0, 4.0, 3.0])
        self.assertEqual(after["observed"][target], {"exists": False})

    def test_no_copper_or_no_cover_layer_blocks_the_opening(self):
        with world(FakeApp(self.doc)):
            bare = E.SolderMaskCreate().preview(context("solder-mask create", {"x1_mm": 6.0, "y1_mm": 1.0, "x2_mm": 8.0, "y2_mm": 2.0}))
            cover = E.SolderMaskCreate().preview(context("solder-mask create", {"x1_mm": 1.0, "y1_mm": 1.0, "x2_mm": 2.0, "y2_mm": 2.0,
                                                                              "cover_layer": True}))
        self.assertIn("no copper lies inside", bare["blocked_by"][0])
        self.assertIn("no cover layer on the top side", cover["blocked_by"][0])

    def test_an_opening_for_one_net_keeps_off_other_copper(self):
        with world(FakeApp(self.doc)):
            over = E.SolderMaskCreate().preview(context("solder-mask create", {"x1_mm": 2.0, "y1_mm": 2.5, "x2_mm": 4.6, "y2_mm": 4.2,
                                                                             "net": "GND"}))
            close = E.SolderMaskCreate().preview(context("solder-mask create", {"x1_mm": 1.0, "y1_mm": 1.0, "x2_mm": 3.5, "y2_mm": 3.85,
                                                                              "net": "GND"}))
            clear = E.SolderMaskCreate().preview(context("solder-mask create", {"x1_mm": 1.0, "y1_mm": 1.0, "x2_mm": 3.0, "y2_mm": 3.0,
                                                                              "net": "GND"}))
            wrong = E.SolderMaskCreate().preview(context("solder-mask create", {"x1_mm": 3.5, "y1_mm": 3.9, "x2_mm": 4.0, "y2_mm": 4.1,
                                                                              "net": "VBUS"}))
        self.assertIn("bares SIG's copper", over["blocked_by"][0])
        self.assertIn("SIG's copper comes 0.05 mm from the opening", close["blocked_by"][0])
        self.assertTrue(clear["can_apply"])
        self.assertIn("0.1 mm or more from all other copper", clear["action"])
        self.assertTrue(any("bares none of VBUS's copper" in reason for reason in wrong["blocked_by"]))

    def test_fit_opens_the_largest_rectangle_on_the_net_alone(self):
        region = {"x1_mm": 0.0, "y1_mm": 0.0, "x2_mm": 5.0, "y2_mm": 5.0, "net": "GND", "fit": True}
        with world(FakeApp(self.doc)):
            preview = E.SolderMaskCreate().preview(context("solder-mask create", region))
            facts = E.SolderMaskCreate().apply(context("solder-mask create", region))
            none = E.SolderMaskCreate().preview(context("solder-mask create", {**region, "x1_mm": 6.0, "x2_mm": 8.0}))
        x1, y1, x2, y2 = facts["result"]["rect_mm"]
        self.assertTrue(x1 < 0.2 and y1 < 0.2 and x2 > 4.8 and 3.4 < y2 <= 3.75, facts["result"]["rect_mm"])
        self.assertIn("the largest rectangle that fits", preview["action"])
        self.assertEqual(facts["result"]["nets"], ["GND"])
        self.assertIn("no rectangle of at least 0.3 mm a side", none["blocked_by"][0])

    def test_a_polygon_opening_is_drawn_point_by_point(self):
        shape = {"polygon": {"points_mm": [[0.5, 0.5], [3, 0.5], [1.5, 2.5]]}, "net": "GND", "side": "bottom"}
        with world(FakeApp(self.doc)):
            preview = E.SolderMaskCreate().preview(context("solder-mask create", shape))
            facts = E.SolderMaskCreate().apply(context("solder-mask create", shape))
        self.assertEqual(preview["changes"][0]["target"], key("/solder-mask/bottom/0.5,0.5,3,2.5"))
        self.assertIn("the polygon of 3 points", preview["action"])
        self.assertEqual(facts["result"]["points_mm"], [[0.5, 0.5], [3.0, 0.5], [1.5, 2.5]])
        self.assertEqual(self.doc.gfxs[0].box, (0.5, 0.5, 3.0, 2.5))

    def test_a_bend_zone_or_a_wrong_request_stops_the_opening(self):
        self.bends = [(0.0, 2.0, 10.0, 3.0)]
        with world(FakeApp(self.doc)):
            bend = E.SolderMaskCreate().preview(context("solder-mask create", {"x1_mm": 1.0, "y1_mm": 1.0, "x2_mm": 3.0, "y2_mm": 2.5}))
            with self.assertRaises(AdapterError) as alone:
                E.SolderMaskCreate().preview(context("solder-mask create", {"x1_mm": 0.0, "y1_mm": 0.0, "x2_mm": 5.0, "y2_mm": 5.0,
                                                                          "fit": True}))
            with self.assertRaises(AdapterError) as both:
                E.SolderMaskCreate().preview(context("solder-mask create", {"x1_mm": 0.0, "y1_mm": 0.0, "x2_mm": 5.0, "y2_mm": 5.0,
                                                                          "polygon": {"points_mm": [[0, 0], [1, 0], [1, 1]]}}))
        self.assertIn("reaches into the bend zone", bend["blocked_by"][0])
        self.assertIn("name the net with --net", str(alone.exception))
        self.assertIn("not both", str(both.exception))

    def test_a_polygon_with_the_extents_of_an_opening_is_another_opening(self):
        self.doc.gfxs.append(Item(self.doc.gfxs, Type=E.FAB_SOLDERMASK, Side=E.SIDE_TOP, Component=None, box=(1.0, 1.0, 3.0, 3.0)))
        triangle = [(1.0, 1.0), (3.0, 1.0), (1.0, 3.0)]
        with world(FakeApp(self.doc)):
            self.assertTrue(E.opening_exists(self.doc, "solder-mask", "top", (1.0, 1.0, 3.0, 3.0), OP.rect_ring((1, 1, 3, 3))))
            self.assertFalse(E.opening_exists(self.doc, "solder-mask", "top", (1.0, 1.0, 3.0, 3.0), triangle))
            listed = E.listed_openings(self.doc)
        self.assertEqual(listed, [{"side": "top", "kind": "solder-mask", "extents_mm": [1.0, 1.0, 3.0, 3.0]}])

    def test_pcb_check_names_an_opening_that_joins_two_nets(self):
        for box in ((2.0, 3.0, 4.6, 4.2), (1.0, 1.0, 2.0, 2.0)):
            self.doc.gfxs.append(Item(self.doc.gfxs, Type=E.FAB_SOLDERMASK, Side=E.SIDE_TOP, Component=None, box=box))
        with world(FakeApp(self.doc)):
            rows = E.opening_rows(self.doc)
        self.assertEqual(len(rows), 1)
        self.assertIn("the top soldermask opening at 2,3 to 4.6,4.2 mm bares GND and SIG", rows[0]["finding"])
        self.assertEqual((rows[0]["rule"], rows[0]["severity"]), ("opening_short", "medium"))

    def test_delete_needs_exactly_one_opening_at_the_point(self):
        with world(FakeApp(self.doc)):
            with self.assertRaises(AdapterError) as missing:
                E.SolderMaskDelete().preview(context("solder-mask delete", {"x_mm": 2.0, "y_mm": 2.0}))
            for box in ((1.0, 1.0, 4.0, 3.0), (1.5, 1.5, 3.0, 2.5)):
                self.doc.gfxs.append(Item(self.doc.gfxs, Type=E.FAB_SOLDERMASK, Side=E.SIDE_TOP, Component=None, box=box))
            with self.assertRaises(AdapterError) as twice:
                E.SolderMaskDelete().preview(context("solder-mask delete", {"x_mm": 2.0, "y_mm": 2.0}))
        self.assertEqual(missing.exception.data["code"], "E_NOT_FOUND")
        self.assertEqual(twice.exception.data["code"], "E_AMBIGUOUS")
        self.assertEqual(len(twice.exception.data["candidates"]), 2)


class TraceCreateTest(unittest.TestCase):
    """The routing plan drawn on fake COM; the board's copper, rules and the library row are given."""

    PLAN = {"traces": [{"net": "A", "layer": 1, "width_mm": 0.1, "points_mm": [[0, 0], [3, 0]]},
                       {"net": "A", "layer": 2, "width_mm": 0.1, "points_mm": [[3, 0], [10, 0]]}],
            "vias": [{"net": "A", "at_mm": [3, 0], "padstack": "VIA_D0.2_P0.4"}]}

    def setUp(self):
        self.doc = board()
        self.doc.drawn = []
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

        self.doc.radii = []

        def put_trace(layer, net, width, count, points, component, fixed, unit):
            self.doc.drawn.append((layer, net.Name, width, list(zip(points[0], points[1], strict=True)), unit))
            self.doc.radii.append(list(points[2]))
            self.doc.traces.append(Item(self.doc.traces, Net=SimpleNamespace(Name=net.Name), Layer=layer,
                                        Geometry=Geometry([list(points[0]), list(points[1]), list(points[2])], width_mm=width)))

        def put_via(x, y, stack, net, component, fixed, unit):
            self.doc.drawn.append(("via", x, y, stack.Name, net.Name))
            self.doc.vias.append(Item(self.doc.vias, Net=SimpleNamespace(Name=net.Name), Name=stack.Name))

        self.doc.PutTrace, self.doc.PutVia = put_trace, put_via
        self.doc.TransactionStart = lambda mode: self.doc.calls.append(("TransactionStart", mode))
        self.doc.TransactionEnd = lambda keep: self.doc.calls.append(("TransactionEnd", keep))
        self.doc.PutPadstack = lambda first, last, name, mirrored, central: SimpleNamespace(Name=name)
        pads = [(1, "A", ST.polygon(circle=[0.0, 0.0, 0.2])), (2, "A", ST.polygon(circle=[10.0, 0.0, 0.2])),
                (1, "B", ST.polygon(circle=[5.0, 0.3, 0.1]))]
        before = len(self.doc.vias)

        def copper(doc):
            """The board's copper as the fake board holds it: the pads, its first vias far off, and what was drawn."""
            out = TP.Copper(layer_count=4, nets={"A", "B", "GND"}, pads=pads)
            out.vias = [(item.Net.Name, 40.0 + i, 30.0, 0.2) for i, item in enumerate(self.doc.vias[:before])]
            traces = [row for row in self.doc.drawn if row[0] != "via"]
            for row in self.doc.drawn:
                if row[0] == "via":
                    out.vias.append((row[4], row[1], row[2], 0.2))
            for row, radii in zip(traces, self.doc.radii, strict=True):  # an arc's row is its centre, as Layout keeps it
                out.traces.append((row[0], row[1], row[2], E._centre_line([[x, y, r] for (x, y), r in zip(row[3], radii, strict=True)])))
            out.model = {"traces": [{"net": net, "layer": layer, "width": width, "path": [[x, y, 0.0] for x, y in line]}
                                    for layer, net, width, line in out.traces],
                         "vias": [{"net": net, "circle": [x, y, r]} for net, x, y, r in out.vias], "pads": []}
            return out

        rules = TP.Rules(net_class_of={}, widths={"(Default)": {layer: (0.1, 0.2) for layer in range(1, 5)}},
                         clearance=lambda a, b, layer, kind: 0.1)
        self.patches = [mock.patch.object(E, "_copper", copper),
                        mock.patch.object(E, "_trace_rules", lambda doc, path: rules),
                        mock.patch.object(E, "_via_row", lambda path, name: {"type": "via", "pad_mm": 0.4, "drill_mm": 0.2}
                                          if name == "VIA_D0.2_P0.4" else None)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def plan_file(self, data) -> str:
        path = self.folder / "route.json"
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return str(path)

    def test_a_plan_is_checked_drawn_with_drc_off_and_read_back(self):
        inputs = {"file": self.plan_file(self.PLAN)}
        self.doc.nets[0].routed, self.doc.nets[0].NumberOfOpens = True, 0  # A, once drawn
        with world(FakeApp(self.doc)):
            preview = E.TraceCreate().preview(context("trace create", inputs))
            facts = E.TraceCreate().run(context("trace create", inputs), None)
            back = E.TraceCreate().read_back(context("trace create", inputs, targets=[key("/traces/A/1"), key("/traces/A/2"), key("/vias/A")],
                                                     fields=["plan_drawn", "count"]))
        self.assertTrue(preview["can_apply"])
        self.assertEqual([(c["target"], c["field"], c["before"], c["after"]) for c in preview["changes"]],
                         [(key("/traces/A/1"), "plan_drawn", False, True), (key("/traces/A/2"), "plan_drawn", False, True),
                          (key("/vias/A"), "count", 1, 2)])
        self.assertEqual(facts["result"], {"traces": 2, "vias": 1, "nets": {"total": 1, "routed": 1}, "open": []})
        self.assertEqual(self.doc.drawn, [(1, "A", 0.1, [(0.0, 0.0), (3.0, 0.0)], E.L.UNIT_MM),
                                          (2, "A", 0.1, [(3.0, 0.0), (10.0, 0.0)], E.L.UNIT_MM),
                                          ("via", 3.0, 0.0, "VIA_D0.2_P0.4", "A")])
        self.assertEqual(self.doc.calls[:1], [("TransactionStart", 0)])  # online DRC off: the check kept the rules
        self.assertEqual(back["observed"], {key("/traces/A/1"): {"plan_drawn": True}, key("/traces/A/2"): {"plan_drawn": True},
                                            key("/vias/A"): {"count": 2}})

    def test_an_end_on_a_pad_or_a_via_is_joined_to_the_pin_origin_or_the_via_centre(self):
        class Box:
            def __init__(self, *corners):
                self.corners = corners

            def GetMinX(self, unit):  # noqa: N802
                return self.corners[0]

            def GetMinY(self, unit):  # noqa: N802
                return self.corners[1]

            def GetMaxX(self, unit):  # noqa: N802
                return self.corners[2]

            def GetMaxY(self, unit):  # noqa: N802
                return self.corners[3]

        # Layout keeps the pin at a few nanometres off the rounded position an Agent reads
        pin = SimpleNamespace(GetPositionX=lambda unit: 0.00004, GetPositionY=lambda unit: -0.00003,
                              Pads=Collection([SimpleNamespace(Layer=1, Extrema=Box(-0.2, -0.2, 0.2, 0.2))]))
        net_a = SimpleNamespace(Name="A", Pins=Collection([pin]), NumberOfOpens=0, IsRouted=True)
        plan = {"traces": [{"net": "A", "layer": 1, "width_mm": 0.1, "points_mm": [[0.05, 0.02], [3, 0]]},
                           {"net": "A", "layer": 2, "width_mm": 0.1, "points_mm": [[3.1, 0.05], [10, 0]]}],
                "vias": [{"net": "A", "at_mm": [3, 0], "padstack": "VIA_D0.2_P0.4"}]}
        real = E.L.find_net
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net", lambda doc, name: net_a if name == "A" else real(doc, name)):
            facts = E.TraceCreate().run(context("trace create", {"file": self.plan_file(plan)}), None)
        self.assertEqual(self.doc.drawn[0][3], [(0.00004, -0.00003), (0.05, 0.02), (3.0, 0.0)])  # joined to the pin's origin
        near = TP.Trace("A", 1, 0.1, [(0.0, 0.0), (3.0, 0.0)])
        self.assertEqual([row[:2] for row in E._joined(near, {("A", 1): [((0.00004, -0.00003), (-0.2, -0.2, 0.2, 0.2))]})],
                         [(0.00004, -0.00003), (3.0, 0.0)])  # nanometres off: moved onto the origin, no extra segment
        self.assertEqual(self.doc.drawn[1][3], [(3.0, 0.0), (3.1, 0.05), (10.0, 0.0)])  # joined to the via's centre
        self.assertEqual((facts["result"]["nets"], facts["result"]["open"]), ({"total": 1, "routed": 1}, []))

    def test_a_rounded_corner_reaches_layout_as_an_arc(self):
        plan = {"traces": [{"net": "A", "layer": 1, "width_mm": 0.1, "points_mm": [[0, 0], [3, 0, 1.0], [3, 3]]}],
                "vias": [{"net": "A", "at_mm": [3, 3], "padstack": "VIA_D0.2_P0.4"}]}
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            E.TraceCreate().run(context("trace create", {"file": self.plan_file(plan)}), None)
        self.assertEqual([tuple(round(v, 6) for v in p) for p in self.doc.drawn[0][3]], [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (3.0, 1.0), (3.0, 3.0)])
        self.assertEqual(self.doc.radii[0], [0.0, 0.0, -1.0, 0.0, 0.0])  # the centre row carries the radius, counter-clockwise

    def test_a_lane_follows_the_board_outline_at_its_distance(self):
        # the board is 50 x 40 mm: the lane runs 5 mm inside the top edge between two vias
        plan = {"vias": [{"net": "A", "at_mm": [20, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "A", "at_mm": [30, 20], "padstack": "VIA_D0.2_P0.4"}],
                "lanes": [{"net": "A", "layer": 3, "width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5}]}
        inputs = {"file": self.plan_file(plan)}
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            preview = E.TraceCreate().preview(context("trace create", inputs))
            E.TraceCreate().run(context("trace create", inputs), None)
            back = E.TraceCreate().read_back(context("trace create", inputs, targets=[key("/traces/A/3")], fields=["plan_drawn"]))
        self.assertTrue(preview["can_apply"])
        self.assertIn("Draw 1 traces, 1 of them lanes along the board outline,", preview["action"])
        rows = [tuple(round(v, 6) for v in p) for p in self.doc.drawn[0][3]]
        self.assertEqual((rows[0], rows[-1]), ((20.0, 20.0), (30.0, 20.0)))
        self.assertEqual(rows, [(20.0, 20.0), (20.0, 34.5), (20.5, 34.5), (20.5, 35.0), (29.5, 35.0), (29.5, 34.5), (30.0, 34.5),
                                (30.0, 20.0)])  # 5 mm inside the edge, the turns rounded with 0.5 mm arcs
        self.assertEqual(back["observed"], {key("/traces/A/3"): {"plan_drawn": True}})

    def test_guards_run_beside_a_lane_and_a_ring_round_the_board_both_clear_of_other_copper(self):
        plan = {"vias": [{"net": "A", "at_mm": [20, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "A", "at_mm": [30, 20], "padstack": "VIA_D0.2_P0.4"}],
                "lanes": [{"net": "A", "layer": 3, "width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5}],
                "guards": [{"nets": ["A"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.15}],
                "rings": [{"net": "GND", "layers": [1], "width_mm": 0.2, "offset_mm": 0.5, "via_pitch_mm": 10,
                           "padstack": "VIA_D0.2_P0.4"}]}
        inputs = {"file": self.plan_file(plan)}
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            preview = E.TraceCreate().preview(context("trace create", inputs))
            E.TraceCreate().run(context("trace create", inputs), None)
        self.assertTrue(preview["can_apply"], preview.get("blocked_by"))
        guards = [row for row in self.doc.drawn if row[0] == 3 and row[1] == "GND"]
        self.assertEqual(sorted({round(y, 4) for row in guards for _x, y in row[3]}), [34.75, 35.25])  # 0.15 mm off A's copper
        xs = [x for row in guards for x, _y in row[3]]
        self.assertTrue(20.4 <= min(xs) and max(xs) <= 29.6)  # only where A runs on its line
        ring = [row for row in self.doc.drawn if row[0] == 1 and row[1] == "GND"]
        # cut once, by B's pad near the bottom edge, and closed round the rest: two traces that meet at the ring's start,
        # and the end on one side of the pad joined round it to the via at the end on the other side
        stretches = sorted(ring, key=lambda row: -sum(math.dist(a, b) for a, b in zip(row[3], row[3][1:])))[:2]
        starts = {(round(row[3][0][0], 6), round(row[3][0][1], 6)) for row in stretches}
        finishes = {(round(row[3][-1][0], 6), round(row[3][-1][1], 6)) for row in stretches}
        self.assertEqual(len(starts & finishes), 1)
        free = sorted(p[0] for p in (starts ^ finishes))
        self.assertTrue(free[0] < 5.0 < free[1])  # either side of the pad
        joins = [row for row in ring if row not in stretches]  # round the pad, and on from a line's end to a via moved off it
        self.assertTrue(any(min(x for x, _y in row[3]) < 5.0 < max(x for x, _y in row[3]) for row in joins))
        self.assertTrue(all(sum(math.dist(a, b) for a, b in zip(row[3], row[3][1:])) < 4.5 for row in joins))
        self.assertIn("guards on layer 3: 2 lines kept in 2 stretches", preview["action"])
        self.assertIn("rings[0] on layer 1:", preview["action"])
        ring_vias = [row for row in self.doc.drawn if row[0] == "via" and row[4] == "GND"]
        self.assertGreaterEqual(len(ring_vias), 10)

    def test_a_guard_nearer_its_lanes_than_the_rules_allow_is_refused_with_the_gap_they_need(self):
        plan = {"vias": [{"net": "A", "at_mm": [20, 20], "padstack": "VIA_D0.2_P0.4"}],
                "lanes": [{"net": "A", "layer": 3, "width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5}],
                "guards": [{"nets": ["A"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.08}]}  # the rules ask 0.1 mm
        with world(FakeApp(self.doc)), self.assertRaises(AdapterError) as caught:
            E.TraceCreate().preview(context("trace create", {"file": self.plan_file(plan)}))
        self.assertEqual(caught.exception.data["code"], "E_VALIDATION")
        self.assertIn("guards[0] keeps 0.08 mm from A, but the clearance rules ask 0.1 mm between GND and A on layer 3",
                      str(caught.exception))
        self.assertIn("give gap_mm of at least 0.1", str(caught.exception))

    def test_a_guard_between_two_lanes_fills_the_room_only_up_to_its_net_class_widest_trace(self):
        plan = {"vias": [{"net": "A", "at_mm": [20, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "A", "at_mm": [30, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "B", "at_mm": [21, 18], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "B", "at_mm": [29, 18], "padstack": "VIA_D0.2_P0.4"}],
                "lanes": [{"net": "A", "layer": 3, "width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5},
                          {"net": "B", "layer": 3, "width_mm": 0.1, "from_mm": [[21, 18]], "to_mm": [[29, 18]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 6}],
                "guards": [{"nets": ["A", "B"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.1, "between": True}]}
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            inputs = {"file": self.plan_file(plan)}
            preview = E.TraceCreate().preview(context("trace create", inputs))
            E.TraceCreate().run(context("trace create", inputs), None)
        self.assertTrue(preview["can_apply"], preview.get("blocked_by"))
        between = [row for row in self.doc.drawn if row[0] == 3 and row[1] == "GND" and all(34.1 < y < 34.9 for _x, y in row[3])]
        self.assertTrue(between)
        self.assertEqual({row[2] for row in between}, {0.2})  # 0.7 mm of room, the class's widest 0.2 mm

    def test_a_guard_hemmed_in_between_two_lanes_would_float_and_is_refused_and_trace_check_says_why(self):
        plan = {"vias": [{"net": "A", "at_mm": [20, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "A", "at_mm": [30, 20], "padstack": "VIA_D0.2_P0.4"}],
                "lanes": [{"net": "A", "layer": 3, "width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5},
                          {"net": "B", "layer": 3, "width_mm": 0.1, "from_mm": [[20.4, 20]], "to_mm": [[29.6, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5.4}],
                "guards": [{"nets": ["A", "B"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.1, "between": True, "via_pitch_mm": 2,
                            "padstack": "VIA_D0.2_P0.4"}]}
        inputs = {"file": self.plan_file(plan)}
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            preview = E.TraceCreate().preview(context("trace create", inputs))
            found = E.TraceCheck().read(context("trace check", inputs))
        self.assertFalse(preview["can_apply"])
        self.assertTrue(any("guard between A and B" in reason and "would float" in reason for reason in preview["blocked_by"]))
        self.assertEqual((found["floating_count"], found["pour_only_count"]), (1, 0))
        outer = next(note for note in found["notes"] if note.startswith("guards on layer 3"))
        self.assertNotIn(" 0 vias", outer)  # the outer two take vias moved off their line towards the open board beside them
        floating = found["floating"][0]
        self.assertEqual((floating["what"], floating["layer"]), ("guard between A and B", 3))
        self.assertTrue(set(floating["blocked_by"]) & {"A", "B"})
        self.assertLess(floating["gap_mm"], floating["needed_mm"])
        self.assertIn("more room for a via", floating["next"])
        self.assertEqual(found["blocked_count"], len(found["blocked"]))
        out = Path(self.plan_file(plan)).with_name("checked.json")
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            E.TraceCheck().read(context("trace check", {**inputs, "output": str(out)}))
        written = json.loads(out.read_text(encoding="utf-8"))["plan"]
        lane = next(t for t in written["traces"] if t["net"] == "B")
        self.assertEqual((lane["from"], lane["layer"]), ("lanes[1]", 3))  # each line with its plan entry and points
        self.assertEqual(lane["points_mm"][0], [20.4, 20.0])
        self.assertEqual([v["from"] for v in written["vias"]][:2], ["vias[0]", "vias[1]"])

    def test_a_lane_with_a_neck_widens_only_itself_not_the_other_lanes_of_its_net(self):
        plan = {"vias": [{"net": "GND", "at_mm": at, "padstack": "VIA_D0.2_P0.4"} for at in ([20, 20], [30, 20], [24, 25], [26, 25])],
                "lanes": [{"net": "GND", "layer": 3, "width_mm": 0.3, "neck_width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5},
                          {"net": "GND", "layer": 3, "width_mm": 0.1, "from_mm": [[24, 25]], "to_mm": [[26, 25]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 6}]}
        out = Path(self.plan_file(plan)).with_name("necks.json")
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            E.TraceCheck().read(context("trace check", {"file": self.plan_file(plan), "output": str(out)}))
        written = json.loads(out.read_text(encoding="utf-8"))["plan"]["traces"]
        self.assertEqual({t["width_mm"] for t in written if t["from"] == "lanes[1]"}, {0.1})  # the narrow lane of the net stays narrow
        self.assertEqual({t["width_mm"] for t in written if t["from"] == "lanes[0]"}, {0.3})  # the neck as wide as its lane where it clears

    def test_a_guard_end_with_no_via_of_its_own_joins_a_ground_via_nearby(self):
        plan = {"vias": [{"net": "A", "at_mm": [20, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "A", "at_mm": [30, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "GND", "at_mm": [18.0, 35.25], "padstack": "VIA_D0.2_P0.4"}],
                "lanes": [{"net": "A", "layer": 3, "width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5}],
                "guards": [{"nets": ["A"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.15}],
                "rings": [{"net": "GND", "layers": [1], "width_mm": 0.2, "offset_mm": 0.5, "via_pitch_mm": 10,
                           "padstack": "VIA_D0.2_P0.4"}]}
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            preview = E.TraceCreate().preview(context("trace create", {"file": self.plan_file(plan)}))
        self.assertTrue(preview["can_apply"], preview.get("blocked_by"))
        # the guard's end to the ground via in line with it, and an end of the ring to a ground via near it
        self.assertIn("guards and rings: 2 ends joined to a pin or via of their net nearby", preview["action"])

    def test_an_end_tries_again_once_every_line_has_its_vias(self):
        plan = {"vias": [{"net": "A", "at_mm": [20, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "A", "at_mm": [30, 20], "padstack": "VIA_D0.2_P0.4"}],
                "lanes": [{"net": "A", "layer": 3, "width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5}],
                "guards": [{"nets": ["A"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.15}],  # no vias of their own
                "rings": [{"net": "GND", "layers": [1], "width_mm": 0.2, "offset_mm": 2.0, "via_pitch_mm": 2,
                           "padstack": "VIA_D0.2_P0.4"}]}  # laid after the guards, its vias 3 mm from the upper guard
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            found = E.TraceCheck().read(context("trace check", {"file": self.plan_file(plan)}))
        self.assertEqual((found["pour_only_count"], found["free_end_count"]), (1, 2))  # the upper guard's ends joined the ring's vias
        self.assertIn("guards and rings: 2 ends joined to a pin or via of their net nearby", found["notes"])
        self.assertIn("guards on layer 3: 2 lines kept in 2 stretches, 18.0 mm, 0 vias, 2 ends without a via", found["notes"])

    def test_a_rings_stretch_through_its_start_is_judged_as_one_line(self):
        plan = {"rings": [{"net": "GND", "layers": [1], "width_mm": 0.2, "offset_mm": 0.5}]}  # no vias asked for
        real = E.GD.spans

        def halves(chain, net, layer, width, obstacles, min_run, closed=False):  # cut on both sides near the start, at the bottom's middle
            return [(0.0, 10.0), (chain.length - 10.0, chain.length)] if closed else real(chain, net, layer, width, obstacles, min_run)

        with world(FakeApp(self.doc)), mock.patch.object(E.GD, "spans", halves), \
                mock.patch.object(E.L, "find_net", lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            found = E.TraceCheck().read(context("trace check", {"file": self.plan_file(plan)}))
        lines = found["pour_only"] + found["floating"]
        self.assertEqual([(row["what"], row["length_mm"]) for row in lines], [("rings[0] on layer 1", 20.0)])  # its two halves, as one
        self.assertEqual(found["free_end_count"], 2)
        self.assertIn("rings[0] on layer 1: 20.0 of 176.0 mm in 1 stretches, 0 vias, 2 ends without a via", found["notes"])

    def test_a_guard_end_does_not_join_back_to_a_via_on_its_own_line(self):
        plan = {"vias": [{"net": "A", "at_mm": [20, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "A", "at_mm": [30, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "GND", "at_mm": [22.0, 35.45], "padstack": "VIA_D0.2_P0.4"}],  # its pad on the guard, 1.5 mm in from its end
                "lanes": [{"net": "A", "layer": 3, "width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5}],
                "guards": [{"nets": ["A"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.15}]}
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            preview = E.TraceCreate().preview(context("trace create", {"file": self.plan_file(plan)}))
        self.assertNotIn("joined to a pin or via", preview["action"])  # the via already touches the guard: a join would run back over it

    def test_a_guard_end_on_a_via_of_its_net_is_joined_to_it(self):
        plan = {"vias": [{"net": "A", "at_mm": [20, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "A", "at_mm": [30, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "GND", "at_mm": [20.55, 35.4], "padstack": "VIA_D0.2_P0.4"}],  # its pad covers the guard's end
                "lanes": [{"net": "A", "layer": 3, "width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5}],
                "guards": [{"nets": ["A"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.15}]}
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            preview = E.TraceCreate().preview(context("trace create", {"file": self.plan_file(plan)}))
        self.assertTrue(preview["can_apply"], preview.get("blocked_by"))  # drawn on to the via's centre, clear of A
        self.assertIn("guards and rings: 1 ends joined to a pin or via of their net nearby", preview["action"])

    def test_vias_along_a_guard_held_by_a_via_keep_the_pitch_as_drawn(self):
        plan = {"vias": [{"net": "A", "at_mm": [20, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "A", "at_mm": [30, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "GND", "at_mm": [20.55, 35.4], "padstack": "VIA_D0.2_P0.4"}],  # the guard's end runs on to it
                "lanes": [{"net": "A", "layer": 3, "width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5}],
                "guards": [{"nets": ["A"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.15, "via_pitch_mm": 2, "padstack": "VIA_D0.2_P0.4"}]}
        requirements = self.folder / "requirements.json"
        requirements.write_text(json.dumps({"requirements": [{"kind": "via_pitch", "net": "GND", "max_mm": 2}]}), encoding="utf-8")
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            found = E.TraceCheck().read(context("trace check", {"file": self.plan_file(plan), "requirements": str(requirements)}))
        row = found["requirements"][0]
        self.assertTrue(row["ok"], row)  # measured on the guards as drawn, run on to the via

    def test_an_end_that_joins_nothing_is_named_with_what_blocks_it_and_listed_in_the_result(self):
        plan = {"traces": [{"net": "B", "layer": 3, "width_mm": 0.1, "points_mm": [[19, 31], [19, 39.5]]}],  # a wall before the via
                "vias": [{"net": "A", "at_mm": [20, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "A", "at_mm": [30, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "GND", "at_mm": [18.0, 35.25], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "B", "at_mm": [19, 31], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "B", "at_mm": [19, 39.5], "padstack": "VIA_D0.2_P0.4"}],
                "lanes": [{"net": "A", "layer": 3, "width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5}],
                "guards": [{"nets": ["A"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.15}]}
        inputs = {"file": self.plan_file(plan)}
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            found = E.TraceCheck().read(context("trace check", inputs))
            facts = E.TraceCreate().run(context("trace create", inputs), None)
        walled = next(row for row in found["free_ends"] if row["end_mm"] == [20.5, 35.25])  # the end in line with the ground via
        self.assertEqual(walled["blocked_by"], ["B"])
        self.assertLess(walled["gap_mm"], walled["needed_mm"])
        self.assertEqual(walled["at_mm"][1], 35.25)
        self.assertTrue(any("no pin or via of GND" in row.get("reason", "") for row in found["free_ends"]))
        self.assertEqual(found["pour_only_count"], 2)  # no vias asked for: only a pour on layer 3 can ground either guard
        self.assertTrue(all("pour_at_mm" in row for row in found["pour_only"]))
        self.assertEqual(facts["result"]["free_end_count"], found["free_end_count"])
        self.assertEqual(len(facts["result"]["free_ends"]), found["free_end_count"])

    def test_the_read_back_reads_the_plan_as_it_was_drawn(self):
        class Store(dict):
            def set(self, key, value):
                self[key] = value

        store = Store()
        plan = {"vias": [{"net": "A", "at_mm": [20, 20], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "A", "at_mm": [30, 20], "padstack": "VIA_D0.2_P0.4"}],
                "lanes": [{"net": "A", "layer": 3, "width_mm": 0.1, "from_mm": [[20, 20]], "to_mm": [[30, 20]],
                           "edge_mm": [[2, 40], [48, 40]], "offset_mm": 5}],
                "guards": [{"nets": ["A"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.15, "via_pitch_mm": 2,
                            "padstack": "VIA_D0.2_P0.4"}]}
        inputs = {"file": self.plan_file(plan)}
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            apply = context("trace create", inputs, operation_id="op_Ab1")
            apply.store = store
            E.TraceCreate().run(apply, None)
            back = context("trace create", inputs, operation_id="op_Ab1", targets=[key("/traces/GND/3")], fields=["plan_drawn"])
            back.store = store
            observed = E.TraceCreate().read_back(back)["observed"]
        self.assertIn("trace-plan-op_ab1", store)  # the guards and vias as drawn: worked out again, the vias now there would move them
        self.assertEqual(observed, {key("/traces/GND/3"): {"plan_drawn": True}})
        with world(FakeApp(self.doc)):  # a save reads the field back from the same kept plan, with no operation of its own
            saved = E.L.read_keys(self.doc, BOARD, [key("/traces/GND/3"), key("/traces/A/3")], ["count", "plan_drawn"], store)
        self.assertEqual(saved["observed"][key("/traces/GND/3")]["plan_drawn"], True)
        self.assertEqual(saved["observed"][key("/traces/A/3")]["plan_drawn"], True)
        self.assertEqual(saved["unreadable"], [])

    def test_a_plan_that_breaks_the_rules_is_blocked_and_never_drawn(self):
        bad = {"traces": [{"net": "A", "layer": 1, "width_mm": 0.3, "points_mm": [[0, 0], [5, 0], [6, 0]]}],
               "vias": [{"net": "A", "at_mm": [6, 0], "padstack": "NOPE"}]}
        inputs = {"file": self.plan_file(bad)}
        with world(FakeApp(self.doc)):
            preview = E.TraceCreate().preview(context("trace create", inputs))
            with self.assertRaises(AdapterError) as caught:
                E.TraceCreate().apply(context("trace create", inputs))
        reasons = preview["blocked_by"]
        self.assertFalse(preview["can_apply"])
        self.assertIn("the central library holds no via padstack NOPE", reasons[0])
        self.assertIn("width 0.3 mm is outside", reasons[1])
        self.assertTrue(any("closer to a pad of B" in reason for reason in reasons))
        self.assertEqual(caught.exception.data.get("conflict"), "application_state")
        self.assertEqual(self.doc.drawn, [])

    def test_an_end_on_a_via_is_checked_as_drawn_on_to_the_via_centre(self):
        plan = {"traces": [{"net": "GND", "layer": 1, "width_mm": 0.5, "points_mm": [[40, 10], [45, 10]]},
                           {"net": "A", "layer": 1, "width_mm": 0.1, "points_mm": [[44, 12], [44, 10.52], [47, 10.52], [47, 12]]}],
                "vias": [{"net": "GND", "at_mm": [40, 10], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "GND", "at_mm": [45.15, 10.15], "padstack": "VIA_D0.2_P0.4"},  # its pad covers the end, off its centre
                         {"net": "A", "at_mm": [44, 12], "padstack": "VIA_D0.2_P0.4"},
                         {"net": "A", "at_mm": [47, 12], "padstack": "VIA_D0.2_P0.4"}]}
        rules = TP.Rules(net_class_of={}, widths={}, clearance=lambda *_args: 0.1)
        with world(FakeApp(self.doc)), mock.patch.object(E, "_trace_rules", lambda doc, path: rules):
            preview = E.TraceCreate().preview(context("trace create", {"file": self.plan_file(plan)}))
            plan["traces"].reverse()
            reversed_preview = E.TraceCreate().preview(context("trace create", {"file": self.plan_file(plan)}))
        self.assertEqual(len(preview["blocked_by"]), 1, preview["blocked_by"])  # as planned, A clears it by 0.22 mm
        self.assertIn("trace 1 of GND: closer to trace 2 of A", preview["blocked_by"][0])
        self.assertIn("where its end runs on to the centre of the pin or via at (45.15, 10.15) mm, at its width", preview["blocked_by"][0])
        self.assertIn("trace 1 of A: closer to trace 2 of GND in the plan", reversed_preview["blocked_by"][0])
        self.assertIn("where the trace 2 of GND's end runs on to the centre", reversed_preview["blocked_by"][0])

    def test_a_missing_or_malformed_plan_file(self):
        with world(FakeApp(self.doc)):
            for inputs, wanted in (({"file": str(self.folder / "none.json")}, "E_NOT_FOUND"), ({"file": self.plan_file("{")}, "E_VALIDATION"),
                                   ({"file": self.plan_file({"traces": []})}, "E_VALIDATION")):
                with self.subTest(inputs=inputs), self.assertRaises(AdapterError) as caught:
                    E.TraceCreate().preview(context("trace create", inputs))
                self.assertEqual(caught.exception.data["code"], wanted)


if __name__ == "__main__":
    unittest.main()


class ClearanceLookupTest(unittest.TestCase):
    def test_the_two_traces_of_a_differential_pair_keep_the_pair_spacing_not_the_clearance(self):
        from xpedition_cli.adapter.plan import rules as R
        current = R.Current(layers=["1"], nets=["DP", "DN", "SIG"], net_class_of={"DP": "90ohm", "DN": "90ohm", "SIG": "90ohm"},
                            net_classes={"90ohm": {"1": {"differential_spacing_mm": 0.1}}}, differential_pairs=[["DN", "DP"]],
                            clearances={"wide": {"1": {"trace_to_trace_mm": 0.15, "trace_to_via_mm": 0.12}}},
                            class_clearances={("90ohm", R.ALL_CLASSES): "wide"})
        clearance = E._clearance_lookup(current)
        self.assertEqual(clearance("DP", "DN", 1, "trace_to_trace"), 0.1)  # the pair's own spacing
        self.assertEqual(clearance("DP", "SIG", 1, "trace_to_trace"), 0.15)  # another net of the class
        self.assertEqual(clearance("DP", "DN", 1, "trace_to_via"), 0.12)  # a via keeps the rule


class ClassWidthTest(unittest.TestCase):
    def test_a_fan_out_join_or_stub_keeps_within_its_class(self):
        rules = TP.Rules({"LED_A": "LED"}, {"LED": {1: (0.1, 0.2)}}, None)
        self.assertEqual(E.within_class(rules, "LED_A", 1, 0.23), 0.2)  # a join as wide as the pads, more than the class allows
        self.assertEqual(E.within_class(rules, "LED_A", 1, 0.08), 0.1)
        self.assertEqual(E.within_class(rules, "LED_A", 3, 0.23), 0.23)  # no limits given on that layer
        self.assertEqual(E.within_class(rules, "OTHER", 1, 0.23), 0.23)


class CentreLineTest(unittest.TestCase):
    def test_an_arc_trace_is_followed_along_its_arc_not_through_its_centre(self):
        # a quarter arc of radius 2 about (0, 0) from (2, 0) to (0, 2): its row in the middle is the centre
        line = E._centre_line([[2.0, 0.0, 0.0], [0.0, 0.0, -2.0], [0.0, 2.0, 0.0]])
        self.assertTrue(all(abs(math.hypot(x, y) - 2.0) < 1e-9 for x, y in line))
        self.assertNotIn((0.0, 0.0), line)
