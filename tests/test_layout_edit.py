"""The board edits of adapter/layout_edit.py on fake COM objects."""
from __future__ import annotations

import json
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
        doc.vias.append(Item(doc.vias, Net=SimpleNamespace(Name=net)))
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
        with world(FakeApp(self.doc)), mock.patch.object(E, "_via_row", unreadable),                 mock.patch.object(E, "_padstack_pad_radius", lambda stack, scale: 0.2):
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
        self.patches = [mock.patch.object(E, "_copper_nets", lambda doc, side, box: ["GND"] if box[0] < 5 else []),
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
        copper = TP.Copper(layer_count=4, nets={"A", "B", "GND"}, pads=pads)
        rules = TP.Rules(net_class_of={}, widths={"(Default)": {layer: (0.1, 0.2) for layer in range(1, 5)}},
                         clearance=lambda a, b, layer, kind: 0.1)
        self.patches = [mock.patch.object(E, "_copper", lambda doc: copper),
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
        self.assertEqual(facts["result"], {"traces": 2, "vias": 1, "nets": [{"net": "A", "routed": True, "opens": 0}]})
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
        self.assertEqual(self.doc.drawn[1][3], [(3.0, 0.0), (3.1, 0.05), (10.0, 0.0)])  # joined to the via's centre
        self.assertEqual(facts["result"]["nets"], [{"net": "A", "routed": True, "opens": 0}])

    def test_a_rounded_corner_reaches_layout_as_an_arc(self):
        plan = {"traces": [{"net": "A", "layer": 1, "width_mm": 0.1, "points_mm": [[0, 0], [3, 0, 1.0], [3, 3]]}],
                "vias": [{"net": "A", "at_mm": [3, 3], "padstack": "VIA_D0.2_P0.4"}]}
        with world(FakeApp(self.doc)), mock.patch.object(E.L, "find_net",
                                                         lambda doc, name: SimpleNamespace(Name=name, NumberOfOpens=0, IsRouted=True)):
            E.TraceCreate().run(context("trace create", {"file": self.plan_file(plan)}), None)
        self.assertEqual([tuple(round(v, 6) for v in p) for p in self.doc.drawn[0][3]], [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (3.0, 1.0), (3.0, 3.0)])
        self.assertEqual(self.doc.radii[0], [0.0, 0.0, -1.0, 0.0, 0.0])  # the centre row carries the radius, counter-clockwise

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

    def test_a_missing_or_malformed_plan_file(self):
        with world(FakeApp(self.doc)):
            for inputs, wanted in (({"file": str(self.folder / "none.json")}, "E_NOT_FOUND"), ({"file": self.plan_file("{")}, "E_VALIDATION"),
                                   ({"file": self.plan_file({"traces": []})}, "E_VALIDATION")):
                with self.subTest(inputs=inputs), self.assertRaises(AdapterError) as caught:
                    E.TraceCreate().preview(context("trace create", inputs))
                self.assertEqual(caught.exception.data["code"], wanted)


if __name__ == "__main__":
    unittest.main()
