"""The board-changing Layout commands (adapter/layout_board.py) on fake COM objects: forward
annotation, the outline, placement, plane shapes and the autorouter."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from test_layout_fakes import (  # noqa: E402
    BOARD,
    Geometry,
    Collection,
    Extrema,
    FakeApp,
    FakeAssignment,
    FakeComError,
    FakeComponent,
    FakeDoc,
    FakeIntegration,
    FakeRoutePass,
    Outline,
    Progress,
    context,
    world,
)

from xpedition_cli._driver import AdapterError  # noqa: E402
from xpedition_cli.adapter import base  # noqa: E402
from xpedition_cli.adapter import layout as L  # noqa: E402
from xpedition_cli.adapter import layout_board as B  # noqa: E402
from xpedition_cli.adapter.plan import outline as plan_outline  # noqa: E402
from xpedition_cli.adapter.plan import routing as plan_routing  # noqa: E402
from xpedition_cli.adapter.platform import com, dialogs  # noqa: E402

PROJECT = """SECTION iCDB
LIST Designs
VALUE "Demo"
ENDLIST
ENDSECTION
SECTION Demo
KEY ConfigType "PCB"
KEY PCBDesignPath "PCB\\Demo.pcb"
ENDSECTION
"""


def key(domain: str) -> str:
    return base.key(BOARD, domain)


def code(caught) -> str:
    return caught.exception.data["code"]


class NativeDispatch:
    """``_oleobj_`` of ProjectIntegration without type information: a method found by name."""

    def __init__(self, answer=True, fail=None) -> None:
        self.calls = []
        self.answer, self.fail = answer, fail

    def GetIDsOfNames(self, name):  # noqa: N802
        assert name == "ForwardAnnotate"
        return 2

    def InvokeTypes(self, member, lcid, kind, result_type, arg_types, *args):  # noqa: N802
        self.calls.append((member, kind, result_type, arg_types, args))
        if self.fail:
            raise self.fail
        return self.answer


class Integration:
    def __init__(self, status=1, allowed=True, answer=True, fail=None):
        self.ForwardAnnotationStatus = status
        self.IsForwardAnnotationAllowed = allowed
        self._oleobj_ = NativeDispatch(answer, fail)

    @property
    def ForwardAnnotate(self):  # noqa: N802
        raise AssertionError("a property probe must not run a method that changes the board")


# ------------------------------------------------------------------ forward annotation


class ForwardAnnotationTest(unittest.TestCase):
    def setUp(self):
        self.handler = B.ForwardAnnotation()
        self.designer = mock.patch.object(B, "designer_projects", lambda: [])
        self.designer.start()

    def tearDown(self):
        self.designer.stop()

    def test_in_synch_previews_nothing_to_do(self):
        doc = FakeDoc()
        doc.ProjectIntegration = FakeIntegration(status=2)
        with world(FakeApp(doc)):
            out = self.handler.preview(context("forward-annotation run"))
        self.assertEqual(out["changes"], [])
        self.assertIn("already in sync", out["action"])

    def test_required_previews_the_status_change(self):
        with world(FakeApp(FakeDoc())):
            out = self.handler.preview(context("forward-annotation run"))
        self.assertEqual(out["changes"], [base.change(key("/forward-annotation"), "status", "required", "in_synch")])

    def test_no_data_and_not_allowed_are_refused(self):
        for integration in (FakeIntegration(status=3), FakeIntegration(allowed=False)):
            doc = FakeDoc()
            doc.ProjectIntegration = integration
            with world(FakeApp(doc)):
                out = self.handler.preview(context("forward-annotation run"))
            self.assertFalse(out["can_apply"])
            self.assertEqual(len(out["blocked_by"]), 1)

    def test_designer_holding_the_project_is_refused_before_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            prj = Path(tmp) / "Demo.prj"
            prj.write_text(PROJECT, encoding="utf-8")
            board = Path(tmp) / "PCB" / "Demo.pcb"
            board.parent.mkdir()
            board.write_bytes(b"board")
            rows = [{"pid": 4120, "state": "ready", "documents": [str(prj)]}]
            with world(FakeApp(FakeDoc(str(board)))), mock.patch.object(B, "designer_projects", lambda: rows):
                out = self.handler.preview(context("forward-annotation run", document=str(board)))
            self.assertFalse(out["can_apply"])
            reason = " ".join(out["blocked_by"])
            self.assertIn("doc save", reason)
            self.assertIn("doc close", reason)
            self.assertIn("designer-4120-", reason)
            busy = [{"pid": 4120, "state": "blocked", "documents": []}]
            with world(FakeApp(FakeDoc(str(board)))), mock.patch.object(B, "designer_projects", lambda: busy):
                self.assertFalse(self.handler.preview(context("forward-annotation run", document=str(board)))["can_apply"])
            other = [{"pid": 4120, "state": "ready", "documents": [str(Path(tmp) / "Other.prj")]}]
            with world(FakeApp(FakeDoc(str(board)))), mock.patch.object(B, "designer_projects", lambda: other):
                self.assertTrue(self.handler.preview(context("forward-annotation run", document=str(board)))["changes"])

    def test_the_project_is_found_from_the_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Demo.prj").write_text(PROJECT, encoding="utf-8")
            (Path(tmp) / "Spare.prj").write_text("SECTION iCDB\nENDSECTION\n", encoding="utf-8")
            board = Path(tmp) / "PCB" / "Demo.pcb"
            self.assertTrue(L.same_path(B.project_of(str(board)), Path(tmp) / "Demo.prj"))
            self.assertIsNone(B.project_of(str(Path(tmp) / "PCB" / "Unknown.pcb")))

    def test_designer_without_session_data_is_read_lightly(self):
        app = SimpleNamespace(GetProjectData=SimpleNamespace(GetProjectFilePath="D:/work/demo/Demo.prj"))
        with mock.patch.object(com, "active_object", lambda progids: app), mock.patch.object(com, "call_with_timeout", lambda fn, t: fn()):
            self.assertEqual(B._designer_by_com(), [{"pid": None, "state": "ready", "documents": ["D:/work/demo/Demo.prj"]}])
        with mock.patch.object(com, "active_object", lambda progids: None), mock.patch.object(com, "call_with_timeout", lambda fn, t: fn()):
            self.assertEqual(B._designer_by_com(), [])

    def _board(self, tmp: str, integration: Integration) -> tuple[FakeDoc, Path]:
        board = Path(tmp) / "PCB" / "Demo.pcb"
        (board.parent / "LogFiles").mkdir(parents=True)
        doc = FakeDoc(str(board))
        doc.ProjectIntegration = integration
        return doc, board

    def test_run_dispatches_forward_annotate_once_as_a_method_and_saves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            integration = Integration()
            doc, board = self._board(tmp, integration)
            log = board.parent / "LogFiles" / "ForwardAnnotation.txt"

            def annotate(*args):
                integration.ForwardAnnotationStatus = 2
                log.write_text("2 nets were found containing 4 pins\n2 components were found\nWARNING: a note\n"
                               "Forward-Annotation on the Layout Design has been successfully completed\n", encoding="utf-8")
                return True

            integration._oleobj_.InvokeTypes = lambda *args: (integration._oleobj_.calls.append(args), annotate())[1]
            progress = Progress()
            with world(FakeApp(doc)):
                out = self.handler.run(context("forward-annotation run", document=str(board)), progress)
        self.assertEqual(len(integration._oleobj_.calls), 1)
        member, lcid, kind, result_type, arg_types = integration._oleobj_.calls[0]
        self.assertEqual((member, kind, result_type, arg_types), (2, com.DISPATCH_METHOD, (com.VT_BOOL, 0), ()))
        self.assertEqual((out["change"], out["persistence"]), ("applied", "unchanged"))
        self.assertEqual(out["result"], {"outcome": "annotated", "components": 2, "nets": 2, "pins": 4, "errors": [],
                                         "warnings": ["WARNING: a note"]})
        self.assertNotIn(("Save",), doc.calls)
        self.assertEqual(progress.steps[-1], (3, 3, "steps"))

    def test_the_annotate_prompt_is_answered_yes_during_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc, board = self._board(tmp, Integration())
            from test_layout_fakes import FakeAnswerer

            with world(FakeApp(doc)):
                self.handler.run(context("forward-annotation run", document=str(board)), Progress())
            self.assertEqual(FakeAnswerer.made[0].rules, dialogs.LAYOUT_PROMPTS_ANNOTATE)

    def test_a_failed_annotation_is_not_repeated_and_its_outcome_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            integration = Integration(fail=FakeComError(text="正向标注失败"))
            doc, board = self._board(tmp, integration)
            (board.parent / "LogFiles" / "ForwardAnnotation.txt").write_text("ERROR: cell not found\n", encoding="utf-8")
            with world(FakeApp(doc)), self.assertRaises(AdapterError) as caught:
                self.handler.run(context("forward-annotation run", document=str(board)), Progress())
        self.assertEqual(len(integration._oleobj_.calls), 1)
        data = caught.exception.data
        self.assertEqual((data["dispatch_state"], data["change"]), ("dispatched", "unknown"))
        self.assertIn("1 error lines", str(caught.exception))
        self.assertNotIn("cell not found", str(caught.exception))  # Layout's text stays out of the message

    def test_a_stale_log_gives_no_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            integration = Integration()
            doc, board = self._board(tmp, integration)
            log = board.parent / "LogFiles" / "ForwardAnnotation.txt"
            log.write_text("9 components were found\n", encoding="utf-8")
            old = time.time() - 3600
            os.utime(log, (old, old))
            with world(FakeApp(doc)):
                out = self.handler.run(context("forward-annotation run", document=str(board)), Progress())
        self.assertIsNone(out["result"]["components"])

    def test_a_board_in_synch_at_run_time_changes_nothing(self):
        integration = Integration(status=2)
        doc = FakeDoc()
        doc.ProjectIntegration = integration
        with world(FakeApp(doc)):
            out = self.handler.run(context("forward-annotation run"), Progress())
        self.assertEqual((out["change"], out["result"]["outcome"]), ("not_applied", "in_synch"))
        self.assertEqual(integration._oleobj_.calls, [])

    def test_false_without_synch_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc, board = self._board(tmp, Integration(answer=False))
            with world(FakeApp(doc)), self.assertRaises(AdapterError) as caught:
                self.handler.run(context("forward-annotation run", document=str(board)), Progress())
        self.assertEqual(caught.exception.data["change"], "unknown")

    def test_read_back_reads_the_status(self):
        doc = FakeDoc()
        doc.ProjectIntegration = FakeIntegration(status=2)
        ctx = context("forward-annotation run", targets=[key("/forward-annotation")], fields=["status"])
        with world(FakeApp(doc)):
            out = self.handler.read_back(ctx)
        self.assertEqual(out, {"observed": {key("/forward-annotation"): {"status": "in_synch"}}, "unreadable": []})


# ------------------------------------------------------------------ the outline


class OutlineTest(unittest.TestCase):
    def setUp(self):
        self.handler = B.OutlineUpdate()

    def test_preview_compares_the_current_outline_with_the_requested_one(self):
        with world(FakeApp(FakeDoc(width=50.0, height=40.0))):
            out = self.handler.preview(context("pcb update", {"width_mm": 70.0, "height_mm": 48.0, "radius_mm": 3.0}))
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in out["changes"]],
                         [("width_mm", 50.0, 70.0), ("height_mm", 40.0, 48.0), ("radius_mm", 0.0, 3.0)])
        self.assertEqual(out["targets"][0]["key"], key("/outline"))

    def test_both_sides_are_needed_and_the_plan_checks_them(self):
        for inputs in ({"width_mm": 70.0}, {"width_mm": 70.0, "height_mm": 48.0, "radius_mm": 30.0}, {"width_mm": 0.4, "height_mm": 9.0}):
            with world(FakeApp(FakeDoc())), self.assertRaises(AdapterError) as caught:
                self.handler.preview(context("pcb update", inputs))
            self.assertEqual((code(caught), caught.exception.data["dispatch_state"]), ("E_VALIDATION", "not_started"))

    def test_apply_writes_the_three_contours_and_read_back_matches(self):
        doc = FakeDoc()
        inputs = {"width_mm": 70.0, "height_mm": 48.0, "radius_mm": 3.0}
        with world(FakeApp(doc)):
            preview = self.handler.preview(context("pcb update", inputs))
            facts = self.handler.apply(context("pcb update", inputs))
            back = self.handler.read_back(context("pcb update", inputs, targets=[key("/outline")],
                                                  fields=["height_mm", "radius_mm", "width_mm"]))
        self.assertEqual([call[0] for call in doc.calls], ["PutBoardOutline", "PutRouteBorder", "PutManufacturingOutline"])
        board, border, manufacturing = doc.calls
        self.assertEqual((board[1], board[3], board[4]), (13, 0.15, L.UNIT_MM))
        self.assertEqual(manufacturing[3], L.UNIT_MM)
        self.assertEqual(board[2][2][1], -3.0)  # the arc centre carries a negative radius
        self.assertEqual(border[2][0][0], round(70.0 - 3.0, 4))  # 0.3 mm inside, corners 2.7 mm
        self.assertEqual(min(border[2][1]), plan_outline.INSET_MM)
        self.assertEqual(facts["result"], {"width_mm": 70.0, "height_mm": 48.0, "radius_mm": 3.0, "route_border_inset_mm": 0.3})
        self.assertEqual((facts["change"], facts["persistence"]), ("applied", "unchanged"))
        observed = back["observed"][key("/outline")]
        for change in preview["changes"]:
            self.assertAlmostEqual(observed[change["field"]], change["after"], places=3)

    def test_without_radius_the_current_corners_stay(self):
        doc = FakeDoc()
        with world(FakeApp(doc)):
            self.handler.apply(context("pcb update", {"width_mm": 70.0, "height_mm": 48.0, "radius_mm": 3.0}))
            out = self.handler.preview(context("pcb update", {"width_mm": 80.0, "height_mm": 50.0}))
            facts = self.handler.apply(context("pcb update", {"width_mm": 80.0, "height_mm": 50.0}))
        radius = [c for c in out["changes"] if c["field"] == "radius_mm"][0]
        self.assertAlmostEqual(radius["after"], 3.0, places=3)
        self.assertAlmostEqual(facts["result"]["radius_mm"], 3.0, places=3)

    def test_an_outline_whose_route_border_would_cut_placed_parts_is_blocked(self):
        # Layout's preventive DRC refuses such a route border after the board outline is written
        parts = [FakeComponent("R1", 3.0, 1.5, placed=True, x=14.0, y=42.5), FakeComponent("C1", placed=False)]
        with world(FakeApp(FakeDoc(width=70.0, height=48.0, components=parts))):
            with self.assertRaises(AdapterError) as caught:
                self.handler.preview(context("pcb update", {"width_mm": 61.0, "height_mm": 41.0}))
            fits = self.handler.preview(context("pcb update", {"width_mm": 70.0, "height_mm": 44.0}))
        message = str(caught.exception)
        self.assertEqual((code(caught), caught.exception.data["conflict"]), ("E_CONFLICT", "application_state"))
        self.assertIn("R1", message)
        self.assertIn("such as 61 x 43.6 mm", message)
        self.assertNotIn("C1", message)
        self.assertTrue(fits["can_apply"])

    def test_a_setter_that_fails_after_the_first_leaves_a_partial_change(self):
        doc = FakeDoc()
        doc.PutRouteBorder = mock.Mock(side_effect=FakeComError(text="bad contour"))
        with world(FakeApp(doc)):
            facts = self.handler.apply(context("pcb update", {"width_mm": 30.0, "height_mm": 20.0}))
        self.assertEqual(facts["change"], "partial")
        doc = FakeDoc()
        doc.PutBoardOutline = mock.Mock(side_effect=FakeComError(text="bad contour"))
        with world(FakeApp(doc)), self.assertRaises(AdapterError) as caught:
            self.handler.apply(context("pcb update", {"width_mm": 30.0, "height_mm": 20.0}))
        self.assertEqual(caught.exception.data["dispatch_state"], "dispatched")


# ------------------------------------------------------------------ placement


def parts() -> list[FakeComponent]:
    """An IC with two capacitors and a connector, unplaced as forward annotation leaves them."""
    return [
        FakeComponent("U101", 5.0, 4.0, pins={"1": (-2.0, 1.0, "VCC"), "2": (-2.0, -1.0, "GND"), "3": (2.0, 0.0, "SIG")}, cell="SOIC8"),
        FakeComponent("C101", 1.6, 0.8, offset=(0.1, 0.0), pins={"1": (-0.7, 0.0, "VCC"), "2": (0.7, 0.0, "GND")}, cell="C0603"),
        FakeComponent("C102", 1.6, 0.8, pins={"1": (-0.7, 0.0, "VCC"), "2": (0.7, 0.0, "GND")}, cell="C0603"),
        FakeComponent("J101", 2.5, 7.5, pins={"1": (0.0, 2.5, "SIG"), "2": (0.0, 0.0, "GND"), "3": (0.0, -2.5, "VCC")}, cell="HDR3"),
    ]


class PlacementTest(unittest.TestCase):
    def setUp(self):
        self.handler = B.PlacementRun()

    def _doc(self, components=None, **kwargs) -> FakeDoc:
        return FakeDoc(components=components if components is not None else parts(), nets=["VCC", "GND", "SIG"], **kwargs)

    def test_zones_come_from_the_design_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            design = Path(tmp) / "design.json"
            design.write_text(json.dumps({"sheets": [{"number": 2, "zone": "POWER", "blocks": [{"refdes": "U101"}, {"refdes": "C101"}]},
                                                     {"number": 3, "zone": "EMPTY", "blocks": []}]}), encoding="utf-8")
            self.assertEqual(B.design_zones(str(design)), {"1": "POWER"})
            with self.assertRaises(AdapterError) as caught:
                B.design_zones(str(Path(tmp) / "none.json"))
            self.assertEqual(code(caught), "E_NOT_FOUND")
        self.assertEqual(B.design_zones(None), {})

    def test_preview_of_unplaced_parts_changes_nothing_and_previews_placed(self):
        doc = self._doc()
        with world(FakeApp(doc)):
            out = self.handler.preview(context("placement run"))
        self.assertTrue(all(not part.calls for part in doc.components))
        self.assertEqual({(c["field"], c["before"], c["after"]) for c in out["changes"]}, {("placed", False, True)})
        self.assertEqual(len(out["changes"]), 4)
        self.assertEqual(out["targets"], [base.target(BOARD, "component", "/components", label="4 components")])
        self.assertIn("planned when the change runs", out["action"])

    def test_apply_measures_parks_lifts_and_places_and_restores_the_drc_switch(self):
        doc = self._doc()
        with world(FakeApp(doc)):
            facts = self.handler.run(context("placement run"), Progress())
            refs = [p.RefDes for p in doc.components]
            back = self.handler.read_back(context("placement run", targets=[key(f"/components/{r}") for r in refs], fields=["placed"]))
        self.assertEqual((facts["change"], facts["result"]["placed"]), ("applied", 4))
        for part in doc.components:
            places = [call for call in part.calls if call[0] == "Place"]
            self.assertEqual(len(places), 3, part.calls)  # measured, parked below the board, then placed
            self.assertLess(places[1][2], 0.0)  # parked below the outline
            self.assertTrue(part.Placed)
        self.assertEqual(doc.drc_history, [False, True])
        self.assertTrue(all(v == {"placed": True} for v in back["observed"].values()))
        self.assertEqual([call[1] for call in doc.calls if call[0] == "PutFabricationLayerText"], ["SHEET 1"])
        self.assertEqual(facts["result"]["clusters"][0]["name"], "U101")

    def test_a_placed_board_is_previewed_with_positions_that_apply_then_stores(self):
        doc = self._doc()
        with world(FakeApp(doc)):
            self.handler.run(context("placement run"), Progress())
            for part in doc.components:
                part.calls.clear()
            preview = self.handler.preview(context("placement run", {"all": True}))
            self.assertTrue(all(not part.calls for part in doc.components))  # measured where they stand
            self.handler.run(context("placement run", {"all": True}), Progress())
            targets = sorted({c["target"] for c in preview["changes"]})
            back = self.handler.read_back(context("placement run", targets=targets, fields=["rotation_deg", "x_mm", "y_mm"]))
        self.assertEqual({c["field"] for c in preview["changes"]}, {"x_mm", "y_mm", "rotation_deg"})
        for change in preview["changes"]:
            self.assertAlmostEqual(back["observed"][change["target"]][change["field"]], change["after"], places=3)

    def test_parts_beyond_the_outline_are_refused_before_anything_moves(self):
        doc = self._doc(width=6.0, height=5.0)
        with world(FakeApp(doc)), self.assertRaises(AdapterError) as caught:
            self.handler.run(context("placement run"), Progress())
        self.assertEqual((code(caught), caught.exception.data["knowledge"]), ("E_VALIDATION", ["layout-placement-drc"]))
        self.assertTrue(all(not part.Placed for part in doc.components))  # the parked parts went back
        self.assertNotIn("PutFabricationLayerText", [call[0] for call in doc.calls])

    def test_a_preview_of_placed_parts_beyond_the_outline_is_blocked(self):
        doc = self._doc()
        with world(FakeApp(doc)):
            self.handler.run(context("placement run"), Progress())
            doc.BoardOutline = Outline([[0, 6, 6, 0, 0], [0, 0, 5, 5, 0], [0.0] * 5])
            out = self.handler.preview(context("placement run", {"all": True}))
        self.assertFalse(out["can_apply"])
        self.assertIn("pcb update", out["blocked_by"][0])

    def test_a_measurement_that_fails_puts_the_parked_parts_back(self):
        doc = self._doc()
        with world(FakeApp(doc)), mock.patch.object(B, "_plan", mock.Mock(side_effect=FakeComError(text="nets unreadable"))):
            with self.assertRaises(AdapterError) as caught:
                self.handler.run(context("placement run"), Progress())
        self.assertEqual(code(caught), "E_NATIVE_FAILURE")
        self.assertTrue(all(not part.Placed for part in doc.components))

    def test_routing_is_deleted_first_when_there_is_some(self):
        doc = self._doc()
        deleted = []
        doc.traces = [SimpleNamespace(Delete=lambda: deleted.append("trace"))]
        doc.vias = [SimpleNamespace(Delete=lambda: deleted.append("via"))]
        with world(FakeApp(doc)):
            preview = self.handler.preview(context("placement run"))
            facts = self.handler.run(context("placement run"), Progress())
        routing = [c for c in preview["changes"] if c["target"] == key("/routing")]
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in routing], [("traces", 1, 0), ("vias", 1, 0)])
        self.assertEqual(deleted, ["trace", "via"])
        self.assertEqual(facts["result"]["routing_removed"], {"traces": 1, "vias": 1})

    def test_a_refused_placement_halfway_is_partial(self):
        doc = self._doc()
        with world(FakeApp(doc)):
            original = doc.components[-1].Place

            def refuse_last(x, y, rotation, top, a, unit, b):
                if len([c for c in doc.components[-1].calls if c[0] == "Place"]) >= 2:
                    raise FakeComError(text="DRC violation")
                original(x, y, rotation, top, a, unit, b)

            doc.components[-1].Place = refuse_last
            facts = self.handler.run(context("placement run"), Progress())
        self.assertEqual(facts["change"], "partial")
        self.assertIn("--all", facts["native_message"])

    def test_nothing_to_place(self):
        doc = self._doc([FakeComponent("R1", placed=True, x=5.0, y=5.0)])
        with world(FakeApp(doc)):
            preview = self.handler.preview(context("placement run"))
            facts = self.handler.run(context("placement run"), Progress())
        self.assertEqual(preview["changes"], [])
        self.assertEqual(facts["change"], "not_applied")


# ------------------------------------------------------------------ plane shapes


class PlaneTest(unittest.TestCase):
    def setUp(self):
        self.handler = B.PlaneCreate()

    def test_preview_refuses_an_unknown_net_layer_or_existing_plane(self):
        doc = FakeDoc(nets=["GND"])
        cases = [({"net": "VCC", "layer": 2}, "E_NOT_FOUND"), ({"net": "GND", "layer": 9}, "E_VALIDATION"),
                 ({"net": "GND", "layer": 2, "margin_mm": 30.0}, "E_VALIDATION")]
        for inputs, expected in cases:
            with world(FakeApp(doc)), self.assertRaises(AdapterError) as caught:
                self.handler.preview(context("plane create", inputs))
            self.assertEqual(code(caught), expected)
        doc.shapes.append(SimpleNamespace(Layer=2, Net=SimpleNamespace(Name="GND")))
        with world(FakeApp(doc)), self.assertRaises(AdapterError):
            self.handler.preview(context("plane create", {"net": "GND", "layer": 2}))

    def test_apply_pours_the_inset_rectangle_and_switches_it_to_dynamic(self):
        doc = FakeDoc(nets=["GND"], width=50.0, height=40.0)
        inputs = {"net": "GND", "layer": 2}
        with world(FakeApp(doc)):
            preview = self.handler.preview(context("plane create", inputs))
            facts = self.handler.apply(context("plane create", inputs))
            back = self.handler.read_back(context("plane create", inputs, targets=[key("/planes/GND/2")], fields=["exists", "state", "hatch"]))
        call = doc.calls[0]
        self.assertEqual(call[:3], ("PutPlaneShape", 2, 5))
        self.assertEqual(call[3], ((1.0, 49.0, 49.0, 1.0, 1.0), (1.0, 1.0, 39.0, 39.0, 1.0), (0.0,) * 5))
        self.assertEqual(call[4:], ("GND", False, 0, 0.0, 0.0, None, L.UNIT_MM))
        self.assertEqual(doc.assignments[0].states, [L.PLANE_DRAFT, L.PLANE_DYNAMIC])
        self.assertEqual(facts["result"], {"net": "GND", "layer": 2, "within": "1 mm inside the board outline", "hatch": "solid",
                                           "generated": False})
        self.assertIn("1 mm inside the board outline", preview["action"])
        self.assertEqual(back["observed"][key("/planes/GND/2")], {c["field"]: c["after"] for c in preview["changes"]})

    def test_a_rounded_outline_gives_rounded_corners_less_the_margin(self):
        doc = FakeDoc(nets=["GND"])
        doc.BoardOutline = Outline(plan_outline.rounded(0, 0, 40, 30, 3.0))
        with world(FakeApp(doc)):
            self.handler.apply(context("plane create", {"net": "GND", "layer": 3, "margin_mm": 1.0}))
        points = doc.calls[0][3]
        self.assertEqual(doc.calls[0][2], 13)
        self.assertEqual(points[2][1], -2.0)

    def test_an_outline_that_is_no_rectangle_gives_the_route_border_and_refuses_a_margin(self):
        doc = FakeDoc(nets=["GND"])
        # an L-shaped flex outline, and Layout's route border 0.2 mm inside it; its arcs go as chords
        doc.BoardOutline = Outline([[0, 10, 10, 4, 4, 0, 0], [0, 0, 4, 4, 30, 30, 0], [0.0] * 7])
        border = [[0.2, 9.8, 9.8, 8.8, 8.8, 3.8, 3.8, 0.2, 0.2], [0.2, 0.2, 2.8, 2.8, 3.8, 3.8, 29.8, 29.8, 0.2],
                  [0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0]]  # one corner rounded with a radius of 1 mm
        doc.RouteBorder = Outline(border)
        with world(FakeApp(doc)):
            preview = self.handler.preview(context("plane create", {"net": "GND", "layer": 2}))
            facts = self.handler.apply(context("plane create", {"net": "GND", "layer": 2}))
            with self.assertRaises(AdapterError) as caught:
                self.handler.preview(context("plane create", {"net": "GND", "layer": 3, "margin_mm": 0.5}))
        self.assertIn("inside the route border", preview["action"])
        points = doc.calls[0][3]
        self.assertEqual([(points[0][i], points[1][i]) for i in range(3)], [(0.2, 0.2), (9.8, 0.2), (9.8, 2.8)])
        self.assertGreater(len(points[0]), 9)  # the arc as chords
        self.assertEqual(set(points[2]), {0.0})
        self.assertEqual(facts["result"]["within"], "inside the route border")
        self.assertEqual(code(caught), "E_VALIDATION")
        self.assertIn("--margin does not apply", str(caught.exception))

    def test_no_assignment_after_the_shape_is_partial(self):
        doc = FakeDoc(nets=["GND"])
        doc.FindPlaneAssignment = lambda layer, net: None
        with world(FakeApp(doc)):
            facts = self.handler.apply(context("plane create", {"net": "GND", "layer": 2}))
        self.assertEqual(facts["change"], "partial")


# ------------------------------------------------------------------ the autorouter


class PlaneHatchTest(unittest.TestCase):
    def test_a_hatched_plane_passes_its_pattern_and_reads_back(self):
        doc = FakeDoc(nets=["GND"])
        inputs = {"net": "GND", "layer": 2, "hatch": "orthogonal", "hatch_width_mm": 0.15, "hatch_distance_mm": 0.5}
        with world(FakeApp(doc)):
            preview = B.PlaneCreate().preview(context("plane create", inputs))
            facts = B.PlaneCreate().apply(context("plane create", inputs))
            back = B.PlaneCreate().read_back(context("plane create", targets=[key("/planes/GND/2")], fields=["exists", "state", "hatch"]))
            widths = B.PlaneCreate().read_back(context("plane create", targets=[key("/planes/GND/2")],
                                                       fields=["hatch_width_mm", "hatch_distance_mm"]))
        self.assertEqual(widths["observed"][key("/planes/GND/2")], {"hatch_width_mm": 0.15, "hatch_distance_mm": 0.5})
        plane_class = doc.shapes[0].PlaneClass  # the shape's own override of its plane class
        self.assertEqual(plane_class.HatchPattern, 5)  # crosshatch
        self.assertAlmostEqual(plane_class.HatchWidth * 0.0254, 0.15)  # the fake board counts in mils
        self.assertAlmostEqual(plane_class.HatchDistance * 0.0254, 0.5)
        self.assertIn("orthogonal hatch of 0.15 mm lines 0.5 mm apart", preview["action"])
        self.assertEqual(facts["result"]["hatch"], "orthogonal")
        self.assertEqual(back["observed"][key("/planes/GND/2")], {"exists": True, "state": "dynamic", "hatch": "orthogonal"})

    def test_a_band_beside_a_plane_is_cut_to_its_region_and_reads_back_by_it(self):
        doc = FakeDoc(nets=["GND"])
        band = {"net": "GND", "layer": 2, "hatch": "diagonal", "hatch_width_mm": 0.1, "hatch_distance_mm": 0.3,
                "from_y_mm": 10.0, "to_y_mm": 20.0}
        target = key("/planes/GND/2/,10,,20")
        with world(FakeApp(doc)):
            B.PlaneCreate().apply(context("plane create", {"net": "GND", "layer": 2, "to_y_mm": 10.0}))
            preview = B.PlaneCreate().preview(context("plane create", band))
            B.PlaneCreate().apply(context("plane create", band))
            back = B.PlaneCreate().read_back(context("plane create", targets=[target], fields=["exists", "hatch"]))
            with self.assertRaises(AdapterError) as caught:  # it would reach into the band
                B.PlaneCreate().preview(context("plane create", {**band, "from_y_mm": 15.0, "to_y_mm": 25.0}))
            with self.assertRaises(AdapterError) as wrong:
                B.PlaneCreate().preview(context("plane create", {**band, "from_y_mm": 20.0, "to_y_mm": 10.0}))
        self.assertEqual(len(doc.shapes), 2)
        ys = doc.shapes[1].Extrema
        self.assertEqual((ys.GetMinY(L.UNIT_MM), ys.GetMaxY(L.UNIT_MM)), (10.0, 20.0))  # cut to the band
        self.assertEqual(preview["targets"][0]["key"], target)
        self.assertIn("cut to y 10 to 20 mm", preview["action"])
        self.assertEqual(back["observed"][target], {"exists": True, "hatch": "diagonal"})
        self.assertEqual((code(caught), code(wrong)), ("E_CONFLICT", "E_VALIDATION"))

    def test_a_polygon_is_poured_as_given_inside_the_board(self):
        doc = FakeDoc(nets=["GND", "VBUS"])
        polygon = {"points_mm": [[2.0, 2.0], [9.0, 2.0], [9.0, 4.0], [4.0, 6.0], [2.0, 6.0]]}
        inputs = {"net": "VBUS", "layer": 1, "polygon": polygon}
        target = key("/planes/VBUS/1/2,2,9,6")
        with world(FakeApp(doc)):
            preview = B.PlaneCreate().preview(context("plane create", inputs))
            B.PlaneCreate().apply(context("plane create", inputs))
            back = B.PlaneCreate().read_back(context("plane create", targets=[target], fields=["exists", "hatch"]))
            refused = []
            for wrong in ({**inputs, "from_x_mm": 1.0},  # a polygon takes no region
                          {**inputs, "polygon": {"points_mm": [[20.0, 2.0], [60.0, 2.0], [60.0, 6.0]]}},  # past the outline
                          {**inputs, "polygon": {"points_mm": [[2.0, 2.0], [6.0, 6.0], [6.0, 2.0], [2.0, 6.0]]}},  # sides cross
                          {**inputs, "polygon": {"points_mm": [[2.0, 2.0], [4.0, 2.0]]}}):
                with self.assertRaises(AdapterError) as caught:
                    B.PlaneCreate().preview(context("plane create", wrong))
                refused.append(code(caught))
        rows = next(call[3] for call in doc.calls if call[0] == "PutPlaneShape")
        self.assertEqual(list(zip(rows[0], rows[1], strict=True)), [(2.0, 2.0), (9.0, 2.0), (9.0, 4.0), (4.0, 6.0), (2.0, 6.0), (2.0, 2.0)])
        self.assertEqual(preview["targets"][0]["key"], target)
        self.assertIn("as a polygon of 5 points over x 2 to 9, y 2 to 6 mm, 1 mm inside the board outline", preview["action"])
        self.assertEqual(back["observed"][target], {"exists": True, "hatch": "solid"})
        self.assertEqual(refused, ["E_VALIDATION"] * 4)

    def test_bends_pour_hatched_bands_over_the_bend_zones_and_solid_between(self):
        from xpedition_cli.adapter import layout_output as O
        from xpedition_cli.adapter.plan import board_metrics

        doc = FakeDoc(nets=["GND"])
        zones = [{"extents_mm": [0.0, 10.0, 60.0, 14.0], "along": "x", "marked_by": ["placement_keepout"]},
                 {"extents_mm": [0.0, 30.0, 60.0, 33.0], "along": "x", "marked_by": ["placement_keepout"]}]
        inputs = {"net": "GND", "layer": 3, "bends": True, "hatch": "diagonal", "hatch_width_mm": 0.1, "hatch_distance_mm": 0.3}
        with world(FakeApp(doc)), mock.patch.object(O, "flex_model", lambda doc: {}), \
                mock.patch.object(board_metrics, "bend_zones", lambda model: zones):
            preview = B.PlaneCreate().preview(context("plane create", inputs))
            facts = B.PlaneCreate().apply(context("plane create", inputs))
            band = key("/planes/GND/3/,10,,14")
            back = B.PlaneCreate().read_back(context("plane create", targets=[band, key("/planes/GND/3/,14,,30")], fields=["exists", "hatch"]))
            with self.assertRaises(AdapterError) as solid:
                B.PlaneCreate().preview(context("plane create", {"net": "GND", "layer": 3, "bends": True}))
        self.assertEqual([t["key"] for t in preview["targets"]],
                         [key(f"/planes/GND/3/{r}") for r in (",,,10", ",10,,14", ",14,,30", ",30,,33", ",33,,")])
        self.assertEqual([p["hatch"] for p in facts["result"]["parts"]], ["solid", "diagonal", "solid", "diagonal", "solid"])
        self.assertEqual(len(doc.shapes), 5)
        self.assertEqual(doc.shapes[1].PlaneClass.HatchPattern, 6)
        self.assertEqual(back["observed"][band], {"exists": True, "hatch": "diagonal"})
        self.assertEqual(back["observed"][key("/planes/GND/3/,14,,30")], {"exists": True, "hatch": "solid"})
        self.assertEqual(code(solid), "E_VALIDATION")  # the bend zones need a hatch
        self.assertIn("2 bend zone(s)", preview["action"])

    def test_bend_parts_need_bend_zones_along_one_axis(self):
        with self.assertRaises(AdapterError) as none:
            B.bend_parts([])
        with self.assertRaises(AdapterError) as mixed:
            B.bend_parts([{"extents_mm": [0, 1, 9, 2], "along": "x"}, {"extents_mm": [3, 0, 4, 9], "along": "y"}])
        self.assertEqual((code(none), code(mixed)), ("E_CONFLICT", "E_CONFLICT"))
        self.assertEqual(B.bend_parts([{"extents_mm": [5, 0, 6, 9], "along": "y"}]),
                         [((None, None, 5, None), False), ((5, None, 6, None), True), ((6, None, None, None), False)])

    def test_a_plane_is_cut_around_another_nets_shape_on_its_layer(self):
        doc = FakeDoc(nets=["GND", "VBUS"])
        power = {"net": "VBUS", "layer": 1, "polygon": {"points_mm": [[10.0, 10.0], [20.0, 10.0], [20.0, 20.0], [10.0, 20.0]]}}
        with world(FakeApp(doc)):
            B.PlaneCreate().apply(context("plane create", power))
            preview = B.PlaneCreate().preview(context("plane create", {"net": "GND", "layer": 1}))
            B.PlaneCreate().apply(context("plane create", {"net": "GND", "layer": 1}))
        ground = [shape for shape in doc.shapes[1:]]
        self.assertEqual(len(ground), 4)  # left, right, below and above the copper area
        for shape in ground:
            box = shape.Extrema
            x0, y0, x1, y1 = box.GetMinX(L.UNIT_MM), box.GetMinY(L.UNIT_MM), box.GetMaxX(L.UNIT_MM), box.GetMaxY(L.UNIT_MM)
            self.assertTrue(x1 <= 9.8 + 1e-6 or x0 >= 20.2 - 1e-6 or y1 <= 9.8 + 1e-6 or y0 >= 20.2 - 1e-6)  # 0.2 mm off it
        self.assertIn("cut around 1 plane shape(s) of other nets on the layer", preview["action"])

    def test_a_cut_leaves_out_a_sliver_between_the_shape_and_a_band_edge(self):
        band = ((0.0, 30.0, 30.0, 0.0, 0.0), (0.0, 0.0, 4.463, 4.463, 0.0), (0.0, 0.0, 0.0, 0.0, 0.0))  # x, y, r rows, closed
        pieces = B.cut_around(band, [(4.0, -0.1, 7.75, 4.25)])  # 0.2 mm off its top leaves 0.013 mm to the band's edge
        tops = sorted(round(max(row[1]), 3) for row in pieces)
        self.assertEqual(len(pieces), 2)  # left and right of it, no strip above
        self.assertEqual(tops, [4.463, 4.463])

    def test_a_cut_across_both_arms_of_a_u_shape_gives_two_pieces(self):
        u_shape = [(0, 0), (10, 0), (10, 10), (7, 10), (7, 3), (3, 3), (3, 10), (0, 10)]
        clipped = B._clip(u_shape, (None, 5, None, None))  # joined along y = 5 by an edge run there and back
        pieces = sorted(sorted(set(piece)) for piece in B._untangled(clipped))
        self.assertEqual(pieces, [[(0, 5), (0, 10), (3, 5), (3, 10)], [(7, 5), (7, 10), (10, 5), (10, 10)]])
        self.assertEqual(len(B._untangled(u_shape)), 1)  # a whole shape stays whole

    def test_nothing_left_to_pour_is_refused(self):
        doc = FakeDoc(nets=["GND", "VBUS"])
        power = {"net": "VBUS", "layer": 1, "polygon": {"points_mm": [[1.1, 1.1], [48.9, 1.1], [48.9, 38.9], [1.1, 38.9]]}}
        with world(FakeApp(doc)):
            B.PlaneCreate().apply(context("plane create", power))
            with self.assertRaises(AdapterError) as caught:
                B.PlaneCreate().apply(context("plane create", {"net": "GND", "layer": 1}))
        self.assertIn("Nothing is left to pour on layer 1", str(caught.exception))

    def test_a_band_reads_back_the_shapes_inside_it_not_one_along_its_edge(self):
        doc = FakeDoc(nets=["GND"])

        def shape(box: tuple[float, float, float, float], width: float, distance: float) -> SimpleNamespace:
            return SimpleNamespace(Layer=1, Net=SimpleNamespace(Name="GND"), Extrema=Extrema(box),
                                   PlaneClass=SimpleNamespace(Name="(Default)", HatchPattern=6, HatchWidth=width, HatchDistance=distance))

        doc.shapes = [shape((3.8, 4.45, 7.95, 4.463), 6.0, 6.0),  # a solid strip along the band's lower edge
                      shape((0.4, 4.463, 12.1, 6.767), 4.0, 12.0)]  # the band, hatched
        with world(FakeApp(doc)):
            values = L.plane_values(doc, "GND", 1, L.parse_region(",4.463,,6.767"))
        self.assertEqual(values["hatch"], "diagonal")

    def test_hatch_inputs_must_fit_together(self):
        with world(FakeApp(FakeDoc(nets=["GND"]))):
            for extra in ({"hatch": "zigzag"}, {"hatch": "orthogonal"}, {"hatch": "diagonal", "hatch_width_mm": 0.5, "hatch_distance_mm": 0.4},
                          {"hatch_width_mm": 0.1}):
                with self.subTest(extra=extra), self.assertRaises(AdapterError) as caught:
                    B.PlaneCreate().preview(context("plane create", {"net": "GND", "layer": 2, **extra}))
                self.assertEqual(code(caught), "E_VALIDATION")


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.handler = B.RouterRun()

    def _doc(self) -> FakeDoc:
        doc = FakeDoc(components=[FakeComponent("R1", placed=True, x=5, y=5)], nets=["A", "B", "GND", "VCC"])
        doc.net("VCC").routed, doc.net("VCC").NumberOfOpens = True, 0
        doc.net("GND").routed, doc.net("GND").NumberOfOpens = True, 0
        return doc

    def test_a_route_pass_sets_its_type_first_and_removes_only_inner_layers(self):
        doc = FakeDoc()
        item = {"type": 8, "effort": [1, 5], "pass": "route"}
        route_pass = B._route_pass(doc, item, [1, 4], 4)
        names = [call[0] for call in route_pass.calls]
        self.assertEqual(names[:4], ["PassType", "Items", "Order", "Config"])  # PassType resets everything
        self.assertIn(("Items", plan_routing.ROUTE_ITEMS_ALL_NETS, None), route_pass.calls)
        self.assertEqual([c for c in route_pass.calls if c[0] == "LayerSelect"], [("LayerSelect", 3, 2), ("LayerSelect", 3, 3)])
        self.assertFalse([c for c in B._route_pass(doc, item, [], 4).calls if c[0] == "LayerSelect"])

    def test_layers_must_keep_the_outer_ones(self):
        with world(FakeApp(self._doc())):
            for layers in ("2,3", "1,9"):
                with self.assertRaises(AdapterError) as caught:
                    self.handler.preview(context("router run", {"layers": layers}))
                self.assertEqual(code(caught), "E_VALIDATION")

    def test_preview_lists_the_open_nets_and_the_draft_planes(self):
        doc = self._doc()
        # GND on layer 1 is a Draft assignment whose shape was deleted: nothing to regenerate
        doc.assignments = [FakeAssignment("GND", 2, L.PLANE_DRAFT), FakeAssignment("VCC", 3, L.PLANE_DYNAMIC),
                           FakeAssignment("GND", 1, L.PLANE_DRAFT)]
        doc.shapes = [SimpleNamespace(Layer=layer, Net=SimpleNamespace(Name=net)) for net, layer in (("GND", 2), ("VCC", 3))]
        with world(FakeApp(doc)):
            out = self.handler.preview(context("router run"))
        self.assertEqual([(c["target"], c["field"], c["after"]) for c in out["changes"]],
                         [(key("/nets/A"), "routed", True), (key("/nets/B"), "routed", True), (key("/planes/GND/2"), "state", "dynamic")])
        self.assertTrue(out["can_apply"])

    def test_unplaced_parts_block_routing(self):
        doc = self._doc()
        doc.components.append(FakeComponent("R2"))
        with world(FakeApp(doc)):
            out = self.handler.preview(context("router run"))
            with self.assertRaises(AdapterError) as caught:
                self.handler.run(context("router run"), Progress())
        self.assertFalse(out["can_apply"])
        self.assertEqual(caught.exception.data["knowledge"], ["layout-unplaced-parts-invisible"])

    def test_apply_runs_the_passes_and_regenerates_the_planes(self):
        doc = self._doc()
        # GND on layer 1 lost its shape to plane delete: nothing to regenerate, and it is left alone
        doc.assignments = [FakeAssignment("GND", 2, L.PLANE_DYNAMIC), FakeAssignment("VCC", 3, L.PLANE_DRAFT),
                           FakeAssignment("GND", 1, L.PLANE_DRAFT)]
        doc.shapes = [SimpleNamespace(Layer=layer, Net=SimpleNamespace(Name=net)) for net, layer in (("GND", 2), ("VCC", 3))]

        def route_everything(route_pass: FakeRoutePass):
            for net in doc.nets:
                net.routed, net.NumberOfOpens = True, 0

        doc.on_go = route_everything
        with world(FakeApp(doc)):
            facts = self.handler.run(context("router run"), Progress())
            back = self.handler.read_back(context("router run", targets=[key("/nets/A"), key("/planes/VCC/3")], fields=["routed", "state"]))
        self.assertEqual([p.calls[0][1] for p in doc.passes], [8, 14, 10])  # route, viamin, smooth
        self.assertEqual(doc.assignments[0].states, [L.PLANE_DYNAMIC, L.PLANE_DRAFT, L.PLANE_DYNAMIC])
        self.assertEqual(doc.assignments[1].states, [L.PLANE_DRAFT, L.PLANE_DYNAMIC])
        self.assertEqual(doc.assignments[2].states, [L.PLANE_DRAFT])
        self.assertEqual((facts["change"], facts["result"]["routed"], facts["result"]["open"]), ("applied", 4, []))
        self.assertEqual(back["observed"], {key("/nets/A"): {"routed": True}, key("/planes/VCC/3"): {"state": "dynamic"}})

    def test_selected_nets_and_classes_route_alone_with_the_passes_asked_for(self):
        doc = self._doc()
        doc.NetClasses = Collection([SimpleNamespace(Name="USB", Nets=Collection([doc.net("B")]))])
        added: list[str] = []
        app = FakeApp(doc)
        app.Utility = SimpleNamespace(NewNets=lambda: SimpleNamespace(Add=lambda net: added.append(net.Name)))

        def route_mine(route_pass: FakeRoutePass):
            for name in ("A", "B"):
                doc.net(name).routed, doc.net(name).NumberOfOpens = True, 0

        doc.on_go = route_mine
        inputs = {"nets": "A", "net_classes": "USB", "passes": "fanout:1,route:1-5"}
        with world(app):
            out = self.handler.preview(context("router run", inputs))
            facts = self.handler.run(context("router run", inputs), Progress())
        self.assertEqual([c["target"] for c in out["changes"]], [key("/nets/A"), key("/nets/B")])
        self.assertIn("only the 2 selected nets", out["action"])
        self.assertIn("fanout:1-1,route:1-5", out["action"])
        self.assertEqual([p.calls[0][1] for p in doc.passes], [2, 8])  # fanout, then route
        self.assertTrue(all(("Items", plan_routing.ROUTE_ITEMS_NETS) == p.calls[1][:2] for p in doc.passes))
        self.assertEqual(added, ["A", "B"])
        self.assertEqual(facts["result"]["selected"], {"nets": 2, "routed": 2, "open": []})

    def test_existing_traces_of_the_nets_to_route_raise_the_tier_and_those_the_passes_change_are_reported(self):
        doc = self._doc()

        def trace(points):
            return SimpleNamespace(Layer=1, Geometry=Geometry([[p[0] for p in points], [p[1] for p in points], [0.0] * len(points)],
                                                              width_mm=0.1))

        guard, kept = trace([(0.0, 1.0), (5.0, 1.0)]), trace([(0.0, 2.0), (5.0, 2.0)])
        doc.net_traces = {"A": [guard, kept]}

        def reroute(route_pass: FakeRoutePass):
            doc.net_traces["A"] = [kept, trace([(0.0, 1.0), (2.0, 3.0), (5.0, 1.0)])]  # the guard moved
            for net in doc.nets:
                net.routed, net.NumberOfOpens = True, 0

        doc.on_go = reroute
        with world(FakeApp(doc)):
            out = self.handler.preview(context("router run"))
            facts = self.handler.run(context("router run"), Progress())
        self.assertEqual(out["tier_raises"], ["reworks_existing"])
        self.assertIn("A (2)", out["action"])
        self.assertEqual(facts["result"]["reworked"], [{"net": "A", "traces_before": 2, "moved_or_removed": 1}])

    def test_a_trace_the_passes_only_split_is_not_reported_as_moved(self):
        from xpedition_cli.adapter import layout_board as B
        before = [(1, [(0.0, 1.0), (5.0, 1.0)])]
        split = [(1, [(0.0, 1.0), (2.0, 1.0)]), (1, [(2.0, 1.0), (5.0, 1.0)]), (1, [(2.0, 1.0), (2.0, 4.0)])]  # a new trace meets it
        self.assertEqual(B._uncovered(before, split), 0)
        self.assertEqual(B._uncovered(before, [(1, [(0.0, 1.0), (2.0, 3.0), (5.0, 1.0)])]), 1)
        self.assertEqual(B._uncovered(before, [(2, [(0.0, 1.0), (5.0, 1.0)])]), 1)  # on another layer

    def test_an_unknown_net_class_or_pass_is_refused(self):
        with world(FakeApp(self._doc())):
            for inputs, wanted in (({"nets": "NOPE"}, "E_NOT_FOUND"), ({"net_classes": "NOPE"}, "E_NOT_FOUND"),
                                   ({"passes": "zigzag"}, "E_VALIDATION"), ({"passes": "route:9"}, "E_VALIDATION")):
                with self.subTest(inputs=inputs), self.assertRaises(AdapterError) as caught:
                    self.handler.preview(context("router run", inputs))
                self.assertEqual(code(caught), wanted)

    def test_a_net_still_open_gets_a_second_round(self):
        doc = self._doc()
        with world(FakeApp(doc)):
            facts = self.handler.run(context("router run"), Progress())
        self.assertEqual(len(doc.passes), 6)
        self.assertEqual(facts["result"]["open"], ["A", "B"])

    def test_passes_past_the_time_limit_are_skipped_and_reported(self):
        doc = self._doc()
        with world(FakeApp(doc)), mock.patch.object(B, "ROUTE_BUDGET_S", -1.0):
            facts = self.handler.run(context("router run"), Progress())
        self.assertEqual((facts["change"], facts["result"]["skipped_passes"]), ("partial", ["route", "viamin", "smooth"]))

    def test_a_failing_first_pass_is_an_error(self):
        doc = self._doc()
        doc.on_go = mock.Mock(side_effect=FakeComError(text="router failed"))
        with world(FakeApp(doc)), self.assertRaises(AdapterError) as caught:
            self.handler.run(context("router run"), Progress())
        self.assertEqual(caught.exception.data["dispatch_state"], "dispatched")

    def test_nothing_open_nothing_to_do(self):
        doc = FakeDoc(nets=["GND"])
        doc.net("GND").routed, doc.net("GND").NumberOfOpens = True, 0
        with world(FakeApp(doc)):
            self.assertEqual(self.handler.preview(context("router run"))["changes"], [])
            self.assertEqual(self.handler.run(context("router run"), Progress())["change"], "not_applied")
        self.assertEqual(doc.passes, [])


if __name__ == "__main__":
    unittest.main()
