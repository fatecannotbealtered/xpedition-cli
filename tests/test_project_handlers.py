"""project get, project create, packager run and pcb create on temporary folders.

package.exe and JobWizard are faked at ``env.run_tool``; no Xpedition program runs.
"""
from __future__ import annotations

import os
import struct
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from xpedition_cli._driver import AdapterError
from xpedition_cli._driver.state import AdapterStore, StateDir
from xpedition_cli.adapter import base, sessions
from xpedition_cli.adapter import project as PR
from xpedition_cli.adapter.platform import env


def prj_text(library: str, pdbs: list[str] | None = None, symbols: list[str] | None = None, board: str = "") -> str:
    lines = ["SECTION DesignInfo", f'KEY CentralLibrary "{library}"', 'KEY DxD_Version "XPED2604"', "ENDSECTION",
             "SECTION iCDB", "LIST Designs", 'VALUE "Schematic1"', 'VALUE "Board1"', "ENDLIST", 'KEY iCDBDir ".\\database"',
             "ENDSECTION", "SECTION Schematic1", 'KEY ConfigType "Board1"', "ENDSECTION", "SECTION Board1", "LIST Symbols"]
    lines += [f'VALUE "SymbolLibs\\{name}"' for name in symbols or ["Globals"]]
    lines += ["ENDLIST", "LIST PDBs"] + [f'VALUE "PartsDBLibs\\{name}"' for name in pdbs or ["PartQuest.pdb"]] + ["ENDLIST"]
    lines += ["LIST 2dCellLibraries", 'VALUE "CellDBLibs\\PartQuest.cel"', "ENDLIST", 'KEY ConfigType "PCB"',
              'KEY RootBlock "Schematic1"', f'KEY PCBDesignPath "{board}"', "ENDSECTION", ""]
    return "\r\n".join(lines)


def lmc_bytes(*entries: str) -> bytes:
    out = b"\x01p\x04\x00"
    for entry in entries:
        raw = entry.encode("ascii") + b"\x00"
        out += struct.pack("<I", len(raw)) + raw
    return out


class Case(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.store = AdapterStore(StateDir(self.tmp / "state"))
        self.runs: list[tuple[str, list[str], dict[str, Any]]] = []
        for patch in (mock.patch.object(env, "configure_environment"),
                      mock.patch.object(env, "launcher", side_effect=lambda name: Path(f"C:/bin/{name}.exe")),
                      mock.patch.object(env, "find_sdd_home", return_value=None)):
            patch.start()
            self.addCleanup(patch.stop)

    def project(self, folder: Path, **options: Any) -> Path:
        library = folder / "Library"
        for name in ("PartsDBLibs", "CellDBLibs"):
            (library / name).mkdir(parents=True, exist_ok=True)
        (library / "PartsDBLibs" / "PartQuest.pdb").write_bytes(b"parts")
        (library / "CellDBLibs" / "PartQuest.cel").write_bytes(b"cells")
        (library / "Lib.lmc").write_bytes(lmc_bytes("PartsDBLibs\\PartQuest.pdb", "CellDBLibs\\PartQuest.cel", "SymbolLibs\\PartQuest"))
        path = folder / "Demo.prj"
        path.write_bytes(prj_text("Library\\Lib.lmc", **options).encode("utf-8"))
        return path

    def ctx(self, command: str, document: Path, inputs: dict[str, Any] | None = None, **request: Any) -> base.Context:
        return base.Context(self.store, {"command": command, "inputs": inputs or {}, "document": str(document), **request})

    def error(self, call: Any) -> dict[str, Any]:
        with self.assertRaises(AdapterError) as caught:
            call()
        return {**caught.exception.data, "message": str(caught.exception)}

    def fake_tool(self, behave: Any) -> None:
        def run(name: str, args: list[str], *, cwd: Any = None, timeout_s: float = 0.0, log: Any = None) -> env.ToolRun:
            self.runs.append((name, list(args), {"cwd": cwd, "timeout_s": timeout_s, "log": log}))
            return behave(name, args)

        patch = mock.patch.object(env, "run_tool", run)
        patch.start()
        self.addCleanup(patch.stop)


# ------------------------------------------------------------------ project get


class ProjectGetTest(Case):
    def test_it_reads_the_project_file_alone(self):
        prj = self.project(self.tmp / "demo")
        result = PR.ProjectGet().read(self.ctx("project get", prj))
        self.assertEqual((result["path"], result["xpedition_version"]), (str(prj), "XPED2604"))
        self.assertEqual(result["central_library"], {"path": str(self.tmp / "demo" / "Library" / "Lib.lmc"), "exists": True})
        self.assertEqual({d["name"]: d["kind"] for d in result["designs"]}, {"Schematic1": "schematic", "Board1": "board"})
        board = next(d for d in result["designs"] if d["kind"] == "board")
        self.assertEqual((board["board"], board["board_exists"], result["board"]), ("", False, ""))
        self.assertEqual(result["parts_databases"], ["PartsDBLibs\\PartQuest.pdb"])
        self.assertEqual((result["symbol_libraries"], result["cell_libraries"]), (["SymbolLibs\\Globals"], ["CellDBLibs\\PartQuest.cel"]))
        self.assertTrue(result["ascii_path"])
        self.assertEqual(self.runs, [])

    def test_it_reports_the_board_once_one_exists(self):
        prj = self.project(self.tmp / "demo", board="PCB\\Demo.pcb")
        (prj.parent / "PCB").mkdir()
        (prj.parent / "PCB" / "Demo.pcb").write_bytes(b"")
        result = PR.ProjectGet().read(self.ctx("project get", prj))
        self.assertEqual(result["board"], str(prj.parent / "PCB" / "Demo.pcb"))
        self.assertTrue(next(d for d in result["designs"] if d["kind"] == "board")["board_exists"])

    def test_a_missing_or_wrong_file(self):
        self.assertEqual(self.error(lambda: PR.ProjectGet().read(self.ctx("project get", self.tmp / "No.prj")))["code"], "E_NOT_FOUND")
        other = self.tmp / "design.json"
        other.write_text("{}", encoding="utf-8")
        self.assertEqual(self.error(lambda: PR.ProjectGet().read(self.ctx("project get", other)))["code"], "E_VALIDATION")


# ------------------------------------------------------------------ project create


class ProjectCreateTest(Case):
    def setUp(self) -> None:
        super().setUp()
        source = self.tmp / "templates" / "Tpl"
        library = source / "Library"
        files = {
            "database/icdb.dat": b"database", "Library/Lib.lmc": b"library", "Library/Lib.dbc": b"dbc",
            "Library/PartsDBLibs/PartQuest.pdb": b"parts", "Library/SymbolLibs/Globals/sym/gnd.1": b"symbol",
            "Library/Templates/Layout/4 Layer Template/template.pcb": b"template", "Library/Work/x.tmp": b"work",
            "LogFiles/a.txt": b"log", "old.bak": b"backup", "Integration/PartPkg.log": b"old report", "PCB/Board.pcb": b"board",
        }
        for name, raw in files.items():
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            (source / name).write_bytes(raw)
        text = prj_text(str(library / "Lib.lmc")).replace('KEY DxD_Version "XPED2604"', f'KEY DBCFile "{library / "Lib.dbc"}"')
        self.template = source / "Tpl.prj"
        self.template.write_bytes(text.encode("utf-8"))
        self.new = self.tmp / "work" / "demo" / "Demo.prj"

    def create(self, output: Path | None = None, **request: Any) -> base.Context:
        return self.ctx("project create", self.template, {"output": str(output or self.new)}, **request)

    def test_the_preview_names_the_new_project_and_copies_nothing(self):
        out = PR.ProjectCreate().preview(self.create())
        self.assertEqual(out["targets"], [{"key": base.key(str(self.new)), "kind": "project", "label": str(self.new)}])
        self.assertEqual(out["changes"], [{"target": base.key(str(self.new)), "field": "exists", "before": False, "after": True}])
        self.assertFalse(self.new.parent.exists())

    def test_apply_copies_renames_and_relocates_the_library_references(self):
        facts = PR.ProjectCreate().apply(self.create(operation_id="op_1"))
        self.assertEqual((facts["change"], facts["persistence"], facts["files"]), ("not_applicable", "saved", [{"path": str(self.new)}]))
        folder = self.new.parent
        self.assertTrue(self.new.is_file() and not (folder / "Tpl.prj").exists())
        self.assertTrue((folder / "database" / "icdb.dat").is_file() and (folder / "PCB" / "Board.pcb").is_file())
        for skipped in ("Library/Templates", "Library/Work", "LogFiles", "old.bak", "Integration/PartPkg.log"):
            self.assertFalse((folder / skipped).exists(), skipped)
        text = self.new.read_text(encoding="utf-8")
        self.assertIn(str(folder.resolve() / "Library" / "Lib.lmc"), text)
        self.assertIn(str(folder.resolve() / "Library" / "Lib.dbc"), text)
        self.assertNotIn(str(self.template.parent), text)
        result = facts["result"]
        self.assertEqual((result["path"], result["template"]), (str(self.new), str(self.template)))
        self.assertEqual(sorted(r["key"] for r in result["rewritten"]), ["CentralLibrary", "DBCFile"])
        self.assertEqual(result["files"], sum(1 for p in folder.rglob("*") if p.is_file()))
        self.assertGreater(result["bytes"], 0)
        back = PR.ProjectCreate().read_back(self.create(operation_id="op_1", targets=[base.key(str(self.new))], fields=["exists"]))
        self.assertEqual(back["observed"], {base.key(str(self.new)): {"exists": True}})

    def test_an_external_library_is_left_where_it_is(self):
        shared = self.tmp / "shared" / "Shared.lmc"
        shared.parent.mkdir()
        shared.write_bytes(b"shared")
        text = self.template.read_bytes().decode("utf-8").replace(str(self.template.parent / "Library" / "Lib.lmc"), str(shared))
        self.template.write_bytes(text.encode("utf-8"))
        PR.ProjectCreate().apply(self.create(operation_id="op_1"))
        self.assertIn(f'KEY CentralLibrary "{shared}"', self.new.read_text(encoding="utf-8"))

    def test_it_refuses_what_designer_could_not_use(self):
        cases = [
            (self.tmp / "b" / "B.txt", "E_VALIDATION"),
            (self.tmp / "项目" / "C.prj", "E_VALIDATION"),
            (self.template.parent / "sub" / "D.prj", "E_VALIDATION"),
            (self.template, "E_VALIDATION"),
        ]
        busy = self.tmp / "busy"
        busy.mkdir()
        (busy / "other.txt").write_text("x", encoding="utf-8")
        cases.append((busy / "E.prj", "E_VALIDATION"))
        for output, code in cases:
            error = self.error(lambda output=output: PR.ProjectCreate().preview(self.create(output)))
            self.assertEqual((error["code"], error["dispatch_state"]), (code, "not_started"), output)
        ascii_error = self.error(lambda: PR.ProjectCreate().preview(self.create(self.tmp / "项目" / "C.prj")))
        self.assertEqual(ascii_error["knowledge"], ["xpedition-ascii-paths-only"])
        missing = self.ctx("project create", self.tmp / "none.prj", {"output": str(self.new)})
        self.assertEqual(self.error(lambda: PR.ProjectCreate().preview(missing))["code"], "E_NOT_FOUND")
        self.assertFalse(self.new.parent.exists())

    def test_an_empty_folder_is_used(self):
        self.new.parent.mkdir(parents=True)
        PR.ProjectCreate().apply(self.create(operation_id="op_1"))
        self.assertTrue(self.new.is_file())

    def test_a_setup_template_or_one_without_its_database_is_refused(self):
        (self.template.parent / "database" / "icdb.dat").unlink()
        self.assertEqual(self.error(lambda: PR.ProjectCreate().preview(self.create()))["code"], "E_VALIDATION")
        setup = self.tmp / "setup" / "Default.prj"
        setup.parent.mkdir()
        setup.write_text('SECTION DesignInfo\nKEY CentralLibrary "x.lmc"\nENDSECTION\nSECTION iCDB\nKEY iCDBDir "./database"\n'
                         "ENDSECTION\n", encoding="utf-8")
        error = self.error(lambda: PR.ProjectCreate().preview(self.ctx("project create", setup, {"output": str(self.new)})))
        self.assertEqual(error["code"], "E_UNSUPPORTED")

    def test_a_partition_without_a_parts_database_gets_one_in_the_new_project(self):
        parts = self.template.parent / "Library" / "PartsDBLibs"
        for name in ("Spare1.pdb", "Spare2.pdb"):
            (parts / name).write_bytes(b"stock-empty")
        text = self.template.read_bytes().decode("utf-8")
        text = text.replace('VALUE "SymbolLibs\\Globals"', 'VALUE "SymbolLibs\\Globals"\r\nVALUE "SymbolLibs\\Case"')
        self.template.write_bytes(text.encode("utf-8"))
        facts = PR.ProjectCreate().apply(self.create(operation_id="op_1"))
        created = self.new.parent / "Library" / "PartsDBLibs" / "Case.pdb"
        self.assertEqual(created.read_bytes(), b"stock-empty")
        self.assertEqual(facts["result"]["parts_databases"], {"created": [str(created)], "registered": ["Case.pdb"]})
        self.assertFalse((parts / "Case.pdb").exists())  # the template is not touched


class RepairPartsDatabasesTest(Case):
    """A new project must not carry a symbol partition without a parts database: Designer stops
    on a dialog when it places a part of one."""

    EMPTY = b"stock-empty-parts-database"

    def make(self, symbols: list[str], pdbs: list[str], stock: dict[str, bytes], library: Path | None = None) -> Path:
        library = library or self.tmp / "RcLib"
        (library / "PartsDBLibs").mkdir(parents=True, exist_ok=True)
        for name, content in stock.items():
            (library / "PartsDBLibs" / name).write_bytes(content)
        (library / "lib.lmc").write_text("central library", encoding="utf-8")
        path = self.tmp / "Design.prj"
        lines = [f'KEY CentralLibrary "{library / "lib.lmc"}"', "SECTION Board1", "LIST Symbols"]
        lines += [f'VALUE "SymbolLibs\\{name}"' for name in symbols] + ["ENDLIST", "LIST PDBs"]
        lines += [f'VALUE "PartsDBLibs\\{name}"' for name in pdbs] + ["ENDLIST", "ENDSECTION", ""]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def test_a_user_partition_without_a_database_gets_an_empty_one(self):
        path = self.make(["Case", "Resistors", "Globals", "builtin", "Borders"], ["Resistors.pdb"],
                         {"Resistors.pdb": b"used", "Spare1.pdb": self.EMPTY, "Spare2.pdb": self.EMPTY})
        report = PR.repair_missing_parts_databases(path)
        created = self.tmp / "RcLib" / "PartsDBLibs" / "Case.pdb"
        self.assertEqual(created.read_bytes(), self.EMPTY)
        self.assertEqual((report["missing"], report["registered"]), ([], ["Case.pdb"]))
        self.assertIn('VALUE "PartsDBLibs\\Case.pdb"', path.read_text(encoding="utf-8"))

    def test_stock_partitions_are_left_alone(self):
        path = self.make(["Globals", "builtin", "Borders"], [], {"Spare1.pdb": self.EMPTY, "Spare2.pdb": self.EMPTY})
        self.assertEqual(PR.repair_missing_parts_databases(path), {"created": [], "registered": [], "missing": []})

    def test_without_two_matching_unused_databases_nothing_is_invented(self):
        path = self.make(["Case"], [], {"Only.pdb": b"could be anything"})
        report = PR.repair_missing_parts_databases(path)
        self.assertEqual((report["missing"], report["created"]), (["Case"], []))

    def test_a_database_that_exists_but_is_unlisted_is_registered(self):
        path = self.make(["Case"], [], {"Case.pdb": self.EMPTY, "Spare1.pdb": self.EMPTY, "Spare2.pdb": self.EMPTY})
        report = PR.repair_missing_parts_databases(path)
        self.assertEqual((report["created"], report["registered"]), ([], ["Case.pdb"]))

    def test_a_complete_project_is_not_rewritten(self):
        path = self.make(["Case"], ["Case.pdb"], {"Case.pdb": self.EMPTY, "Spare1.pdb": self.EMPTY, "Spare2.pdb": self.EMPTY})
        before = path.read_text(encoding="utf-8")
        self.assertEqual(PR.repair_missing_parts_databases(path), {"created": [], "registered": [], "missing": []})
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_a_project_without_a_central_library_is_not_a_failure(self):
        path = self.tmp / "Design.prj"
        path.write_text("SECTION Board1\nENDSECTION\n", encoding="utf-8")
        self.assertEqual(PR.repair_missing_parts_databases(path), {"created": [], "registered": [], "missing": []})

    def test_a_library_outside_the_project_folder_is_never_written(self):
        shared = self.tmp.parent / f"{self.tmp.name}-shared"
        self.addCleanup(lambda: __import__("shutil").rmtree(shared, ignore_errors=True))
        path = self.make(["Case"], [], {"Spare1.pdb": self.EMPTY, "Spare2.pdb": self.EMPTY}, library=shared)
        report = PR.repair_missing_parts_databases(path)
        self.assertEqual((report["created"], report["missing"]), ([], ["Case"]))
        self.assertFalse((shared / "PartsDBLibs" / "Case.pdb").exists())


# ------------------------------------------------------------------ packager run

PACKAGER_LOG = """
                                    Packager
     Common Data Base has been read
     Target PDB Name: Integration\\LocalPartsDB.pdb
     WARNING:  Something to look at.
     Packaging of the design has been completed.
"""
PACKAGER_ERRORS = """
     ERROR:  There is no Part Number: R in the Parts
      DataBase for symbols with Part Name: R and Part Label: (null).

     ERROR:  There is no Part Number: C in the Parts

     Testing of Packaging is being terminated with 2 errors and 0 warnings.
      Design has NOT been packaged.
"""


class PackagerRunTest(Case):
    def setUp(self) -> None:
        super().setUp()
        self.prj = self.project(self.tmp / "demo")
        self.log = self.prj.parent / "Integration" / "PartPkg.log"
        home = mock.patch.object(env, "sdd_home", return_value=self.tmp / "SDD_HOME")
        home.start()
        self.addCleanup(home.stop)
        environment = mock.patch.dict(os.environ, {})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)

    def packager(self, report: str | None, *, stdout: str = "", exit_code: int = 0) -> None:
        def behave(name: str, args: list[str]) -> env.ToolRun:
            if report is not None:
                self.log.parent.mkdir(parents=True, exist_ok=True)
                self.log.write_text(report, encoding="utf-8")
            return env.ToolRun(exit_code=exit_code, stdout=stdout, stderr="")

        self.fake_tool(behave)

    def test_the_preview_names_the_packaged_state(self):
        out = PR.PackagerRun().preview(self.ctx("packager run", self.prj))
        self.assertEqual(out["targets"], [{"key": base.key(str(self.prj), "/package"), "kind": "project", "label": "Board1"}])
        self.assertEqual(out["changes"], [{"target": base.key(str(self.prj), "/package"), "field": "packaged", "before": None,
                                           "after": True}])

    def test_apply_runs_the_packager_on_the_board_design_and_reads_its_report(self):
        self.log.parent.mkdir(parents=True)
        self.log.write_text("the template's old report", encoding="utf-8")
        self.packager(PACKAGER_LOG)
        facts = PR.PackagerRun().apply(self.ctx("packager run", self.prj, operation_id="op_1"))
        name, args, options = self.runs[0]
        self.assertEqual((name, args), ("package", [f"-j{self.prj}", "-nBoard1", "-Replace"]))
        self.assertEqual((Path(options["cwd"]), options["timeout_s"]), (self.prj.parent, 900.0))
        self.assertTrue(os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"].endswith(os.path.join("lib", "plugins", "platforms")))
        self.assertEqual((facts["change"], facts["persistence"]), ("applied", "saved"))
        self.assertEqual(facts["result"], {"packaged": True, "design": "Board1", "errors": [], "warnings": 1, "verdict": "",
                                           "log": str(self.log)})
        back = PR.PackagerRun().read_back(self.ctx("packager run", self.prj, operation_id="op_1"))
        self.assertEqual(back["observed"], {base.key(str(self.prj), "/package"): {"packaged": True}})

    def test_a_report_older_than_the_run_does_not_count(self):
        self.packager(PACKAGER_LOG)
        PR.PackagerRun().apply(self.ctx("packager run", self.prj, operation_id="op_1"))
        old = time.time() - 3600
        os.utime(self.log, (old, old))
        back = PR.PackagerRun().read_back(self.ctx("packager run", self.prj, operation_id="op_1"))
        self.assertFalse(back["observed"][base.key(str(self.prj), "/package")]["packaged"])

    def test_the_report_on_the_output_is_kept_when_no_log_was_written(self):
        self.packager(None, stdout=PACKAGER_LOG)
        facts = PR.PackagerRun().apply(self.ctx("packager run", self.prj, operation_id="op_1"))
        self.assertTrue(facts["result"]["packaged"] and self.log.is_file())

    def test_errors_in_the_report_refuse_the_packaging(self):
        self.packager(PACKAGER_ERRORS)
        error = self.error(lambda: PR.PackagerRun().apply(self.ctx("packager run", self.prj, operation_id="op_1")))
        self.assertEqual((error["code"], error["dispatch_state"]), ("E_VALIDATION", "dispatched"))
        self.assertIn("There is no Part Number: R", error["message"])
        self.assertIn("NOT been packaged", error["message"])
        # the fixture's LIST PDBs entry resolves from the library's folder only, which 1.0.3 rewrote
        self.assertIn("LIST PDBs names PartsDBLibs\\PartQuest.pdb", error["message"])
        back = PR.PackagerRun().read_back(self.ctx("packager run", self.prj, operation_id="op_1"))
        self.assertFalse(back["observed"][base.key(str(self.prj), "/package")]["packaged"])

    def test_a_failed_run_without_a_report(self):
        self.packager(None, exit_code=3)
        error = self.error(lambda: PR.PackagerRun().apply(self.ctx("packager run", self.prj, operation_id="op_1")))
        self.assertEqual((error["code"], error["dispatch_state"]), ("E_NATIVE_FAILURE", "dispatched"))

    def test_a_licence_window_leaves_the_outcome_open(self):
        self.fake_tool(lambda name, args: env.ToolRun(exit_code=None, stdout="", stderr="", timed_out=True, blocked_by_license=True))
        error = self.error(lambda: PR.PackagerRun().apply(self.ctx("packager run", self.prj, operation_id="op_1")))
        self.assertEqual((error["code"], error["action"], error["dispatch_state"]), ("E_HUMAN_REQUIRED", "activate_license", "dispatched"))

    def test_a_parts_database_the_library_has_not_registered_needs_the_person(self):
        prj = self.project(self.tmp / "other", pdbs=["PartQuest.pdb", "Case.pdb"])
        (prj.parent / "Library" / "PartsDBLibs" / "Case.pdb").write_bytes(b"empty")
        self.packager(PACKAGER_LOG)
        for call in (PR.PackagerRun().preview, PR.PackagerRun().apply):
            error = self.error(lambda call=call: call(self.ctx("packager run", prj, operation_id="op_1")))
            self.assertEqual((error["code"], error["action"], error["dispatch_state"]), ("E_HUMAN_REQUIRED", "other", "not_started"))
            self.assertIn("Case", error["message"])
        self.assertEqual(self.runs, [])

    def test_a_project_without_designs_has_nothing_to_package(self):
        prj = self.tmp / "Empty.prj"
        prj.write_text("SECTION iCDB\nENDSECTION\n", encoding="utf-8")
        self.assertEqual(self.error(lambda: PR.PackagerRun().preview(self.ctx("packager run", prj)))["conflict"], "application_state")


# ------------------------------------------------------------------ pcb create


class PcbCreateTest(Case):
    def setUp(self) -> None:
        super().setUp()
        self.prj = self.project(self.tmp / "demo")
        self.library = self.prj.parent / "Library"
        stock = self.tmp / "SDD_HOME" / Path(*PR.LAYOUT_TEMPLATES) / "4 Layer Template"
        stock.mkdir(parents=True)
        (stock / "template.pcb").write_bytes(b"stock template")
        self.home = self.tmp / "SDD_HOME"
        patch = mock.patch.object(env, "find_sdd_home", return_value=self.home)
        patch.start()
        self.addCleanup(patch.stop)
        self.pcb = self.prj.parent / "PCB" / "Demo.pcb"

    def jobwizard(self, log_text: str = "Successfully copied 12 files\n", create: bool = True) -> None:
        def behave(name: str, args: list[str]) -> env.ToolRun:
            Path(args[args.index("-l") + 1]).write_text(log_text, encoding="utf-8")
            if create:
                pcb = Path(args[args.index("-newpcb") + 1])
                pcb.parent.mkdir(parents=True, exist_ok=True)
                pcb.write_bytes(b"board")
                prj = Path(args[args.index("-prj") + 1])
                prj.write_text(prj.read_text(encoding="utf-8").replace('KEY PCBDesignPath ""', 'KEY PCBDesignPath "PCB\\Demo.pcb"'),
                               encoding="utf-8")
            return env.ToolRun(exit_code=0, stdout="", stderr="")

        self.fake_tool(behave)

    def test_the_preview_names_the_board_and_the_project_and_writes_nothing(self):
        out = PR.PcbCreate().preview(self.ctx("pcb create", self.prj))
        keys = [t["key"] for t in out["targets"]]
        self.assertEqual(keys, [base.key(str(self.pcb)), base.key(str(self.prj)), base.key(str(self.library / "Lib.lmc"))])
        self.assertEqual(out["changes"], [{"target": base.key(str(self.pcb)), "field": "exists", "before": False, "after": True}])
        self.assertIn("4 Layer Template", out["action"])
        self.assertFalse((self.library / "Templates").exists() or self.pcb.parent.exists())

    def test_apply_copies_the_template_runs_jobwizard_and_lists_the_cell_partitions(self):
        # a project without LIST 2dCellLibraries gains it, with the cell partition of its parts partition
        text = self.prj.read_bytes().decode("utf-8")
        self.prj.write_bytes(text.replace('LIST 2dCellLibraries\r\nVALUE "CellDBLibs\\PartQuest.cel"\r\nENDLIST\r\n', "").encode("utf-8"))
        self.assertNotIn("2dCellLibraries", self.prj.read_text(encoding="utf-8"))
        self.jobwizard()
        facts = PR.PcbCreate().apply(self.ctx("pcb create", self.prj, operation_id="op_1"))
        name, args, options = self.runs[0]
        log = self.prj.parent / "LogFiles" / "jobwizard.log"
        self.assertEqual(name, "JobWizard")
        self.assertEqual(args, ["-createnew", "-f", "-prj", str(self.prj), "-newpcb", str(self.pcb), "-template", "4 Layer Template",
                                "-lib", str(self.library / "Lib.lmc"), "-l", str(log)])
        self.assertEqual(options["timeout_s"], 240.0)
        self.assertEqual((self.library / "Templates" / "Layout" / "4 Layer Template" / "template.pcb").read_bytes(), b"stock template")
        text = self.prj.read_text(encoding="utf-8")
        self.assertLess(text.index('VALUE "Board1"'), text.index('VALUE "Schematic1"'))  # JobWizard takes the first design
        self.assertEqual((facts["change"], facts["persistence"], "files" in facts), ("applied", "saved", False))
        self.assertEqual(facts["result"], {"path": str(self.pcb), "template": "4 Layer Template", "files": 12,
                                           "cells_registered": ["CellDBLibs\\PartQuest.cel"]})
        back = PR.PcbCreate().read_back(self.ctx("pcb create", self.prj, operation_id="op_1"))
        self.assertEqual(back["observed"], {base.key(str(self.pcb)): {"exists": True}})

    def test_a_template_the_library_holds_is_not_copied_again(self):
        own = self.library / "Templates" / "Layout" / "4 Layer Template"
        own.mkdir(parents=True)
        (own / "template.pcb").write_bytes(b"own template")
        out = PR.PcbCreate().preview(self.ctx("pcb create", self.prj))
        self.assertEqual(len(out["targets"]), 2)
        self.jobwizard()
        PR.PcbCreate().apply(self.ctx("pcb create", self.prj, operation_id="op_1"))
        self.assertEqual((own / "template.pcb").read_bytes(), b"own template")

    def test_it_refuses_a_second_board_a_bad_name_or_a_project_without_a_board_design(self):
        with_board = self.project(self.tmp / "other", board="PCB\\Old.pcb")
        self.assertEqual(self.error(lambda: PR.PcbCreate().preview(self.ctx("pcb create", with_board)))["conflict"], "application_state")
        bad = self.error(lambda: PR.PcbCreate().preview(self.ctx("pcb create", self.prj, {"name": "my board"})))
        self.assertEqual((bad["code"], bad["dispatch_state"]), ("E_VALIDATION", "not_started"))
        plain = self.tmp / "Plain.prj"
        plain.write_text('SECTION iCDB\nLIST Designs\nVALUE "S1"\nENDLIST\nENDSECTION\n', encoding="utf-8")
        error = self.error(lambda: PR.PcbCreate().preview(self.ctx("pcb create", plain)))
        self.assertEqual(error["code"], "E_NOT_FOUND")
        self.assertIn("S1", error["message"])
        unknown = self.error(lambda: PR.PcbCreate().preview(self.ctx("pcb create", self.prj, {"template": "No Such Template"})))
        self.assertEqual(unknown["code"], "E_NOT_FOUND")
        self.assertIn("4 Layer Template", unknown["message"])
        self.assertEqual(self.runs, [])

    def test_a_board_jobwizard_did_not_create(self):
        self.jobwizard("未能 获取设计信息\n", create=False)
        error = self.error(lambda: PR.PcbCreate().apply(self.ctx("pcb create", self.prj, operation_id="op_1")))
        self.assertEqual((error["code"], error["dispatch_state"]), ("E_NATIVE_FAILURE", "dispatched"))
        self.assertIn("未能", error["message"])

    def test_a_board_created_with_errors_in_the_log_is_partial(self):
        self.jobwizard("Successfully copied 3 files\nfailed to copy one setup file\n")
        facts = PR.PcbCreate().apply(self.ctx("pcb create", self.prj, operation_id="op_1"))
        self.assertEqual((facts["change"], facts["persistence"]), ("partial", "partial"))
        self.assertIn("failed to copy", facts["native_message"])


if __name__ == "__main__":
    unittest.main()


class ProjectCreateInDesignerTest(unittest.TestCase):
    """project create in a Designer session: a new project from a setup template (Designer's
    NewProject is faked here)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lib = root / "Stock" / "Lib" / "Stock.lmc"
        self.lib.parent.mkdir(parents=True)
        self.lib.write_text("lmc", encoding="utf-8")
        self.template = root / "Stock" / "Default.prj"
        self.template.write_text(f'SECTION DesignInfo\nKEY CentralLibrary "{self.lib}"\nENDSECTION\n', encoding="utf-8")
        self.target = root / "work" / "Demo" / "Demo.prj"

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self, **extra):
        from xpedition_cli.adapter.base import Context

        request = {"command": "project create", "session": "designer-12-ab",
                   "inputs": {"output": str(self.target), "template": str(self.template)}, **extra}
        return Context(AdapterStore(StateDir(self.tmp.name)), request)

    def test_it_runs_designer_only_in_a_session(self):
        from xpedition_cli.adapter.base import Context

        self.assertTrue(PR.ProjectCreate().long(self.ctx()))
        self.assertFalse(PR.ProjectCreate().long(Context(mock.Mock(), {"command": "project create", "inputs": {}})))

    def test_preview_is_blocked_while_designer_has_a_project_open(self):
        out = PR.ProjectCreate().preview(self.ctx(document=str(self.template)))
        self.assertFalse(out["can_apply"])
        self.assertIn("doc close", out["blocked_by"][0])

    def test_run_creates_the_project_with_designer(self):
        def fake_new_project(pid, target, template, library, progress):
            Path(target).parent.mkdir(parents=True)
            Path(target).write_text("prj", encoding="utf-8")
            return {"designs": ["Board1"], "library": str(Path(target).parent / "Lib" / "Stock.lmc")}

        with mock.patch("xpedition_cli.adapter.designer.new_project", side_effect=fake_new_project) as called:
            out = PR.ProjectCreate().run(self.ctx(), lambda *a: None)
        self.assertEqual(called.call_args.args[:4], (12, str(self.target), str(self.template), str(self.lib)))
        self.assertEqual((out["change"], out["persistence"], out["files"]), ("not_applicable", "saved", [{"path": str(self.target)}]))
        self.assertEqual(out["result"]["designs"], ["Board1"])
        state = sessions.doc_state(AdapterStore(StateDir(self.tmp.name)), "designer-12-ab", str(self.target))
        self.assertFalse(state["dirty"])  # open in Designer and saved by the tool

    def test_a_folder_that_is_not_empty_is_refused(self):
        self.target.parent.mkdir(parents=True)
        (self.target.parent / "other.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(AdapterError) as caught:
            PR.ProjectCreate().preview(self.ctx())
        self.assertEqual(caught.exception.data["dispatch_state"], "not_started")
