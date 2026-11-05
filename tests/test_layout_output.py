"""The Layout commands that read, check and export the open board (adapter/layout_output.py), on
fake COM objects."""
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

from test_layout_fakes import (  # noqa: E402
    BOARD,
    MIL,
    FakeAnswerer,
    FakeApp,
    FakeComError,
    FakeComponent,
    FakeDoc,
    Collection,
    Extrema,
    Fake,
    Geometry,
    Progress,
    context,
    rect_rows,
    world,
)

from xpedition_cli._driver import AdapterError  # noqa: E402
from xpedition_cli.adapter import base  # noqa: E402
from xpedition_cli.adapter import layout as L  # noqa: E402
from xpedition_cli.adapter import layout_output as O  # noqa: E402
from xpedition_cli.adapter.platform import dialogs  # noqa: E402


def code(caught) -> str:
    return caught.exception.data["code"]


class Layer(Fake):
    def __init__(self, kind: int, board_layer: int, conductor: int | None = None, name: str = "", usage: int = 1) -> None:
        self.Type, self.BoardLayer, self.ConductorLayer = kind, board_layer, conductor
        properties = Fake()
        properties.StackupLayerName, properties.Description, properties.LayerUsage = name, "", usage
        properties.DielectricConstant = 4.3
        properties.props = {O.THICKNESS_DISPID: lambda unit: 0.035 if kind == 0 else 1.5}
        self.LayerProperties = properties


class NetClass(Fake):
    def __init__(self, name: str, nets: list[str]) -> None:
        self.Name = name
        self.Nets = Collection(SimpleNamespace(Name=n) for n in nets)
        self.props = {dispid: (lambda layer, scheme, unit, w=width: w if scheme == "(Master)" and unit == L.UNIT_MM else None)
                      for dispid, width in ((7, 0.2), (8, 0.254), (9, 0.3))}


class Text:
    def __init__(self, string: str, owner: FakeComponent | None, box: tuple[float, float, float, float], fab_type: int = 2,
                 text_type: int = 1) -> None:
        self.TextString, self.Component, self.Type, self.TextType = string, owner, fab_type, text_type
        self.Extrema = Extrema(box)
        self.Format = SimpleNamespace(Height=1.0 / MIL, PenWidth=0.15 / MIL)
        self.Side = 1


def board() -> FakeDoc:
    placed = FakeComponent("U101", 5.0, 4.0, pins={"1": (-2.0, 1.0, "VCC"), "2": (2.0, 0.0, "GND")}, placed=True, x=10.0, y=10.0)
    cap = FakeComponent("C101", 1.6, 0.8, pins={"1": (-0.7, 0.0, "VCC"), "2": (0.7, 0.0, "GND")}, placed=True, x=14.0, y=11.0)
    loose = FakeComponent("R101")
    doc = FakeDoc(components=[placed, cap, loose], nets=["VCC", "GND"])
    for net in doc.nets:
        net.routed, net.NumberOfOpens = net.Name == "GND", 0 if net.Name == "GND" else 1
    doc.traces = [SimpleNamespace(Layer=1, Net=SimpleNamespace(Name="GND"), Geometry=Geometry(rect_rows(0, 0, 2, 0)[:2] + [[0.0] * 5],
                                                                                                  width_mm=0.25))]
    doc.vias = [SimpleNamespace(Net=SimpleNamespace(Name="GND"), GetPositionX=lambda unit: 3.0, GetPositionY=lambda unit: 4.0)]
    doc.texts = [Text("U101", placed, (9.0, 12.5, 11.0, 13.5)), Text("R101", loose, (0.0, 0.0, 1.0, 1.0)),
                 Text("ASSY", placed, (9.0, 9.0, 11.0, 10.0), fab_type=1), Text("POWER", None, (20.0, 30.0, 26.0, 31.0), text_type=2)]
    doc.gfxs = [SimpleNamespace(Type=2, Component=placed, Geometry=Geometry([[7.0, 13.0], [8.0, 8.0], [0.0, 0.0]], width_mm=0.12),
                                Side=1)]
    doc.props = {O.STACKUP_DISPID: lambda insulation: Collection([Layer(0, 1, 1, "Top"), Layer(3, 2, name="Core"),
                                                                  Layer(0, 3, 2, "Bottom", usage=2)]),
                 O.NET_CLASSES_DISPID: lambda pattern: Collection([NetClass("(Default)", ["VCC", "GND"])]),
                 O.OBSTRUCTS_DISPID: lambda *args: Collection([1, 2]), O.PLACEMENT_OBSTRUCTS_DISPID: lambda *args: Collection([3])}
    doc.LayerCount = 2
    return doc


class BoardModelTest(unittest.TestCase):
    def test_the_model_holds_placed_parts_with_pins_pads_and_their_silkscreen_texts(self):
        with world(FakeApp(board())) as _:
            model = O.board_model(board(), full=True)
        refs = {c["refdes"]: c for c in model["components"]}
        self.assertEqual(refs["U101"]["extents"], [7.5, 8.0, 12.5, 12.0])
        self.assertEqual(refs["U101"]["pins"][0], {"pin": "1", "net": "VCC", "x": 8.0, "y": 11.0})
        self.assertNotIn("pins", refs["R101"])  # an unplaced part's pins stand at its cell's coordinates
        self.assertEqual(sorted(t["text"] for t in model["texts"]), ["POWER", "U101"])  # silkscreen of placed parts only
        u101 = next(t for t in model["texts"] if t["text"] == "U101")
        self.assertEqual((u101["kind"], u101["height"], u101["pen"], u101["refdes"]), ("refdes", 1.0, 0.15, "U101"))
        self.assertEqual(model["pads"][0]["circle"], [0.0, 0.0, 0.3])
        self.assertEqual(model["traces"][0]["path"][1][:2], [2.0, 0.0])
        self.assertEqual(model["unrouted"], [{"net": "VCC", "opens": 1}])
        self.assertEqual(model["silk"][0]["refdes"], "U101")

    def test_a_board_not_in_mils_has_no_model(self):
        doc = board()
        doc.CurrentUnit = L.UNIT_MM
        self.assertIsNone(O.board_model(doc, full=False))


class BoardGetTest(unittest.TestCase):
    def test_the_board_in_numbers(self):
        with world(FakeApp(board())):
            out = O.BoardGet().read(context("pcb get"))
        self.assertEqual(out["path"], BOARD)
        self.assertEqual(out["components"], {"total": 3, "placed": 2, "unplaced": 1})
        self.assertEqual(out["nets"], {"total": 2, "routed": 1, "open": 1})
        self.assertEqual((out["traces"], out["vias"], out["keepouts"]), (1, 1, 3))
        self.assertEqual(out["outline"]["width_mm"], 50.0)
        self.assertEqual([row["kind"] for row in out["stackup"]], ["conductor", "insulation", "conductor"])
        self.assertEqual(out["stackup"][2]["usage"], "plane")
        self.assertEqual(out["stackup"][1]["thickness_mm"], 1.5)
        self.assertEqual(out["net_classes"], [{"name": "(Default)", "nets": 2, "widths": {
            "1": {"minimum": 0.2, "typical": 0.254, "expansion": 0.3}, "2": {"minimum": 0.2, "typical": 0.254, "expansion": 0.3}}}])
        self.assertTrue(out["geometry"]["available"])
        self.assertEqual(out["geometry"]["counts"]["placed"], 2)
        self.assertEqual(out["metrics"]["summary"]["unplaced"], 1)
        self.assertLess(len(json.dumps(out)), 8000)

    def test_geometry_and_metrics_are_unavailable_on_a_board_not_in_mils(self):
        doc = board()
        doc.CurrentUnit = L.UNIT_MM
        with world(FakeApp(doc)):
            out = O.BoardGet().read(context("pcb get"))
        self.assertFalse(out["geometry"]["available"])
        self.assertIn("mils", out["geometry"]["reason"])
        self.assertFalse(out["metrics"]["available"])
        self.assertEqual(out["components"]["total"], 3)

    def test_a_part_of_the_report_that_cannot_be_read_is_none(self):
        doc = board()
        doc.props[O.STACKUP_DISPID] = FakeComError(text="no stack")
        with world(FakeApp(doc)):
            out = O.BoardGet().read(context("pcb get"))
        self.assertIsNone(out["stackup"])
        self.assertIsNotNone(out["net_classes"])


class Hazard(Fake):
    def __init__(self, kind: int, description: str, values: dict[int, float], objects: list | None = None) -> None:
        self.Type, self.Description = kind, description
        self.props = {dispid: (lambda unit, v=value: v if unit == L.UNIT_MM else None) for dispid, value in values.items()}
        self.Objects = Collection(objects or [])


class Pin(Fake):
    type_name = "IMGCPCBPin"

    def __init__(self, refdes: str, name: str) -> None:
        self.Component, self.Name = SimpleNamespace(RefDes=refdes), name


class BoardCheckTest(unittest.TestCase):
    def setUp(self):
        self.names = mock.patch.object(O, "hazard_names", lambda app: {67: "Clearance", 91: "ViasUnderParts"})
        self.names.start()

    def tearDown(self):
        self.names.stop()

    def _run(self, doc, **world_args):
        app = FakeApp(doc)
        with world(app, **world_args), mock.patch.object(O, "DRC_QUIET_S", 0.0), mock.patch.object(O, "DRC_POLL_S", 0.0):
            out = O.BoardCheck().read(context("pcb check"))
        return app, out

    def test_batch_drc_runs_on_the_open_board_and_every_hazard_is_read(self):
        doc = board()
        doc.hazards = [Hazard(67, "Pad to pad clearance", {9: 4.23, 10: 29.63, 16: 0.1, 17: 0.2}, [Pin("U101", "1"), Pin("C101", "2")]),
                       Hazard(91, "Via under part", {9: 3.0, 10: 4.0, 16: -1.0, 17: -1.0})]
        app, out = self._run(doc)
        self.assertEqual(app.Gui.commands, [O.BATCH_DRC_COMMAND])
        self.assertEqual(FakeAnswerer.made[0].rules, dialogs.DRC_PROMPTS)
        self.assertIn(("GetHazards", O.HAZARDS_BATCH), doc.calls)
        self.assertNotIn(("Close", True), doc.calls)
        self.assertNotIn(("Save",), doc.calls)
        first, second = out["hazards"]
        self.assertEqual(first, {"type": "Clearance", "severity": "error", "description": "Pad to pad clearance", "x_mm": 4.23,
                                 "y_mm": 29.63, "objects": ["U101.1", "C101.2"], "actual_mm": 0.1, "required_mm": 0.2})
        self.assertEqual((second["severity"], second["actual_mm"]), ("warning", None))
        self.assertGreaterEqual(out["errors"], 1)
        self.assertTrue(any(rule["rule"] == "unplaced" and rule["objects"] == ["R101"] for rule in out["rules"]))

    def test_the_run_waits_for_the_processing_window(self):
        windows = [["Processing..."], ["Processing..."], []]

        def processing(pid):
            return windows.pop(0) if windows else []

        doc = board()
        app = FakeApp(doc)
        with world(app), mock.patch.object(dialogs, "processing_windows", processing), mock.patch.object(O, "DRC_POLL_S", 0.0):
            out = O.BoardCheck().read(context("pcb check"))
        self.assertTrue(out["drc"]["processing_seen"])
        self.assertEqual(windows, [])

    def test_a_drc_that_runs_too_long_is_a_timeout(self):
        with world(FakeApp(board()), processing=["Processing..."]), mock.patch.object(O, "DRC_WAIT_S", 0.0), \
                mock.patch.object(O, "DRC_POLL_S", 0.0), self.assertRaises(AdapterError) as caught:
            O.BoardCheck().read(context("pcb check"))
        self.assertEqual(code(caught), "E_TIMEOUT")

    def test_a_refused_command_and_unreadable_hazards_are_errors(self):
        doc = board()
        app = FakeApp(doc)
        app.Gui.set("accept", False)
        with world(app), self.assertRaises(AdapterError) as caught:
            O.BoardCheck().read(context("pcb check"))
        self.assertEqual(code(caught), "E_NATIVE_FAILURE")
        doc = board()
        doc.GetHazards = mock.Mock(side_effect=FakeComError(text="no hazards"))
        with world(FakeApp(doc)), mock.patch.object(O, "DRC_QUIET_S", 0.0), self.assertRaises(AdapterError):
            O.BoardCheck().read(context("pcb check"))

    def test_no_hazards_no_type_library(self):
        loads = []
        self.names.stop()
        with mock.patch.object(O, "hazard_names", lambda app: loads.append(app) or {}):
            _, out = self._run(board())
        self.names.start()
        self.assertEqual((loads, out["hazards"], out["hazard_count"]), ([], [], 0))

    def test_board_rules_need_a_board_in_mils(self):
        doc = board()
        doc.CurrentUnit = L.UNIT_MM
        _, out = self._run(doc)
        self.assertEqual([rule["rule"] for rule in out["rules"]], ["board_rules_unavailable"])

    def test_hazard_names_come_from_the_type_library_constants(self):
        self.names.stop()
        table = SimpleNamespace(__dicts__=[{"epcbHazardTypeClearance": 67, "epcbHazardTypeBatch": 2, "other": 1}])
        with mock.patch("xpedition_cli.adapter.platform.com.late_bound", lambda app: app), \
                mock.patch.object(O, "_constants_table", lambda: table):
            self.assertEqual(O.hazard_names(object()), {67: "Clearance", 2: "Batch"})
        self.names.start()


ODB_SETUP = '.FILETYPE ODBSETUP\n.LAYERS_TO_EXPORT\n ..NAME "d_1_4"\n  ...INCLUDE NO\n.BOARD_OUTLINE NO\n'


class Outputs:
    """Layout's Output commands writing into the board's Output folder; the ODB++ dialog writes its
    old setup back over the patch the first time, as Layout's dialogs can."""

    def __init__(self, board_dir: Path, rewrite_once: bool = False) -> None:
        self.board_dir, self.rewrite_once = board_dir, rewrite_once

    def __call__(self, command: int) -> None:
        out = self.board_dir / "Output"
        if command == 33018:
            (out / "NCDrill").mkdir(parents=True, exist_ok=True)
            (out / "NCDrill" / "ThruHolePlated.ncd").write_text("T1C0.3\nX1000Y1000\nX2000Y1000\nM30\n", encoding="latin-1")
        elif command == 33017:
            job = out / "ODBpp" / "demo"
            (job / "matrix").mkdir(parents=True, exist_ok=True)
            (job / "matrix" / "matrix").write_text("LAYER {\n NAME=top\n TYPE=SIGNAL\n}\n", encoding="latin-1")
            if self.rewrite_once:
                (self.board_dir / "Config" / "ODBSetup.ocf").write_text(ODB_SETUP, encoding="utf-8")
                self.rewrite_once = False
        elif command == 33016:
            (out / "Gerber").mkdir(parents=True, exist_ok=True)
            (out / "Gerber" / "EtchLayer1Top.gdo").write_text("G04 top*\nX1Y1D02*\nX2Y2D01*\nM02*\n", encoding="latin-1")


class BoardExportTest(unittest.TestCase):
    def setUp(self):
        self.settle = mock.patch.object(O, "OUTPUT_SETTLE_S", 0.0)
        self.settle.start()
        self.addCleanup(self.settle.stop)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.board_dir = root / "PCB"
        (self.board_dir / "Config").mkdir(parents=True)
        (self.board_dir / "Config" / "ODBSetup.ocf").write_text(ODB_SETUP, encoding="utf-8")
        self.pcb = str(self.board_dir / "Demo.pcb")
        Path(self.pcb).write_bytes(b"board")
        self.output = str(root / "fab")

    def tearDown(self):
        self.tmp.cleanup()

    def _doc(self) -> FakeDoc:
        doc = FakeDoc(self.pcb, components=[FakeComponent("R1", placed=True, x=5.0, y=6.0, part_number="RES-10K")], width=30.0,
                      height=20.0)
        doc.LayerCount = 2
        return doc

    def test_preview_names_the_package_and_the_setups_to_patch(self):
        with world(FakeApp(self._doc())):
            out = O.BoardExport().preview(context("pcb export", {"output": self.output}, document=self.pcb))
        files = {c["target"] for c in out["changes"] if c["field"] == "exists"}
        self.assertEqual(files, {base.key(os.path.join(self.output, name)) for name in O.PACKAGE_FILES})
        self.assertIn(base.key(str(self.board_dir / "Config" / "ODBSetup.ocf")), {c["target"] for c in out["changes"]})
        self.assertIn("ODBSetup.ocf", out["action"])
        self.assertEqual(Path(self.board_dir / "Config" / "ODBSetup.ocf").read_text(encoding="utf-8"), ODB_SETUP)  # nothing changed

    def test_a_folder_with_files_is_refused(self):
        Path(self.output).mkdir()
        (Path(self.output) / "old.txt").write_text("x", encoding="utf-8")
        with world(FakeApp(self._doc())), self.assertRaises(AdapterError) as caught:
            O.BoardExport().preview(context("pcb export", {"output": self.output}, document=self.pcb))
        self.assertEqual(code(caught), "E_VALIDATION")

    def test_run_patches_runs_the_three_outputs_and_writes_the_package_with_the_board_open(self):
        doc = self._doc()
        app = FakeApp(doc)
        app.Gui.set("on_command", Outputs(self.board_dir, rewrite_once=True))
        progress = Progress()
        with world(app):
            out = O.BoardExport().run(context("pcb export", {"output": self.output}, document=self.pcb), progress)
        self.assertEqual(app.Gui.commands, [33018, 33017, 33016, 33018, 33017, 33016])  # rewritten once: a second round
        self.assertTrue(all(answerer.rules == dialogs.OUTPUT_PROMPTS for answerer in FakeAnswerer.made))
        self.assertFalse([call for call in doc.calls if call[0] in ("Close", "Save")])
        self.assertEqual((out["change"], out["persistence"]), ("not_applicable", "saved"))
        paths = [item["path"] for item in out["files"]]
        self.assertIn(str(Path(self.output) / "manifest.json"), paths)
        self.assertIn(str(Path(self.output) / "drill" / "ThruHolePlated.drl"), paths)
        self.assertTrue(all(os.path.isfile(path) for path in paths))
        self.assertEqual(out["result"]["board"], {"width_mm": 30.0, "height_mm": 20.0, "layers": 2})
        self.assertEqual(out["result"]["file_count"], len(out["files"]))  # the receipt records each file; the result counts them
        self.assertTrue(out["result"]["manifest"].endswith("manifest.json"))
        self.assertEqual(out["result"]["setups_rewritten"], [])
        self.assertTrue((self.board_dir / "Config" / "ODBSetup.ocf.bak-xpedition-cli").read_text(encoding="utf-8").endswith("NO\n"))
        self.assertIn(".BOARD_OUTLINE YES", (self.board_dir / "Config" / "ODBSetup.ocf").read_text(encoding="utf-8"))
        centroid = (Path(self.output) / "centroid.csv").read_text(encoding="utf-8")
        self.assertIn("R1,5.0,6.0", centroid)
        self.assertEqual(progress.steps[-1][0], progress.steps[-1][1])

    def test_one_round_when_the_patch_holds(self):
        app = FakeApp(self._doc())
        app.Gui.set("on_command", Outputs(self.board_dir))
        with world(app):
            O.BoardExport().run(context("pcb export", {"output": self.output}, document=self.pcb), Progress())
        self.assertEqual(app.Gui.commands, [33018, 33017, 33016])

    def test_a_refused_output_command_stops_the_job(self):
        app = FakeApp(self._doc())
        app.Gui.set("accept", False)
        with world(app), self.assertRaises(AdapterError) as caught:
            O.BoardExport().run(context("pcb export", {"output": self.output}, document=self.pcb), Progress())
        self.assertEqual(code(caught), "E_NATIVE_FAILURE")

    def test_a_failure_after_the_setups_were_patched_is_partial(self):
        app = FakeApp(self._doc())
        commands = []

        def fail_on_odb(command):
            commands.append(command)
            if command == 33017:
                raise FakeComError(text="ODB++ output failed")

        app.Gui.set("on_command", fail_on_odb)
        with world(app), self.assertRaises(AdapterError) as caught:
            O.BoardExport().run(context("pcb export", {"output": self.output}, document=self.pcb), Progress())
        data = caught.exception.data
        self.assertEqual((data["change"], data["persistence"], data["dispatch_state"]), ("not_applicable", "partial", "dispatched"))
        self.assertEqual(commands, [33018, 33017])

    def test_the_wait_ends_when_the_output_dialogs_are_gone(self):
        windows = [{"title": "Gerber Output", "owner": 1, "style": 0, "class": "Qt5QWindowIcon"},
                   {"title": "[Demo.pcb]", "owner": 0, "style": 0, "class": "x"},
                   {"title": "Xpedition Layout", "owner": 0, "style": O.WS_MINIMIZEBOX, "class": "Qt5QWindowIcon"}]
        with world(FakeApp(self._doc()), windows=windows):
            self.assertEqual(O.output_windows(1), ["Gerber Output"])

    def test_read_back_reports_the_files(self):
        Path(self.output).mkdir()
        (Path(self.output) / "manifest.json").write_text("{}", encoding="utf-8")
        targets = [base.key(os.path.join(self.output, name)) for name in ("manifest.json", "bom.csv")]
        out = O.BoardExport().read_back(context("pcb export", {"output": self.output}, document=self.pcb, targets=targets,
                                                fields=["exists"]))
        self.assertEqual([out["observed"][t]["exists"] for t in targets], [True, False])


if __name__ == "__main__":
    unittest.main()
