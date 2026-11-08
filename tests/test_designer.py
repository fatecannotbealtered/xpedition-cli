"""Designer sessions and the schematic commands (adapter/designer.py), against a stand-in for
Designer's COM objects.

The stand-in keeps what the old implementation measured on XPED2604: ``SchematicSheetDocuments``
lists the open sheet windows while ``GetAvailableSheets`` lists every sheet; the New Sheet command
(34165) creates and activates the next sheet; ``Documents`` is a property and ``SaveAll`` saves;
``DesignComponents`` fails with a type mismatch after sheet operations until the project is
reopened. No Xpedition is started; ``sch2pdf`` is faked at ``env.run_tool``.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from xpedition_cli._driver import AdapterError
from xpedition_cli._driver.state import AdapterStore, StateDir
from xpedition_cli._driver.targets import normalize
from xpedition_cli.adapter import base, designer, sessions
from xpedition_cli.adapter import library as library_module
from xpedition_cli.adapter.plan import bom, library_read, verification
from xpedition_cli.adapter.plan import schematic_layout as L
from xpedition_cli.adapter.plan import symbols as S
from xpedition_cli.adapter.platform import com, dialogs, env, processes

PID = 4120
SID = f"designer-{PID}-6553f1a0"
DEMO = Path(__file__).resolve().parent / "fixtures" / "demo-sensor-board.json"

PRJ_TEXT = """SECTION DesignInfo
KEY CentralLibrary "Lib\\Lib.lmc"
ENDSECTION
SECTION iCDB
LIST Designs
VALUE "Schematic1"
VALUE "Board1"
ENDLIST
ENDSECTION
SECTION Schematic1
ENDSECTION
SECTION Board1
LIST Symbols
VALUE "SymbolLibs\\Globals"
VALUE "SymbolLibs\\PartQuest"
ENDLIST
LIST PDBs
VALUE "PartsDBLibs\\PartQuest.pdb"
ENDLIST
KEY ConfigType "PCB"
ENDSECTION
"""


# ------------------------------------------------------------------ the stand-in


class Coll:
    """A COM collection: ``Count`` and a 1-based ``Item``."""

    def __init__(self, items: Any) -> None:
        self.items = list(items)

    @property
    def Count(self) -> int:  # noqa: N802 - COM name
        return len(self.items)

    def Item(self, index: int) -> Any:  # noqa: N802 - COM name
        return self.items[index - 1]


class Strings:
    """Designer's IStringList."""

    def __init__(self, items: list[str]) -> None:
        self.items = items

    def GetCount(self) -> int:  # noqa: N802 - COM name
        return len(self.items)

    def GetItem(self, index: int) -> str:  # noqa: N802 - COM name
        return self.items[index - 1]


class ComError(Exception):
    """Shaped like pywin32's com_error: (hresult, text, excepinfo, argerror)."""

    def __init__(self, hresult: int, text: str = "fault") -> None:
        super().__init__(hresult, text, None, None)


def type_mismatch() -> ComError:
    return ComError(designer.DISP_E_TYPEMISMATCH, "Type mismatch.")


class Attr:
    def __init__(self, name: str, value: str = "") -> None:
        self.Name, self.Value = name, value
        self.Orientation = self.Origin = 0
        self.location: tuple[int, int] | None = None

    def SetLocation(self, x: int, y: int) -> None:  # noqa: N802 - COM name
        self.location = (x, y)


def connection(number: str, net: str | None = None, x: int | None = None, y: int | None = None) -> Any:
    pin = SimpleNamespace(Number=number, GetLocation=SimpleNamespace(X=x, Y=y) if x is not None else None)
    return SimpleNamespace(CompPin=pin, Net=SimpleNamespace(Name=net) if net else None)


class Part:
    """A component: ``Refdes``, ``Attributes``, ``GetLocation`` (a property) and ``GetConnections``."""

    def __init__(self, refdes: str, pins: list[tuple] = (), part_number: str = "", x: int = 0, y: int = 0) -> None:
        self.Refdes = refdes
        self.Orientation = 0
        self.GetLocation = SimpleNamespace(X=x, Y=y)
        self.Attributes = Coll([Attr("Ref Designator", refdes), Attr("Part Number", part_number)])
        self._connections = Coll([connection(*pin) for pin in pins])
        self.placed: tuple = ()

    def GetConnections(self) -> Coll:  # noqa: N802 - COM name
        return self._connections


class Wire:
    def __init__(self, args: tuple) -> None:
        self.args = args
        self.labels: list[tuple] = []
        self.GetSegments = Coll([object()])

    def AddLabel(self, segment: Any, name: str, x: int, y: int) -> None:  # noqa: N802 - COM name
        self.labels.append((name, x, y))


class Block:
    def __init__(self, number: int) -> None:
        self.SheetNum = number
        self.size = 0
        self.border = ""
        self.ignore_size = 0  # how many size sets Designer drops
        self.parts: list[Part] = []
        self.symbols: list[tuple] = []
        self.wires: list[Wire] = []
        self.boxes: list[tuple] = []
        self.texts: list[Any] = []
        self.deleted = 0
        self.components: list[Part] = []
        self.components_error: Exception | None = None
        self.text_error: Exception | None = None

    @property
    def SheetSize(self) -> int:  # noqa: N802 - COM name
        return self.size

    @SheetSize.setter
    def SheetSize(self, value: int) -> None:  # noqa: N802 - COM name
        if self.ignore_size:
            self.ignore_size -= 1
        else:
            self.size = value

    @property
    def Components(self) -> Coll:  # noqa: N802 - COM name
        if self.components_error:
            raise self.components_error
        return Coll(self.components)

    def ChangeBorder(self, name: str) -> None:  # noqa: N802 - COM name
        self.border = name

    def DeleteSelected(self, flag: bool) -> None:  # noqa: N802 - COM name
        self.deleted += 1

    def AddPartInstance(self, library: str, part: str, symbol: str, x: int, y: int) -> Part:  # noqa: N802 - COM name
        placed = Part("", [(str(n),) for n in range(1, 65)])
        placed.placed = (library, part, symbol, x, y)
        self.parts.append(placed)
        return placed

    def AddSymbolInstance(self, library: str, symbol: str, x: int, y: int) -> Any:  # noqa: N802 - COM name
        self.symbols.append((library, symbol, x, y))
        return SimpleNamespace(Orientation=0)

    def AddNet(self, *args: Any) -> Wire:  # noqa: N802 - COM name
        wire = Wire(args)
        self.wires.append(wire)
        return wire

    def AddBox(self, *args: int) -> None:  # noqa: N802 - COM name
        self.boxes.append(args)

    def AddText(self, text: str, x: int, y: int) -> Any:  # noqa: N802 - COM name
        if self.text_error:
            raise self.text_error
        item = SimpleNamespace(Size=0, text=text)
        self.texts.append(item)
        return item


class SheetDoc:
    def __init__(self, app: App, number: int) -> None:
        self.app = app
        self.number = number
        self.Name = f"Schematic1.{number}"

    def Activate(self) -> None:  # noqa: N802 - COM name
        self.app.active = self.number


class SheetDocs:
    """``SchematicSheetDocuments``: the open sheet windows, and every sheet through GetAvailableSheets."""

    def __init__(self, app: App) -> None:
        self.app = app

    @property
    def Count(self) -> int:  # noqa: N802 - COM name
        return len(self.app.open_docs)

    def Item(self, index: int) -> SheetDoc:  # noqa: N802 - COM name
        return self.app.open_docs[index - 1]

    def GetAvailableSchematics(self) -> Strings:  # noqa: N802 - COM name
        return Strings(["Schematic1"])

    def GetAvailableSheets(self, name: str) -> Strings:  # noqa: N802 - COM name
        assert name == "Schematic1"
        return Strings([str(n) for n in sorted(self.app.blocks)])

    def Open(self, schematic: str, number: str) -> None:  # noqa: N802 - COM name
        if not any(doc.number == int(number) for doc in self.app.open_docs):
            self.app.open_docs.append(SheetDoc(self.app, int(number)))


class App:
    """Designer's application object."""

    def __init__(self, project: str = "", sheets: tuple[int, ...] = (1,)) -> None:
        self.project = project
        self.opened = bool(project)
        self.blocks = {n: Block(n) for n in sheets}
        self.active: int | None = sheets[0]
        self.open_docs = [SheetDoc(self, sheets[0])]
        self.commands: list[int] = []
        self.components: list[Part] = []
        self.components_error: Exception | None = None
        self.active_design = "Board1"
        self.saved = self.closed = self.quits = 0
        self.opened_paths: list[str] = []
        self.design_opened: str | None = None
        self.view = True
        self.on_verify: Any = None
        self.on_open: Any = None
        self.on_quit: Any = None

    @property
    def ActiveView(self) -> Any:  # noqa: N802 - COM name
        if not self.view or self.active not in self.blocks:
            return None
        return SimpleNamespace(Block=self.blocks[self.active])

    @property
    def Documents(self) -> Any:  # noqa: N802 - COM name: a property in the 151 type library
        return SimpleNamespace(SaveAll=self._save_all, Open=self._open_design)

    def _save_all(self) -> None:
        self.saved += 1

    def _open_design(self, name: str) -> None:
        self.design_opened = name

    def SchematicSheetDocuments(self) -> SheetDocs:  # noqa: N802 - COM name: a method
        return SheetDocs(self)

    def ExecuteCommandByID(self, command: int) -> None:  # noqa: N802 - COM name
        self.commands.append(command)
        if command == designer.NEW_SHEET_COMMAND:
            number = max(self.blocks) + 1
            self.blocks[number] = Block(number)
            self.open_docs.append(SheetDoc(self, number))
            self.active = number
        if command == verification.FULL_VERIFICATION_COMMAND and self.on_verify:
            self.on_verify()

    def IsProjectOpened(self) -> bool:  # noqa: N802 - COM name
        return self.opened

    def GetProjectData(self) -> Any:  # noqa: N802 - COM name
        return SimpleNamespace(GetProjectFilePath=lambda: self.project, GetiCDBDesignRootBlock=lambda name: "Schematic1")

    def GetActiveDesign(self) -> str:  # noqa: N802 - COM name
        return self.active_design

    def DesignComponents(self, *args: Any) -> Coll:  # noqa: N802 - COM name
        if self.components_error:
            raise self.components_error
        return Coll(self.components)

    def OpenProject(self, path: str) -> None:  # noqa: N802 - COM name
        if self.on_open:
            self.on_open()
        self.opened_paths.append(path)
        self.project, self.opened = path, True

    def CloseProject(self) -> bool:  # noqa: N802 - COM name
        self.closed += 1
        self.project, self.opened = "", False
        return True

    def Quit(self) -> None:  # noqa: N802 - COM name
        self.quits += 1
        if self.on_quit:
            self.on_quit()


class FakeProgress:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.states: list[tuple] = []

    def __call__(self, done: float, total: float | None, unit: str) -> None:
        self.calls.append((done, total, unit))

    def blocked(self, action: str, message: str) -> None:
        self.states.append(("blocked", action, message))

    def unblocked(self) -> None:
        self.states.append(("unblocked",))


# ------------------------------------------------------------------ the test base


class DesignerCase(unittest.TestCase):
    """A temporary project and the stand-in Designer, attached in place of COM."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prj = str(self.root / "Demo.prj")
        Path(self.prj).write_text(PRJ_TEXT, encoding="utf-8")
        self.app = App(project=self.prj)
        self.procs = [processes.ProcessInfo(PID, "C:/xpedition/wv/viewdraw.exe", 1)]
        self.dialogs: list[dict[str, Any]] = []
        self.licence: list[str] = []
        self.store = AdapterStore(StateDir(str(self.root / "state")))
        for patcher in (
            mock.patch.object(designer, "_attach", lambda: self.app),
            mock.patch.object(designer, "_attach_or_none", lambda: self.app),
            mock.patch.object(designer, "_designer_processes", lambda: list(self.procs)),
            mock.patch.object(designer, "_settle", lambda seconds: None),
            mock.patch.object(designer, "_wire_kind", lambda: 7),
            mock.patch.object(designer, "_VERSION", "2604"),
            mock.patch.object(designer, "POLL_S", 0.01),
            mock.patch.object(com, "initialized", contextlib.nullcontext),
            mock.patch.object(dialogs, "answer_prompts", self.answer),
            mock.patch.object(dialogs, "licence_windows", lambda pid: list(self.licence)),
            mock.patch.object(dialogs, "processing_windows", lambda pid: []),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def answer(self, rules: Any, pid: Any, *, blocking: list | None = None) -> list[str]:
        if blocking is not None:
            blocking.extend(self.dialogs)
        return []

    def ctx(self, command: str, inputs: dict[str, Any] | None = None, **request: Any) -> base.Context:
        req = {"command": command, "inputs": inputs or {}, "session": SID, "document": self.prj, **request}
        return base.Context(self.store, req)

    def design_file(self, design: dict[str, Any] | None = None) -> str:
        path = self.root / "design.json"
        if design is None:
            path.write_text(DEMO.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            path.write_text(json.dumps(design), encoding="utf-8")
        return str(path)

    def sym_folder(self, partition: str = "PartQuest") -> Path:
        return self.root / "Lib" / "SymbolLibs" / partition / "sym"

    def code(self, caught: Any) -> str:
        return caught.exception.data["code"]


def two_sheet_design() -> dict[str, Any]:
    return {
        "sheet_size": "B",
        "sheets": [
            {"number": 1, "title": "POWER", "blocks": [
                {"kind": "ladder", "x": 300, "y": 500, "path": ["power:+3V3", {"refdes": "R1", "symbol": "RES", "value": "10k"},
                                                             "label:LED_A"]}]},
            {"number": 2, "title": "LED", "blocks": [
                {"kind": "ladder", "x": 300, "y": 500, "path": ["label:LED_A", {"refdes": "D1", "symbol": "LED", "value": "LED-RED"},
                                                             "gnd"]}]},
        ],
    }


# ------------------------------------------------------------------ COM members and small readers


class MembersTest(unittest.TestCase):
    def test_a_property_is_read_and_a_method_is_called(self):
        point = SimpleNamespace(X=1, Y=2)
        obj = SimpleNamespace(GetLocation=point, Count=lambda: 3)
        self.assertIs(designer._get(obj, "GetLocation"), point)  # a returned object is never called
        self.assertEqual(designer._get(obj, "Count"), 3)
        self.assertIsNone(designer._try_get(obj, "Absent"))

    def test_a_net_name_comes_from_its_segment_label(self):
        label = SimpleNamespace(TextString="3V3")

        class Net:
            GetSegments = Coll([object(), object()])

            def GetLabel(self, segment: Any) -> Any:  # noqa: N802 - COM name
                return label

        self.assertEqual(designer._net_name(Net()), "3V3")
        named = Net()
        named.Name = "VCC"
        self.assertEqual(designer._net_name(named), "VCC")  # a direct name wins
        label.TextString = ""
        self.assertEqual(designer._net_name(Net()), "")
        self.assertEqual(designer._net_name(SimpleNamespace()), "")  # a net without segments

    def test_an_unnamed_net_reads_as_its_uid(self):
        net = SimpleNamespace(UID="$2N121")
        self.assertEqual(designer._net_identity(net), "$2N121")
        self.assertEqual(designer._net_identity(SimpleNamespace(LogicalNetName="GND", Name="x")), "GND")

    def test_sheet_size_codes_read_as_their_names(self):
        self.assertEqual([designer._size_name(c) for c in (0, 2, 5, 13, 16, 20, 25, 99)],
                         ["A", "C", "A4", "C", "A4", "A0", "E", "code 99"])

    def test_sch2pdf_reports_each_printed_sheet(self):
        stdout = "SCH2PDF - V6.0\nOrdering pages\nPrinted Schematic1 Sheet 1\nPrinted Schematic1 Sheet 2\nSaving document\n"
        self.assertEqual(designer._printed_sheets(stdout), ["Schematic1 Sheet 1", "Schematic1 Sheet 2"])

    def test_a_list_entry_counts_only_at_a_folder_boundary(self):
        entries = ["SymbolLibs\\MyPartQuest", "$LIB\\SymbolLibs\\Globals\\"]
        self.assertFalse(designer._listed(entries, "SymbolLibs/PartQuest"))
        self.assertTrue(designer._listed(entries, "SymbolLibs/Globals"))

    def test_the_launcher_is_not_a_designer(self):
        home = Path("C:/xpedition/SDD_HOME")
        found = [processes.ProcessInfo(1, str(home / "common" / "win64" / "bin" / "viewdraw.exe"), 1),
                 processes.ProcessInfo(2, str(home / "wv" / "win64" / "bin" / "viewdraw.exe"), 1)]
        with mock.patch.object(processes, "processes", lambda image: found), mock.patch.object(env, "find_sdd_home", lambda: home):
            self.assertEqual([p.pid for p in designer._designer_processes()], [2])

    def test_version_is_read_when_asked_for(self):
        with mock.patch.object(designer, "_VERSION", "2604"):
            self.assertEqual(designer.VERSION, "2604")
        with self.assertRaises(AttributeError):
            designer.NO_SUCH_THING  # noqa: B018 - the module's __getattr__


class SymbolPlanTest(unittest.TestCase):
    def test_a_changed_symbol_gets_the_next_version_and_an_identical_one_is_left(self):
        with tempfile.TemporaryDirectory() as folder:
            sym = Path(folder)
            (sym / "PWR_A.1").write_text("old", encoding="utf-8")
            (sym / "PWR_A.2").write_text("older", encoding="utf-8")
            (sym / "RES_B.1").write_text("same\n", encoding="utf-8")
            planned = designer._symbol_plan(sym, {"PWR_A": "new", "RES_B": "same\n", "CAP_C": "fresh"})
            self.assertEqual(sorted(path.name for path, _ in planned), ["CAP_C.1", "PWR_A.3"])

    def test_a_symbol_name_that_leaves_the_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(AdapterError):
            designer._symbol_plan(Path(folder), {"../escape": "x"})


# ------------------------------------------------------------------ session functions


class InstancesTest(DesignerCase):
    def test_one_ready_designer_reports_its_project(self):
        [found] = designer.instances(2.0)
        self.assertEqual(found, {"pid": PID, "state": "ready", "documents": [self.prj], "version": "2604"})

    def test_with_several_designers_no_open_project_is_read(self):
        self.procs.append(processes.ProcessInfo(PID + 1, "C:/xpedition/wv/viewdraw.exe", 2))
        self.assertIsNone(designer.open_documents(PID, 1.0))  # what each last showed stands

    def test_a_designer_without_a_project_lists_no_document(self):
        self.app.project, self.app.opened = "", False
        self.assertEqual(designer.instances(2.0)[0]["documents"], [])

    def test_a_designer_that_does_not_answer_is_unresponsive(self):
        def late(fn: Any, timeout: float) -> Any:
            raise TimeoutError("late")

        with mock.patch.object(com, "call_with_timeout", late):
            self.assertEqual(designer.instances(2.0)[0]["state"], "unresponsive")

    def test_a_designer_not_registered_yet_is_starting(self):
        with mock.patch.object(designer, "_attach_or_none", lambda: None):
            self.assertEqual(designer.instances(2.0)[0]["state"], "starting")

    def test_a_visible_prompt_blocks_the_session(self):
        self.dialogs = [{"title": "Xpedition Designer", "text": "Close all open documents?", "buttons": ["Yes", "No"]}]
        [found] = designer.instances(2.0)
        self.assertEqual(found["state"], "blocked")
        self.assertEqual(found["blocker"], {"action": "dismiss_dialog", "title": "Xpedition Designer",
                                            "knowledge": [designer.PROMPTS]})

    def test_licence_and_sign_in_windows_say_what_the_person_must_do(self):
        self.licence = ["Sign in to continue"]
        self.assertEqual(designer.instances(2.0)[0]["blocker"]["action"], "sign_in")
        self.licence = ["License Error"]
        self.assertEqual(designer.instances(2.0)[0]["blocker"]["action"], "activate_license")

    def test_several_designers_are_each_unreachable_and_none_is_attached(self):
        self.procs.append(processes.ProcessInfo(5000, "C:/xpedition/wv/viewdraw.exe", 2))
        with mock.patch.object(designer, "_attach_or_none", side_effect=AssertionError("attached")):
            found = designer.instances(2.0)
        self.assertEqual([f["state"] for f in found], ["unreachable", "unreachable"])
        self.assertEqual(found[0]["reason"], designer.SEVERAL)


class DocumentsTest(DesignerCase):
    def test_opening_the_open_project_sends_nothing(self):
        designer.open_document(PID, self.prj)
        self.assertEqual(self.app.opened_paths, [])  # opening it again is refused with 64185

    def test_another_open_project_is_refused_not_closed(self):
        self.app.project = str(self.root / "Other.prj")
        with self.assertRaises(AdapterError) as caught:
            designer.open_document(PID, self.prj)
        self.assertEqual((caught.exception.data["conflict"], caught.exception.data["dispatch_state"]), ("application_state", "not_started"))
        self.assertIn("doc close", str(caught.exception))
        self.assertEqual((self.app.closed, self.app.opened_paths), (0, []))

    def test_a_project_opens_and_its_design_view_follows(self):
        text = PRJ_TEXT.replace('KEY ConfigType "PCB"\n', 'KEY ConfigType "PCB"\nKEY RootBlock "Schematic1"\n')
        Path(self.prj).write_text(text, encoding="utf-8")
        self.app.project, self.app.opened, self.app.view = "", False, False
        designer.open_document(PID, self.prj)
        self.assertEqual(self.app.opened_paths, [self.prj])
        # sheet 1 of the root block opens; Documents.Open with the design's name would create an empty schematic
        self.assertEqual(([d.number for d in self.app.open_docs], self.app.design_opened), ([1], None))

    def test_a_dialog_during_the_open_needs_a_person(self):
        self.app.project, self.app.opened = "", False
        release = threading.Event()
        self.addCleanup(release.set)
        self.app.on_open = lambda: release.wait(5)
        self.dialogs = [{"title": "Xpedition Designer", "text": "Something else?", "buttons": ["OK"]}]
        with self.assertRaises(AdapterError) as caught:
            designer.open_document(PID, self.prj)
        data = caught.exception.data
        self.assertEqual((data["code"], data["dispatch_state"], data["action"]), ("E_HUMAN_REQUIRED", "dispatched", "dismiss_dialog"))
        self.assertEqual(data["title"], "Xpedition Designer")

    def test_save_saves_every_open_document(self):
        designer.save_document(PID, self.prj)
        self.assertEqual(self.app.saved, 1)
        with self.assertRaises(AdapterError):
            designer.save_document(PID, str(self.root / "Other.prj"))

    def test_close_never_saves(self):
        designer.close_document(PID, self.prj, False)
        self.assertEqual((self.app.closed, self.app.saved), (1, 0))
        designer.close_document(PID, self.prj, False)  # already closed: nothing sent
        self.assertEqual(self.app.closed, 1)

    def test_a_second_designer_refuses_document_work(self):
        self.procs.append(processes.ProcessInfo(5000, "C:/xpedition/wv/viewdraw.exe", 2))
        with self.assertRaises(AdapterError) as caught:
            designer.save_document(PID, self.prj)
        self.assertEqual(caught.exception.data["knowledge"], [designer.ONE_INSTANCE])

    def test_read_targets_reads_the_open_project(self):
        self.app.components = [Part("R1", [("1", "VCC"), ("2", "GND")], "10k")]
        out = designer.read_targets(PID, self.prj, [designer._net_key(self.prj, "GND")], ["pins"])
        self.assertEqual(out["observed"], {designer._net_key(self.prj, "GND"): {"pins": ["R1.2"]}})


class QuitTest(DesignerCase):
    def test_quit_waits_for_the_process_to_go(self):
        alive = [True]
        self.app.on_quit = lambda: alive.__setitem__(0, False)
        with mock.patch.object(processes, "alive", lambda pid: alive[0]):
            designer.quit(PID, False)
        self.assertEqual(self.app.quits, 1)

    def test_a_designer_held_by_a_dialog_names_it(self):
        with mock.patch.object(processes, "alive", lambda pid: True), mock.patch.object(designer, "QUIT_WAIT_S", 0.05), \
                mock.patch.object(designer, "_dialog_blocker", lambda pid, look_s: {"action": "dismiss_dialog", "title": "Save?"}):
            with self.assertRaises(AdapterError) as caught:
                designer.quit(PID, False)
        self.assertEqual((self.code(caught), caught.exception.data["title"]), ("E_HUMAN_REQUIRED", "Save?"))


class StartTest(DesignerCase):
    def test_a_running_designer_is_used_instead_of_a_second(self):
        with self.assertRaises(AdapterError) as caught:
            designer.start(self.prj, 40.0)
        self.assertEqual((caught.exception.data["conflict"], caught.exception.data["knowledge"]), ("application_state", [designer.ONE_INSTANCE]))

    def test_the_launcher_starts_designer_and_the_project_opens(self):
        launcher = self.root / "SDD_HOME" / "common" / "win64" / "bin" / "viewdraw.exe"
        calls: list[dict[str, Any]] = []
        found = iter([[], [], [processes.ProcessInfo(PID, "C:/xpedition/wv/viewdraw.exe", 1, parent_pid=99)]])
        last: list[processes.ProcessInfo] = []

        def processes_now() -> list[processes.ProcessInfo]:
            try:
                last[:] = next(found)
            except StopIteration:
                pass
            return list(last)

        def popen(args: list[str], **kwargs: Any) -> Any:
            calls.append({"args": args, **kwargs})
            return SimpleNamespace(pid=99, poll=lambda: 0)

        self.app.project, self.app.opened = "", False
        with mock.patch.object(designer, "_designer_processes", processes_now), \
                mock.patch.object(env, "configure_environment", lambda: None), \
                mock.patch.object(env, "launcher", lambda name: launcher), \
                mock.patch.object(designer.subprocess, "Popen", popen), \
                mock.patch.object(processes, "alive", lambda pid: True):
            started = designer.start(self.prj, 40.0)
        self.assertEqual(started, {"pid": PID, "state": "ready"})
        self.assertEqual(calls[0]["args"], [str(launcher)])
        self.assertEqual(calls[0]["cwd"], str(launcher.parent))
        if os.name == "nt":
            self.assertEqual(calls[0]["creationflags"], designer.subprocess.DETACHED_PROCESS | designer.subprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertEqual(self.app.opened_paths, [self.prj])


# ------------------------------------------------------------------ schematic get


class SchematicGetTest(DesignerCase):
    def setUp(self) -> None:
        super().setUp()
        self.app.blocks = {1: Block(1), 2: Block(2)}
        self.app.blocks[1].size, self.app.blocks[2].size = 5, 13
        self.app.components = [
            Part("R1", [("1", "+3V3"), ("2", "LED_A")], "10k"),
            Part("D1", [("1", "LED_A"), ("2", None, 100, 50)], "LED-RED"),
            Part("", x=100, y=50),  # a no-connect mark: not a component
        ]

    def test_the_sheets_their_sizes_and_the_counts(self):
        result = designer.HANDLERS[0].read(self.ctx("schematic get"))
        self.assertEqual(result["design"], "Board1")
        self.assertEqual(result["sheets"], [{"number": 1, "name": "Schematic1.1", "size": "A4"},
                                            {"number": 2, "name": "Schematic1.2", "size": "C"}])
        self.assertEqual((result["components"], result["nets"]), (2, 2))
        self.assertEqual(self.app.active, 1)  # the sheet that was active is brought back

    def test_after_sheet_changes_the_read_asks_for_a_reopen(self):
        self.app.components_error = type_mismatch()
        self.app.active_design = "Schematic1"
        with self.assertRaises(AdapterError) as caught:
            designer.HANDLERS[0].read(self.ctx("schematic get"))
        self.assertEqual((caught.exception.data["conflict"], caught.exception.data["knowledge"]), ("application_state", [designer.REOPEN]))
        self.assertIn("doc close", str(caught.exception))

    def test_a_type_mismatch_alone_names_both_causes(self):
        self.app.components_error = type_mismatch()
        with self.assertRaises(AdapterError) as caught:
            designer.HANDLERS[0].read(self.ctx("schematic get"))
        self.assertEqual(caught.exception.data["knowledge"], [designer.REOPEN])
        self.assertIn("packager run", str(caught.exception))

    def test_another_fault_is_a_native_failure(self):
        self.app.components_error = ComError(-2147418113, "Catastrophic failure")
        with self.assertRaises(AdapterError) as caught:
            designer.HANDLERS[0].read(self.ctx("schematic get"))
        self.assertEqual(self.code(caught), "E_NATIVE_FAILURE")

    def test_the_document_must_be_the_open_project(self):
        self.app.project = str(self.root / "Other.prj")
        with self.assertRaises(AdapterError) as caught:
            designer.HANDLERS[0].read(self.ctx("schematic get"))
        self.assertIn("doc open", str(caught.exception))

    def test_it_runs_in_a_designer_session(self):
        ctx = base.Context(self.store, {"command": "schematic get", "inputs": {}, "document": self.prj})
        with self.assertRaises(AdapterError):
            designer.HANDLERS[0].read(ctx)


# ------------------------------------------------------------------ components and nets


def placed(refdes: str, sheet: str, pins: list[tuple], attributes: dict[str, str]) -> Part:
    """A component on a sheet whose pins carry names: ``pins`` holds (number, name, net[, x, y])."""
    part = Part(refdes, [(number, net, *rest) for number, _name, net, *rest in pins], attributes.get("Part Number", ""))
    part.Attributes = Coll([Attr(name, value) for name, value in attributes.items()])
    part.Parent = SimpleNamespace(SheetNum=sheet)
    for (_number, name, *_rest), conn in zip(pins, part._connections.items):
        conn.CompPin.Pin = SimpleNamespace(GetName=lambda flag, name=name: name)
    return part


class ComponentNetReadTest(DesignerCase):
    def setUp(self) -> None:
        super().setUp()
        self.app.components = [
            placed("R10", "2", [("1", "1", "+3V3"), ("2", "2", "LED_A")],
                   {"Part Number": "RES-10K", "Value": "10k", "@Sheet": "2", "Ref Designator": ""}),
            placed("R2", "1", [("2", "2", "LED_A"), ("1", "1", "$1N7")], {"Part Number": "RES-1K", "Value": "1k"}),
            placed("U1", "1", [("10", "GND", "GND"), ("2", "VBUS", "+3V3"), ("1", "NC", None, 300, 40)],
                   {"Part Number": "MCU-1", "Description": "Controller"}),
            Part("", x=300, y=40),  # a no-connect mark on U1 pin 1
        ]

    def read(self, command: str, **inputs: Any) -> dict[str, Any]:
        handler = next(h for h in designer.HANDLERS if h.command == command)
        return handler.read(self.ctx(command, inputs))

    def test_component_list_in_reference_order_with_values_sheets_and_counts(self):
        items = self.read("component list")["items"]
        self.assertEqual([i["refdes"] for i in items], ["R2", "R10", "U1"])
        self.assertEqual(items[1], {"refdes": "R10", "part_number": "RES-10K", "value": "10k", "sheet": 2, "pins": 2,
                                    "connected": 2})
        self.assertEqual((items[2]["pins"], items[2]["connected"]), (3, 2))
        self.assertEqual([i["refdes"] for i in self.read("component list", sheet=1)["items"]], ["R2", "U1"])

    def test_component_get_attributes_and_pins_with_names_nets_and_no_connects(self):
        found = self.read("component get", refdes="u1")
        self.assertEqual((found["refdes"], found["part_number"], found["value"], found["sheet"]), ("U1", "MCU-1", "", 1))
        self.assertEqual(found["attributes"], {"Part Number": "MCU-1", "Description": "Controller"})
        self.assertEqual(found["pins"], [{"number": "1", "name": "NC", "net": None, "no_connect": True},
                                         {"number": "2", "name": "VBUS", "net": "+3V3", "no_connect": False},
                                         {"number": "10", "name": "GND", "net": "GND", "no_connect": False}])
        self.assertEqual(self.read("component get", refdes="R10")["attributes"], {"Part Number": "RES-10K", "Value": "10k"})

    def test_component_get_refuses_a_missing_and_a_repeated_reference(self):
        with self.assertRaises(AdapterError) as caught:
            self.read("component get", refdes="C1")
        self.assertEqual(self.code(caught), "E_NOT_FOUND")
        self.assertIn("component list", str(caught.exception))
        self.app.components.append(placed("R2", "3", [("1", "1", "X")], {"Part Number": "RES-2K"}))
        with self.assertRaises(AdapterError) as caught:
            self.read("component get", refdes="R2")
        self.assertEqual(self.code(caught), "E_AMBIGUOUS")
        self.assertEqual(caught.exception.data["candidates"], [{"refdes": "R2", "sheet": 1, "part_number": "RES-1K"},
                                                               {"refdes": "R2", "sheet": 3, "part_number": "RES-2K"}])

    def test_net_list_counts_pins_and_sheets_named_nets_first(self):
        items = self.read("net list")["items"]
        self.assertEqual(items, [{"name": "+3V3", "pins": 2, "unnamed": False, "sheets": [1, 2]},
                                 {"name": "GND", "pins": 1, "unnamed": False, "sheets": [1]},
                                 {"name": "LED_A", "pins": 2, "unnamed": False, "sheets": [1, 2]},
                                 {"name": "$1N7", "pins": 1, "unnamed": True, "sheets": [1]}])

    def test_net_get_lists_the_pins_it_joins(self):
        found = self.read("net get", name="led_a")
        self.assertEqual(found["name"], "LED_A")
        self.assertEqual(found["pins"], [{"refdes": "R2", "number": "2", "name": "2", "part_number": "RES-1K", "sheet": 1},
                                         {"refdes": "R10", "number": "2", "name": "2", "part_number": "RES-10K", "sheet": 2}])
        with self.assertRaises(AdapterError) as caught:
            self.read("net get", name="VBUS")
        self.assertEqual(self.code(caught), "E_NOT_FOUND")
        self.assertIn("net list", str(caught.exception))

    def test_the_reads_need_the_open_project(self):
        self.app.project = str(self.root / "Other.prj")
        for command, inputs in (("component list", {}), ("component get", {"refdes": "U1"}), ("net list", {}),
                                ("net get", {"name": "GND"})):
            with self.assertRaises(AdapterError):
                self.read(command, **inputs)


class BomTest(DesignerCase):
    def setUp(self) -> None:
        super().setUp()
        self.app.components = [
            placed("R1", "1", [("1", "1", "5V")], {"Part Number": "RES-1K", "Value": "1k"}),
            placed("R2", "1", [("1", "1", "5V")], {"Part Number": "RES-1K", "Value": "1k"}),
            placed("U1", "2", [("1", "1", "GND")], {"Part Number": "MCU-9"}),
            placed("TP1", "2", [("1", "1", "GND")], {}),
        ]
        self.library: tuple = ({"RES-1K": "Resistor, 1 kOhm"}, "")
        patcher = mock.patch.object(designer, "_library_index", lambda prj: self.library)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.out = str(self.root / "out" / "bom.csv")

    def handler(self, command: str) -> Any:
        return next(h for h in designer.HANDLERS if h.command == command)

    def test_bom_check_finds_unknown_and_missing_part_numbers(self):
        result = self.handler("bom check").read(self.ctx("bom check"))
        self.assertEqual([f["rule"] for f in result["findings"]], ["missing_part_number", "not_in_library"])
        self.assertEqual((result["errors"], result["warnings"], result["components"], result["part_numbers"]), (2, 0, 4, 2))
        self.assertTrue(result["library_checked"])

    def test_bom_check_without_the_library_says_so(self):
        self.library = (None, "The converters could not export parts PartQuest of the central library.")
        result = self.handler("bom check").read(self.ctx("bom check"))
        self.assertEqual([f["rule"] for f in result["findings"]], ["missing_part_number"])
        self.assertFalse(result["library_checked"])
        self.assertIn("PartQuest", result["notes"][0])

    def test_bom_export_preview_names_the_file(self):
        out = self.handler("bom export").preview(self.ctx("bom export", {"output": self.out}))
        self.assertIn("one row per part number and value", out["action"])
        self.assertEqual(out["targets"][0]["key"], base.key(self.out))

    def test_bom_export_refuses_a_wrong_name_or_baseline(self):
        handler = self.handler("bom export")
        cases = [({"output": str(self.root / "bom.xlsx")}, "E_VALIDATION"),
                 ({"output": self.out, "baseline": str(self.root / "old.csv")}, "E_NOT_FOUND")]
        for inputs, code in cases:
            with self.assertRaises(AdapterError) as caught:
                handler.preview(self.ctx("bom export", inputs))
            self.assertEqual(self.code(caught), code)
        Path(self.out).parent.mkdir()
        Path(self.out).write_text("x", encoding="utf-8")
        with self.assertRaises(AdapterError) as caught:
            handler.preview(self.ctx("bom export", {"output": self.out, "baseline": self.out}))
        self.assertEqual(self.code(caught), "E_VALIDATION")

    def test_bom_export_refuses_a_file_another_program_holds(self):
        win32file = __import__("win32file") if os.name == "nt" else None
        if win32file is None:
            self.skipTest("locking a file needs Windows")
        Path(self.out).parent.mkdir()
        Path(self.out).write_text("x", encoding="utf-8")
        handle = win32file.CreateFile(self.out, win32file.GENERIC_READ, win32file.FILE_SHARE_READ, None,
                                      win32file.OPEN_EXISTING, 0, None)
        try:
            with self.assertRaises(AdapterError) as caught:
                self.handler("bom export").preview(self.ctx("bom export", {"output": self.out}))
        finally:
            handle.Close()
        self.assertEqual((self.code(caught), caught.exception.data["conflict"]), ("E_CONFLICT", "target_locked"))

    def test_bom_export_writes_the_csv_and_compares_with_a_baseline(self):
        baseline = self.root / "old.csv"
        baseline.write_text("Reference,Part Number,Value\nR1-R3,RES-1K,1k\n", encoding="utf-8")
        facts = self.handler("bom export").apply(self.ctx("bom export", {"output": self.out, "baseline": str(baseline)}))
        result = facts["result"]
        text = Path(self.out).read_bytes()
        self.assertTrue(text.startswith(b"\xef\xbb\xbf"))
        lines = text.decode("utf-8-sig").splitlines()
        self.assertEqual(lines[1], '1,2,"R1, R2",RES-1K,1k,"Resistor, 1 kOhm"')
        self.assertEqual((result["rows"], result["components"], result["part_numbers"]), (3, 4, 2))
        self.assertIn("1 component(s) have no part number", result["warnings"][0])
        self.assertEqual(result["changes"]["added"], [{"refdes": "TP1", "part_number": ""}, {"refdes": "U1", "part_number": "MCU-9"}])
        self.assertEqual(result["changes"]["removed"], [{"refdes": "R3", "part_number": "RES-1K"}])
        self.assertEqual(facts["persistence"], "saved")
        self.assertFalse(list(Path(self.out).parent.glob("*.partial-*")))

    def test_bom_export_one_row_per_component_and_no_library(self):
        self.library = (None, "The central library could not be read: no export.")
        result = self.handler("bom export").apply(self.ctx("bom export", {"output": self.out, "per_component": True}))["result"]
        self.assertEqual(result["rows"], 4)
        self.assertIn("Description column is empty", result["warnings"][0])
        self.assertEqual(Path(self.out).read_text(encoding="utf-8-sig").splitlines()[0], ",".join(bom.COMPONENT_COLUMNS))

    def test_bom_export_read_back_checks_the_file(self):
        ctx = self.ctx("bom export", {"output": self.out}, targets=[base.key(self.out)])
        self.assertEqual(self.handler("bom export").read_back(ctx)["observed"][base.key(self.out)], {"exists": False})


# ------------------------------------------------------------------ schematic create


class CreatePreviewTest(DesignerCase):
    def preview(self, design: dict[str, Any] | None = None) -> dict[str, Any]:
        return designer.SchematicCreate().preview(self.ctx("schematic create", {"design": self.design_file(design)}))

    def test_every_planned_net_component_and_symbol_file_is_a_change(self):
        out = self.preview()
        self.assertTrue(out["can_apply"], out.get("blocked_by"))
        plan = L.plan_to_params(json.loads(DEMO.read_text(encoding="utf-8")), self.prj)
        nets = {c["target"]: c["after"] for c in out["changes"] if c["field"] == "pins"}
        self.assertEqual(nets, {designer._net_key(self.prj, n): sorted(p) for n, p in plan["verify"]["nets"].items()})
        parts = {c["target"]: c["after"] for c in out["changes"] if c["field"] == "part_number"}
        self.assertEqual(len(parts), 30)
        files = [c for c in out["changes"] if c["field"] == "exists"]
        self.assertEqual(len(files), len(plan["symbols"]))
        self.assertTrue(all(c["target"].endswith(".1") and c["after"] is True for c in files))
        self.assertEqual([t["label"] for t in out["targets"] if t["kind"] == "sheet"], ["sheet 1", "sheet 2", "sheet 3", "sheet 4"])
        self.assertIn("nothing is saved", out["action"])
        self.assertFalse(self.sym_folder().exists(), "a preview writes nothing")

    def test_an_identical_symbol_already_written_is_no_change(self):
        plan = L.plan_to_params(json.loads(DEMO.read_text(encoding="utf-8")), self.prj)
        name, text = sorted(plan["symbols"].items())[0]
        self.sym_folder().mkdir(parents=True)
        (self.sym_folder() / f"{name}.1").write_text(text, encoding="utf-8")
        files = [c["target"] for c in self.preview()["changes"] if c["field"] == "exists"]
        self.assertEqual(len(files), len(plan["symbols"]) - 1)
        self.assertFalse(any(f"/{name}." in target for target in files))

    def test_a_partition_the_project_does_not_list_blocks_the_draw(self):
        Path(self.prj).write_text(PRJ_TEXT.replace('VALUE "SymbolLibs\\PartQuest"\n', ""), encoding="utf-8")
        out = self.preview()
        self.assertFalse(out["can_apply"])
        self.assertIn("LIST Symbols: SymbolLibs\\PartQuest", out["blocked_by"][0])
        self.assertIn("part create", out["blocked_by"][0])

    def test_a_label_on_another_nets_wire_end_blocks_the_draw(self):
        real = L.plan_to_params

        def with_issue(*args: Any) -> dict[str, Any]:
            params = real(*args)
            params["summary"]["issues"] = [{"check": "DS-15", "sheet": 1, "message": "label box GND covers the end of net SDA"},
                                           {"check": "DS-08", "sheet": 2, "message": "closer than 30 units"}]
            return params

        with mock.patch.object(designer.L, "plan_to_params", with_issue):
            out = self.preview()
        self.assertEqual(out["blocked_by"], ["DS-15 on sheet 1: label box GND covers the end of net SDA"])
        self.assertIn("1 layout warnings", out["action"])

    def test_an_unplannable_design_is_refused(self):
        with self.assertRaises(AdapterError) as caught:
            self.preview({"sheets": []})
        self.assertEqual(self.code(caught), "E_VALIDATION")
        with self.assertRaises(AdapterError) as caught:
            designer.SchematicCreate().preview(self.ctx("schematic create", {"design": str(self.root / "absent.json")}))
        self.assertEqual(self.code(caught), "E_NOT_FOUND")

    def library_design(self) -> dict[str, Any]:
        return {"sheet_size": "B", "symbols": {"LDO": {"part": "LDO-3V3"}}, "sheets": [{"number": 1, "blocks": [
            {"kind": "ic", "refdes": "U1", "symbol": "LDO", "x": 600, "y": 500,
             "pins": {"1": "power:+5V", "2": "label:LDO_EN", "3": "power:+3V3", "4": "gnd"}},
            {"kind": "ladder", "x": 300, "y": 500, "path": ["label:LDO_EN", {"refdes": "R1", "symbol": "RES", "value": "10k"}, "gnd"]}]}]}

    def fake_library(self) -> library_read.Library:
        text = S.box("LDOX", left=[("1", "VIN"), ("2", "EN")], right=[("3", "VOUT")], bottom=[("4", "GND")]).render()
        path = self.root / "Lib" / "SymbolLibs" / "Regulators" / "sym" / "LDOX.1"
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding="utf-8")
        return library_read.Library(
            parts=[{"number": "LDO-3V3", "partition": "Regulators", "symbols": ["Regulators:LDOX"], "cell": "SOT23-5"}],
            symbols=[{"partition": "Regulators", "name": "LDOX", "path": str(path)}])

    def test_a_central_library_part_is_placed_from_its_own_partition(self):
        held = self.fake_library()
        asked: list[Any] = []

        def load_library(prj: Any, *, fresh: bool = False) -> library_read.Library:
            asked.append(prj)
            return held

        with mock.patch.object(library_module, "load_library", load_library, create=True):
            out = self.preview(self.library_design())
        self.assertEqual([str(p) for p in asked], [self.prj])
        self.assertIn({"target": designer._component_key(self.prj, "U1"), "field": "part_number", "before": None,
                       "after": "LDO-3V3"}, out["changes"])
        # the library part's partition must be registered too
        self.assertFalse(out["can_apply"])
        self.assertIn("SymbolLibs\\Regulators", out["blocked_by"][0])
        self.assertIn("PartsDBLibs\\Regulators.pdb", out["blocked_by"][0])

    def test_a_part_the_library_does_not_hold_is_refused(self):
        held = library_read.Library()
        with mock.patch.object(library_module, "load_library", lambda prj, fresh=False: held, create=True):
            with self.assertRaises(AdapterError) as caught:
                self.preview(self.library_design())
        self.assertIn("LDO-3V3", str(caught.exception))


class CreateRunTest(DesignerCase):
    def run_job(self, design: dict[str, Any] | None = None, progress: FakeProgress | None = None) -> dict[str, Any]:
        ctx = self.ctx("schematic create", {"design": self.design_file(design)})
        return designer.SchematicCreate().run(ctx, progress or FakeProgress())

    def test_the_demo_is_drawn_sheet_by_sheet_and_nothing_is_saved(self):
        progress = FakeProgress()
        out = self.run_job(progress=progress)
        self.assertEqual((out["change"], out["persistence"]), ("applied", "unchanged"))
        result = out["result"]
        self.assertEqual((result["sheets"], result["parts"], result["nets"]), ([1, 2, 3, 4], 30, 11))
        plan = L.plan_to_params(json.loads(DEMO.read_text(encoding="utf-8")), self.prj)
        drawn = [op for op in plan["ops"] if op["op"] != "save"]
        self.assertEqual(result["operations"], len(drawn))
        # sheets 2-4 came from the New Sheet command, each wiped and set to A4 landscape
        self.assertEqual(self.app.commands.count(designer.NEW_SHEET_COMMAND), 3)
        self.assertEqual(sorted(self.app.blocks), [1, 2, 3, 4])
        for block in self.app.blocks.values():
            self.assertEqual((block.deleted, block.size, block.border), (1, 5, "a4sheet"))
        self.assertEqual(sum(len(b.parts) for b in self.app.blocks.values()), 30)
        self.assertEqual(sum(len(b.wires) for b in self.app.blocks.values()) >= 61, True)
        self.assertEqual(self.app.saved, 0)  # the old per-sheet save is gone: only doc save saves
        written = sorted(Path(p).name for p in result["symbols_written"])
        self.assertEqual(written, sorted(f"{name}.1" for name in plan["symbols"]))
        self.assertTrue(all(Path(p).is_file() for p in result["symbols_written"]))
        self.assertEqual(progress.calls[-1], (len(drawn), len(drawn), "operations"))

    def test_a_part_gets_its_refdes_part_number_and_placed_attributes(self):
        self.run_job(two_sheet_design())
        [planned] = [op for op in L.plan_to_params(two_sheet_design(), self.prj)["ops"] if op.get("refdes") == "R1"]
        [r1] = self.app.blocks[1].parts
        self.assertEqual(r1.placed, ("PartQuest", "10k", planned["symbol"], planned["x"], planned["y"]))
        self.assertEqual(r1.Refdes, "R1")
        placed = {a.Name: a for a in r1.Attributes.items}
        self.assertIsNotNone(placed["Ref Designator"].location)
        self.assertEqual(placed["Ref Designator"].Origin, L.ORIGIN_MIDDLE_LEFT)

    def test_a_drop_of_the_page_size_is_retried_then_reported(self):
        self.app.blocks[1].ignore_size = 10
        out = self.run_job(two_sheet_design())
        self.assertTrue(any("page size did not take" in w for w in out["result"]["warnings"]))

    def test_a_failure_says_where_the_drawing_stopped(self):
        self.app.blocks[1].text_error = ComError(-2147467259, "Unspecified error")
        with self.assertRaises(AdapterError) as caught:
            self.run_job(two_sheet_design())
        data = caught.exception.data
        self.assertEqual((data["dispatch_state"], data["change"], data["persistence"]), ("dispatched", "partial", "unchanged"))
        self.assertIn("on sheet 1", str(caught.exception))
        self.assertIn("nothing is saved", str(caught.exception))

    def test_another_open_project_stops_the_job_before_anything_is_written(self):
        self.app.project = str(self.root / "Other.prj")
        with self.assertRaises(AdapterError) as caught:
            self.run_job(two_sheet_design())
        self.assertEqual(caught.exception.data["dispatch_state"], "not_started")
        self.assertFalse(self.sym_folder().exists())

    def test_a_blocked_plan_does_not_run(self):
        Path(self.prj).write_text(PRJ_TEXT.replace('VALUE "PartsDBLibs\\PartQuest.pdb"\n', ""), encoding="utf-8")
        with self.assertRaises(AdapterError) as caught:
            self.run_job(two_sheet_design())
        self.assertEqual(caught.exception.data["dispatch_state"], "not_started")


class DrawerTest(DesignerCase):
    def test_a_wire_attaches_its_end_pins_and_carries_its_label(self):
        drawer = designer._Drawer(self.app, "PartQuest", 7)
        ops = [{"op": "open_sheet", "number": 1},
               {"op": "place_part", "refdes": "R1", "library": "PartQuest", "symbol": "RES_1", "part": "10k", "x": 0, "y": 0},
               {"op": "place_part", "refdes": "C1", "library": "PartQuest", "symbol": "CAP_1", "part": "1u", "x": 100, "y": 0,
                "orientation": 1},
               {"op": "wire", "points": [[10, 20], [60, 20], [60, 50]], "start": "R1.1", "end": "C1.2",
                "label": {"net": "N1", "x": 35, "y": 22}}]
        drawer.run(ops, lambda done, total: None)
        first, second = self.app.blocks[1].wires
        self.assertEqual(first.args[:4] + second.args[:4], (10, 20, 60, 20, 60, 20, 60, 50))
        self.assertEqual(first.args[4].Number, "1")
        self.assertIsNone(first.args[5])
        self.assertIsNone(second.args[4])
        self.assertEqual(second.args[5].Number, "2")
        self.assertEqual((first.args[6], second.args[6]), (7, 7))
        self.assertEqual(first.labels, [("N1", 35, 22)])
        self.assertEqual(self.app.blocks[1].parts[1].Orientation, 1)

    def test_a_pin_of_a_part_not_drawn_is_refused(self):
        drawer = designer._Drawer(self.app, "PartQuest", 7)
        with self.assertRaises(AdapterError) as caught:
            drawer.run([{"op": "open_sheet", "number": 1}, {"op": "wire", "points": [[0, 0], [10, 0]], "start": "U9.1"}],
                       lambda done, total: None)
        self.assertIn("U9.1", str(caught.exception))

    def test_another_sheet_window_taking_over_is_undone(self):
        self.app.blocks[2] = Block(2)
        self.app.open_docs.append(SheetDoc(self.app, 2))
        drawer = designer._Drawer(self.app, "PartQuest", 7)

        def tick(done: int, total: int) -> None:
            if done == 1:
                self.app.active = 2  # a border change brought sheet 2 to the front

        drawer.run([{"op": "open_sheet", "number": 1}, {"op": "text", "text": "T", "x": 1, "y": 2, "size": 9}], tick)
        self.assertEqual([t.text for t in self.app.blocks[1].texts], ["T"])
        self.assertEqual(self.app.blocks[1].texts[0].Size, 9)


class JobWatchTest(DesignerCase):
    def test_a_lasting_dialog_blocks_the_job_until_it_goes(self):
        progress = FakeProgress()
        watch = designer._JobWatch(PID, designer._Progress(progress))
        self.dialogs = [{"title": "Parts database", "text": "not found", "buttons": ["OK"]}]
        watch.poll()
        self.assertEqual(progress.states, [])  # one sighting may be a window passing by
        watch.poll()
        self.assertEqual(progress.states[0][:2], ("blocked", "dismiss_dialog"))
        self.dialogs = []
        watch.poll()
        self.assertEqual(progress.states[-1], ("unblocked",))
        self.assertEqual(watch.titles, ["Parts database"])


class CreateReadBackTest(DesignerCase):
    def setUp(self) -> None:
        super().setUp()
        self.parts = [Part("R1", [("1", "+3V3"), ("2", "LED_A")], "10k"), Part("D1", [("1", "LED_A"), ("2", "GND")], "LED-RED")]
        self.app.components = list(self.parts)
        self.targets = [designer._net_key(self.prj, n) for n in ("+3V3", "LED_A", "GND", "SDA")] + \
            [designer._component_key(self.prj, "R1"), designer._component_key(self.prj, "U7")]

    def read_back(self) -> dict[str, Any]:
        ctx = self.ctx("schematic create", targets=self.targets, fields=["part_number", "pins"])
        return designer.SchematicCreate().read_back(ctx)

    def test_the_design_level_read_gives_pins_and_part_numbers(self):
        out = self.read_back()
        key = designer._net_key
        self.assertEqual(out["unreadable"], [])
        self.assertEqual(out["observed"][key(self.prj, "LED_A")], {"pins": ["D1.1", "R1.2"]})
        self.assertEqual(out["observed"][key(self.prj, "SDA")], {"pins": []})  # a net that is not there
        self.assertEqual(out["observed"][designer._component_key(self.prj, "R1")], {"part_number": "10k"})
        self.assertEqual(out["observed"][designer._component_key(self.prj, "U7")], {"part_number": None})
        self.assertEqual(self.app.closed, 0)  # never reopened

    def test_after_sheet_changes_each_sheet_is_read_and_nets_join_by_name(self):
        self.app.components_error = type_mismatch()
        self.app.active_design = "Schematic1"
        self.app.blocks = {1: Block(1), 2: Block(2)}
        self.app.blocks[1].components = [self.parts[0]]
        self.app.blocks[2].components = [self.parts[1]]
        self.app.active = 2
        self.app.open_docs = [SheetDoc(self.app, 2)]
        out = self.read_back()
        self.assertEqual(out["observed"][designer._net_key(self.prj, "LED_A")], {"pins": ["D1.1", "R1.2"]})
        self.assertEqual(self.app.active, 2)  # the sheet that was active is brought back

    def test_neither_read_working_leaves_the_fields_unreadable(self):
        self.app.components_error = type_mismatch()
        self.app.active_design = "Schematic1"
        self.app.blocks[1].components_error = ComError(-2147352570, "Unknown name.")
        out = self.read_back()
        self.assertEqual(out["unreadable"], ["part_number", "pins"])
        self.assertEqual(out["observed"], {})

    def test_a_key_in_the_runtimes_normalized_form_is_understood(self):
        key = normalize(designer._net_key(self.prj, "GND"))
        out = designer._read_fields(self.app, self.prj, [key], ["pins"], float("inf"))
        self.assertEqual(out["observed"], {key: {"pins": ["D1.2"]}})

    def test_a_symbol_file_reads_back_as_existing(self):
        path = self.sym_folder() / "PWR_A.1"
        path.parent.mkdir(parents=True)
        path.write_text("V 53\n", encoding="utf-8")
        ctx = self.ctx("schematic create", targets=[base.key(str(path)), base.key(str(path) + "x")], fields=["exists"])
        out = designer.SchematicCreate().read_back(ctx)
        self.assertEqual(list(out["observed"].values()), [{"exists": True}, {"exists": False}])


# ------------------------------------------------------------------ schematic check


VDRC = """SEVERITY: Warning
  GROUP: Interconnectivity : 1 Warning(s)
    drc-BI-GROUND - [flatnet : GND, component: U1($2I14), pin: $1P11] BI pin connected to ground
SEVERITY: Error
  GROUP: Connectivity : 1 Error(s)
    drc-SHORT - [flatnet : VCC, component: R1($1I2)] two outputs drive the net
"""
GRC = """Text alignment report
    Text overlap
        R1 value overlaps its reference designator
"""


class SchematicCheckTest(DesignerCase):
    def setUp(self) -> None:
        super().setUp()
        self.logs = self.root / "LogFiles"
        self.app.components = [Part("R1", [("1", "VCC"), ("2", None, 5, 5)], "10k"), Part("U1", [("1", "VCC")], "")]

        def verify() -> None:
            self.logs.mkdir(exist_ok=True)
            (self.logs / "vdrc.log").write_text(VDRC, encoding="utf-8")
            (self.logs / "grc.log").write_text(GRC, encoding="utf-8")

        self.app.on_verify = verify

    def test_designers_findings_and_the_netlist_rules_together(self):
        out = designer.SchematicCheck().read(self.ctx("schematic check"))
        rules = {f["rule"] for f in out["findings"]}
        self.assertIn("xpedition/verify:drc-BI-GROUND", rules)
        self.assertIn("xpedition/verify:drc-SHORT", rules)
        self.assertIn("xpedition/grc:Text overlap", rules)
        self.assertIn("cli/open-pin", rules)  # R1.2 has no net and no no-connect mark
        self.assertTrue(out["complete"])
        self.assertEqual(out["errors"], sum(f["severity"] == "error" for f in out["findings"]))
        self.assertGreaterEqual(out["errors"], 2)  # the short and the open pin
        self.assertTrue(all(f["id"].startswith("F-") for f in out["findings"]))
        self.assertIn(verification.FULL_VERIFICATION_COMMAND, self.app.commands)

    def test_a_verification_that_writes_no_log_in_time_is_reported(self):
        self.app.on_verify = None
        with mock.patch.object(designer, "VERIFY_WAIT_S", 0.05):
            out = designer.SchematicCheck().read(self.ctx("schematic check"))
        self.assertFalse(out["complete"])
        notes = [f for f in out["findings"] if f["rule"] == "xpedition-cli/incomplete"]
        self.assertIn("vdrc.log", notes[0]["message"])
        self.assertIn("cli/open-pin", {f["rule"] for f in out["findings"]})  # Designer was free: the rules ran

    def test_a_dialog_during_verification_stops_the_wait(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.app.on_verify = lambda: release.wait(5)
        self.dialogs = [{"title": "Verification", "text": "Continue?", "buttons": ["OK"]}]
        with mock.patch.object(designer, "VERIFY_WAIT_S", 1.0):
            out = designer.SchematicCheck().read(self.ctx("schematic check"))
        messages = " ".join(f["message"] for f in out["findings"])
        self.assertIn("'Verification'", messages)
        self.assertIn("still busy", messages)
        self.assertFalse(out["complete"])


# ------------------------------------------------------------------ schematic export


def fake_sch2pdf(media_box: bytes = b"[0 0 1190.55 841.89]", code: int = 0, write: bool = True, **extra: Any) -> Any:
    calls: list[dict[str, Any]] = []

    def run_tool(name: str, args: list[str], **kwargs: Any) -> env.ToolRun:
        calls.append({"name": name, "args": list(args), **kwargs})
        if write:
            Path(args[args.index("-a") + 1]).write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Page /MediaBox " + media_box + b" >> endobj\n")
        return env.ToolRun(exit_code=code, stdout="SCH2PDF - V6.0\nPrinted Schematic1 Sheet 1\nSaving document\n", stderr="", **extra)

    return run_tool, calls


class SchematicExportTest(DesignerCase):
    def setUp(self) -> None:
        super().setUp()
        self.out = str(self.root / "out" / "Demo.pdf")
        launcher = mock.patch.object(env, "launcher", lambda name: self.root / f"{name}.exe")
        launcher.start()
        self.addCleanup(launcher.stop)

    def export_ctx(self, session: bool = True) -> base.Context:
        if session:
            return self.ctx("schematic export", {"output": self.out})
        return base.Context(self.store, {"command": "schematic export", "inputs": {"output": self.out}, "document": self.prj})

    def test_sch2pdf_writes_the_pdf_and_its_pages_are_reported(self):
        run_tool, calls = fake_sch2pdf()
        with mock.patch.object(env, "run_tool", run_tool):
            facts = designer.SchematicExport().apply(self.export_ctx(session=False))
        self.assertEqual((facts["change"], facts["persistence"], facts["dispatch_state"]), ("not_applicable", "saved", "dispatched"))
        self.assertEqual(facts["files"], [{"path": self.out}])
        self.assertTrue(Path(self.out).is_file())  # the folder was created
        self.assertEqual(facts["result"]["pages"], [{"sheet": "Schematic1 Sheet 1", "width_mm": 420.0, "height_mm": 297.0}])
        [call] = calls
        args = call["args"]
        self.assertEqual(call["name"], "sch2pdf")
        self.assertEqual(args[args.index("-project") + 1], self.prj)
        self.assertEqual(args[args.index("-c") + 1], "1")
        self.assertNotEqual(args[args.index("-a") + 1], self.out)  # written beside it first
        self.assertEqual(list(Path(self.out).parent.iterdir()), [Path(self.out)])

    def test_a_portrait_page_says_its_border_is_clipped(self):
        run_tool, _ = fake_sch2pdf(b"[0 0 1224 1584]")
        with mock.patch.object(env, "run_tool", run_tool):
            facts = designer.SchematicExport().apply(self.export_ctx())
        self.assertIn("clipped", facts["result"]["warnings"][0])

    def test_an_existing_pdf_is_replaced_only_by_a_good_run(self):
        Path(self.out).parent.mkdir(parents=True)
        Path(self.out).write_bytes(b"old")
        run_tool, _ = fake_sch2pdf(code=1, write=False)
        with mock.patch.object(env, "run_tool", run_tool), self.assertRaises(AdapterError) as caught:
            designer.SchematicExport().apply(self.export_ctx())
        self.assertEqual((self.code(caught), caught.exception.data["dispatch_state"]), ("E_NATIVE_FAILURE", "dispatched"))
        self.assertEqual(Path(self.out).read_bytes(), b"old")
        run_tool, _ = fake_sch2pdf()
        with mock.patch.object(env, "run_tool", run_tool):
            designer.SchematicExport().apply(self.export_ctx())
        self.assertTrue(Path(self.out).read_bytes().startswith(b"%PDF"))

    def test_a_licence_window_and_a_timeout_are_reported(self):
        run_tool, _ = fake_sch2pdf(write=False, blocked_by_license=True)
        with mock.patch.object(env, "run_tool", run_tool), self.assertRaises(AdapterError) as caught:
            designer.SchematicExport().apply(self.export_ctx())
        self.assertEqual((self.code(caught), caught.exception.data["action"]), ("E_HUMAN_REQUIRED", "activate_license"))
        run_tool, _ = fake_sch2pdf(write=False, timed_out=True)
        with mock.patch.object(env, "run_tool", run_tool), self.assertRaises(AdapterError) as caught:
            designer.SchematicExport().apply(self.export_ctx())
        self.assertEqual(self.code(caught), "E_TIMEOUT")

    def test_the_preview_names_the_file_and_unsaved_changes(self):
        out = designer.SchematicExport().preview(self.export_ctx())
        self.assertEqual(out["changes"], [{"target": base.key(self.out), "field": "exists", "before": False, "after": True}])
        self.assertTrue(out["can_apply"])
        self.assertNotIn("unsaved", out["action"])
        sessions.note_change(self.store, SID, self.prj, [base.change(designer._net_key(self.prj, "GND"), "pins", None, ["R1.2"])])
        self.assertIn("unsaved", designer.SchematicExport().preview(self.export_ctx())["action"])

    def test_only_a_pdf_is_written(self):
        self.out = str(self.root / "out" / "Demo.svg")
        with self.assertRaises(AdapterError) as caught:
            designer.SchematicExport().preview(self.export_ctx())
        self.assertEqual(self.code(caught), "E_VALIDATION")

    def test_without_sch2pdf_the_export_is_blocked(self):
        def missing(name: str) -> Path:
            raise AdapterError("E_BACKEND_UNAVAILABLE", "This Xpedition install has no sch2pdf.exe.", dispatch_state="not_started")

        with mock.patch.object(env, "launcher", missing):
            out = designer.SchematicExport().preview(self.export_ctx())
        self.assertFalse(out["can_apply"])


class HandlerTableTest(unittest.TestCase):
    def test_the_schematic_commands_are_registered(self):
        from xpedition_cli.commands import COMMANDS

        registered = {c["path"]: c for c in COMMANDS}
        for handler in designer.HANDLERS:
            self.assertIn(handler.command, registered)
        self.assertEqual({h.command for h in designer.HANDLERS},
                         {"schematic get", "schematic create", "schematic check", "schematic export", "component list",
                          "component get", "net list", "net get", "bom check", "bom export"})
        self.assertTrue(designer.SchematicCreate.long_job)


if __name__ == "__main__":
    unittest.main()
