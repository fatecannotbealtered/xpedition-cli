"""part list, part get and part create against a faked central library.

The converters are faked at ``env.run_tool``: a database file holds HKP text, an export copies it
and an import merges into it, so what part create writes reads back through part list. No
Xpedition program runs.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from xpedition_cli._driver import AdapterError
from xpedition_cli._driver.state import AdapterStore, StateDir
from xpedition_cli.adapter import base, jobs
from xpedition_cli.adapter import library as L
from xpedition_cli.adapter.plan import library_parts as P
from xpedition_cli.adapter.platform import env

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LIBRARY_PARTS = json.loads((FIXTURES / "library-parts.json").read_text(encoding="utf-8"))
RES, MCU = LIBRARY_PARTS["parts"]
LDO = {
    "number": "LDO-3V3",
    "description": "LDO regulator 3.3 V, SOT-23-5",
    "prefix": "U",
    "properties": {"Manufacturer": "Example"},
    "symbol": {"left": [["1", "IN"], ["3", "EN"]], "right": [["5", "OUT"], ["4", "NC"]], "bottom": [["2", "GND"]]},
    "footprint": {"family": "gullwing", "pins": 5, "positions": 6, "omit": [5], "pitch": 0.95, "span": [2.6, 3.0],
                  "terminal": [0.3, 0.6], "lead_width": [0.3, 0.5], "body": [1.6, 2.9], "height": 1.45},
}
EXPORTERS = {"PartsDB2HKP", "CellDB2HKP", "PadstackDB2HKP"}

PRJ = (
    "SECTION DesignInfo\r\n"
    'KEY CentralLibrary "Library\\Lib.lmc"\r\n'
    "ENDSECTION\r\n"
    "SECTION iCDB\r\nLIST Designs\r\n"
    'VALUE "Schematic1"\r\nVALUE "Board1"\r\nENDLIST\r\n'
    'KEY iCDBDir ".\\database"\r\nENDSECTION\r\n'
    'SECTION Schematic1\r\nKEY ConfigType "Board1"\r\nENDSECTION\r\n'
    "SECTION Board1\r\n"
    'LIST Symbols\r\nVALUE "SymbolLibs\\Globals"\r\nENDLIST\r\n'
    "LIST PDBs\r\nENDLIST\r\n"
    'KEY ConfigType "PCB"\r\nKEY RootBlock "Schematic1"\r\nKEY PCBDesignPath ""\r\n'
    "ENDSECTION\r\n"
)

CACHE_ROOT = L.cache_root  # the real location, before the tests point it at a temporary folder


def lmc_bytes(*entries: str) -> bytes:
    """A central library file as Library Manager writes its partitions: each name NUL-terminated
    after a 4-byte length."""
    out = b"\x01p\x04\x00\x0c\x03"
    for entry in entries:
        raw = entry.encode("ascii") + b"\x00"
        out += struct.pack("<I", len(raw)) + raw
    return out


def registered(*partitions: str) -> bytes:
    entries = []
    for name in partitions:
        entries += [f"PartsDBLibs\\{name}.pdb", f"CellDBLibs\\{name}.cel", f"SymbolLibs\\{name}"]
    return lmc_bytes(*entries)


class FakeTools:
    """``env.run_tool`` for the library converters: exports copy a database's HKP text, imports
    append to it. ``fail`` maps a tool to the ToolRun fields it reports instead."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, list[str], dict[str, Any]]] = []
        self.fail: dict[str, dict[str, Any]] = {}
        self.log_lines: dict[str, str] = {}

    def __call__(self, name: str, args: list[str], *, cwd: Any = None, timeout_s: float = 0.0, log: Any = None) -> env.ToolRun:
        self.runs.append((name, list(args), {"timeout_s": timeout_s, "cwd": cwd}))
        source, output = Path(args[args.index("-i") + 1]), Path(args[args.index("-o") + 1])
        log_path = Path(args[args.index("-l") + 1]) if "-l" in args else None
        log_text = self.log_lines.get(name, "正在检查文件格式错误...\n未找到文件格式错误。\n")
        if log_path is not None:
            log_path.write_text(log_text, encoding="utf-8")
        if name in self.fail:
            return env.ToolRun(**{"exit_code": 1, "stdout": "", "stderr": "", "log_text": log_text, **self.fail[name]})
        if name in EXPORTERS:
            output.write_bytes(source.read_bytes())
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            old = output.read_text(encoding="utf-8") if output.exists() else ""
            output.write_text(old + "\n" + source.read_bytes().decode("utf-8"), encoding="utf-8")
        return env.ToolRun(exit_code=0, stdout="", stderr="", log_text=log_text if log_path else None)

    def tools(self) -> list[str]:
        return [name for name, _, _ in self.runs]

    def imports(self) -> list[tuple[str, list[str]]]:
        return [(name, args) for name, args, _ in self.runs if name not in EXPORTERS]


class LibraryCase(unittest.TestCase):
    """A project Demo.prj with its own central library holding the fixture parts."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.folder = self.tmp / "demo"
        self.root = self.folder / "Library"
        for name in ("PartsDBLibs", "CellDBLibs", "Layout", "SymbolLibs"):
            (self.root / name).mkdir(parents=True)
        self.lmc = self.root / "Lib.lmc"
        self.lmc.write_bytes(registered("PartQuest"))
        self.prj = self.folder / "Demo.prj"
        self.prj.write_bytes(PRJ.encode("utf-8"))
        plan = P.plan(LIBRARY_PARTS)
        texts = plan.texts()
        (self.root / "PartsDBLibs" / "PartQuest.pdb").write_text(texts["parts"], encoding="utf-8")
        (self.root / "CellDBLibs" / "PartQuest.cel").write_text(texts["cells"], encoding="utf-8")
        (self.root / "Layout" / "PadstackDB.psk").write_text(texts["padstacks"], encoding="utf-8")
        sym = self.root / "SymbolLibs" / "PartQuest" / "sym"
        sym.mkdir(parents=True)
        for name, text in plan.symbols.items():
            (sym / f"{name}.1").write_text(text, encoding="utf-8")
        self.store = AdapterStore(StateDir(self.tmp / "state"))
        cache = mock.patch.object(L, "cache_root", lambda: self.tmp / "cache")  # never this user's cache folder
        cache.start()
        self.addCleanup(cache.stop)
        self.tools = FakeTools()
        for patch in (mock.patch.object(env, "run_tool", self.tools), mock.patch.object(env, "configure_environment"),
                      mock.patch.object(env, "launcher", side_effect=lambda name: Path(f"C:/bin/{name}.exe")),
                      mock.patch.object(env, "find_sdd_home", return_value=None),
                      mock.patch.dict(os.environ, {jobs.WORKER_ENV: ""})):
            patch.start()
            self.addCleanup(patch.stop)

    def ctx(self, command: str, inputs: dict[str, Any] | None = None, **request: Any) -> base.Context:
        return base.Context(self.store, {"command": command, "inputs": inputs or {}, "document": str(self.prj), **request})

    def parts_file(self, *parts: dict[str, Any], partition: str = "PartQuest") -> Path:
        path = self.tmp / "parts.json"
        path.write_text(json.dumps({"partition": partition, "parts": list(parts)}, ensure_ascii=False), encoding="utf-8")
        return path

    def error(self, call: Any) -> dict[str, Any]:
        with self.assertRaises(AdapterError) as caught:
            call()
        return {**caught.exception.data, "message": str(caught.exception)}


class ExportTest(LibraryCase):
    def library(self, **options: Any) -> Any:
        return L.read_library(self.prj, L.cache_for(self.ctx("part list")), **options)

    def test_each_converter_runs_once_until_its_database_changes(self):
        (self.root / "PartsDBLibs" / "Other.pdb").write_text("", encoding="utf-8")
        library = self.library()
        self.assertEqual(sorted(self.tools.tools()), ["CellDB2HKP", "PadstackDB2HKP", "PartsDB2HKP", "PartsDB2HKP"])
        for _name, args, _ in self.tools.runs:
            self.assertEqual(args[args.index("-u") + 1], "mm")
            self.assertIn("-a", args)
        self.assertEqual(sorted(p["number"] for p in library.parts), ["MCU-8", "RES-10K"])
        self.assertEqual(library.failed, [])
        self.tools.runs.clear()
        self.library()
        self.assertEqual(self.tools.runs, [])
        (self.root / "PartsDBLibs" / "Other.pdb").write_text("a bigger database", encoding="utf-8")
        self.library()
        self.assertEqual(self.tools.tools(), ["PartsDB2HKP"])

    def test_fresh_exports_again_and_partitions_limit_the_exports(self):
        self.library()
        self.tools.runs.clear()
        self.library(fresh=True)
        self.assertEqual(sorted(self.tools.tools()), ["CellDB2HKP", "PadstackDB2HKP", "PartsDB2HKP"])
        self.tools.runs.clear()
        library = self.library(kinds=("parts",), partitions=["partquest"], symbols=False, fresh=True)
        self.assertEqual(self.tools.tools(), ["PartsDB2HKP"])
        self.assertEqual(library.symbols, [])

    def test_a_failed_converter_is_reported_and_not_cached(self):
        self.tools.fail["PartsDB2HKP"] = {"dialogs": [{"title": "PartsDB2HKP", "text": "no", "dismissed": True}]}
        library = self.library(kinds=("parts",))
        self.assertEqual(len(library.failed), 1)
        self.assertIn("no", library.failed[0]["error"])
        del self.tools.fail["PartsDB2HKP"]
        self.tools.runs.clear()
        self.assertEqual(len(self.library(kinds=("parts",)).parts), 2)
        self.assertEqual(self.tools.tools(), ["PartsDB2HKP"])

    def test_a_licence_window_needs_the_person(self):
        self.tools.fail["PartsDB2HKP"] = {"blocked_by_license": True, "dialogs": [{"title": "Licensing", "text": "", "dismissed": False}]}
        error = self.error(lambda: self.library(kinds=("parts",)))
        self.assertEqual((error["code"], error["action"], error["title"]), ("E_HUMAN_REQUIRED", "activate_license", "Licensing"))
        self.assertEqual(error["knowledge"], ["xpedition-license-sign-in"])

    def test_exports_that_do_not_fit_one_call_continue_from_the_cache(self):
        with mock.patch.object(L, "READ_BUDGET_S", 0.5):
            error = self.error(lambda: self.library())
        self.assertEqual(error["code"], "E_TIMEOUT")
        self.assertEqual(self.tools.runs, [])
        with mock.patch.object(L, "READ_BUDGET_S", 0.5), mock.patch.dict(os.environ, {jobs.WORKER_ENV: "job.json"}):
            library = self.library()  # a long job's worker has no call limit
        self.assertEqual(len(library.parts), 2)

    def test_load_library_reads_the_parts_cells_padstacks_and_symbols_into_the_cache_folder(self):
        library = L.load_library(str(self.prj))
        self.assertEqual(len(library.parts), 2)
        self.assertTrue(library.cells and library.padstacks["padstacks"] and library.symbols)
        self.assertTrue(library.find_symbol(library.find_parts("MCU-8")[0]["symbols"][0]))
        self.assertTrue(any((self.tmp / "cache").rglob("*.hkp")))
        self.assertFalse((self.tmp / "state" / "adapter" / L.CACHE_FOLDER).exists())  # the runtime's folder stays its own

    def test_the_cache_is_this_users_cache_folder(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.tmp / "local")}):
            self.assertEqual(CACHE_ROOT(), self.tmp / "local" / "xpedition-cli" / L.CACHE_FOLDER)

    def test_a_project_without_its_library_is_not_found(self):
        self.lmc.unlink()
        self.assertEqual(self.error(lambda: L.load_library(str(self.prj)))["code"], "E_NOT_FOUND")

    def test_the_library_of_the_installation_is_named_through_sdd_home(self):
        self.prj.write_bytes(PRJ.replace("Library\\Lib.lmc", "${SDD_HOME}/Library/Lib.lmc").encode("utf-8"))
        with mock.patch.dict(os.environ, {"SDD_HOME": str(self.folder)}):
            self.assertEqual(L.central_library(self.prj), self.lmc)


class PartReadTest(LibraryCase):
    def test_part_list_gives_one_row_per_part_with_its_number_as_id(self):
        result = L.PartList().read(self.ctx("part list"))
        self.assertEqual([row["id"] for row in result["items"]], ["MCU-8", "RES-10K"])
        mcu = result["items"][0]
        self.assertEqual((mcu["partition"], mcu["cell"], mcu["pins"]), ("PartQuest", "SOIC127P600X175-8N", 8))
        self.assertEqual(result["items"][1]["value"], "10k")
        self.assertEqual(self.tools.tools(), ["PartsDB2HKP"])  # the list needs the parts only

    def test_part_list_of_a_library_that_cannot_be_read_says_so(self):
        self.tools.fail["PartsDB2HKP"] = {}
        error = self.error(lambda: L.PartList().read(self.ctx("part list")))
        self.assertEqual(error["code"], "E_NATIVE_FAILURE")
        self.assertIn("parts PartQuest", error["message"])

    def test_part_get_gives_the_pin_map_symbol_cell_and_padstacks(self):
        result = L.PartGet().read(self.ctx("part get", {"id": "MCU-8"}))
        self.assertEqual(result["cell"]["name"], "SOIC127P600X175-8N")
        self.assertEqual({row["name"]: row["number"] for row in result["pins"]}["SWCLK"], "5")
        self.assertEqual(result["symbols"][0]["pins"][0]["name"], "VDD")
        self.assertNotIn("lines", result["symbols"][0])
        self.assertEqual(len(result["cell"]["pins"]), 8)
        self.assertEqual(sorted(result["cell"]["pins"][0]), ["number", "padstack", "rotation_deg", "x_mm", "y_mm"])
        pin = result["symbols"][0]["pins"][0]
        self.assertEqual(sorted(k for k in pin if k.endswith("_mm")), ["body_x_mm", "body_y_mm", "x_mm", "y_mm"])
        stack = next(iter(result["padstacks"].values()))
        self.assertTrue(stack["pad"]["width_mm"] > 0)
        self.assertEqual((result["findings"], result["also_in"]), ([], []))

    def test_part_get_reports_the_problems_of_a_part(self):
        pdb = self.root / "PartsDBLibs" / "Extra.pdb"
        text = (self.root / "PartsDBLibs" / "PartQuest.pdb").read_text(encoding="utf-8")
        pdb.write_text(text.replace('"MCU-8"', '"MCU-9"').replace("SOIC127P600X175-8N", "NO_SUCH_CELL"), encoding="utf-8")
        result = L.PartGet().read(self.ctx("part get", {"id": "MCU-9"}))
        rules = {f["rule"]: f for f in result["findings"]}
        self.assertEqual(rules["part_cell_missing"]["severity"], "high")
        self.assertEqual(rules["part_cell_missing"]["objects"], ["part MCU-9", "cell NO_SUCH_CELL"])
        self.assertTrue(rules["part_cell_missing"]["id"].startswith("part_cell_missing-"))

    def test_part_get_of_an_unknown_number(self):
        error = self.error(lambda: L.PartGet().read(self.ctx("part get", {"id": "NOPE"})))
        self.assertEqual(error["code"], "E_NOT_FOUND")
        self.assertIn("part list", error["message"])


class PartCreateTest(LibraryCase):
    def test_the_preview_names_each_new_part_and_its_content_and_writes_nothing(self):
        parts = self.parts_file(LDO, {**RES, "number": "RES-4K7"})
        before = sorted(p.name for p in self.root.rglob("*"))
        out = L.PartCreate().preview(self.ctx("part create", {"file": str(parts)}))
        keys = [t["key"] for t in out["targets"]]
        self.assertEqual(keys[:2], [base.key(str(self.prj), "/parts/LDO-3V3"), base.key(str(self.prj), "/parts/RES-4K7")])
        self.assertEqual(out["targets"][2], {"key": base.key(str(self.lmc)), "kind": "library", "label": str(self.lmc)})
        self.assertEqual([c["field"] for c in out["changes"]], ["content", "content"])
        self.assertTrue(all(c["before"] is None and len(c["after"]) == 64 for c in out["changes"]))
        self.assertTrue(out["can_apply"])
        self.assertEqual(self.tools.imports(), [])
        self.assertEqual(sorted(p.name for p in self.root.rglob("*")), before)

    def test_apply_imports_in_order_without_replacing_and_the_read_back_matches(self):
        parts = self.parts_file(LDO, {**RES, "number": "RES-4K7"})
        ctx = self.ctx("part create", {"file": str(parts)})
        planned = L.PartCreate().preview(ctx)
        out = L.PartCreate().apply(self.ctx("part create", {"file": str(parts)}, operation_id="op_1"))
        self.assertEqual((out["dispatch_state"], out["change"], out["persistence"]), ("dispatched", "applied", "saved"))
        self.assertEqual([name for name, _ in self.tools.imports()], ["HKP2PadstackDB", "HKP2CellDB", "HKP2PartsDB"])
        for name, args in self.tools.imports():
            self.assertNotIn("-r", args)
            self.assertEqual(args[args.index("-c") + 1], str(self.lmc))
        parts_args = self.tools.imports()[2][1]
        self.assertEqual(Path(parts_args[parts_args.index("-o") + 1]), self.root / "PartsDBLibs" / "PartQuest.pdb")
        result = out["result"]
        self.assertEqual((result["created"], result["skipped"]), (["LDO-3V3", "RES-4K7"], []))
        self.assertTrue(result["symbols"] and all(name.startswith("LDO") for name in result["symbols"]))
        self.assertIn("SOT95P280X145-5N", result["cells"])
        self.assertNotIn("RESC1005X35N", result["cells"])  # the resistor's cell is in the library already
        text = self.prj.read_text(encoding="utf-8")
        for entry in ('VALUE "PartsDBLibs\\PartQuest.pdb"', 'VALUE "SymbolLibs\\PartQuest"', 'VALUE "CellDBLibs\\PartQuest.cel"'):
            self.assertIn(entry, text)
        targets = [c["target"] for c in planned["changes"]] + [base.key(str(self.prj), "/parts/NOPE")]
        back = L.PartCreate().read_back(self.ctx("part create", {"file": str(parts)}, operation_id="op_1", targets=targets,
                                                 fields=["content"]))
        for planned_change in planned["changes"]:
            self.assertEqual(back["observed"][planned_change["target"]]["content"], planned_change["after"])
        self.assertIsNone(back["observed"][targets[-1]]["content"])
        self.assertEqual(back["unreadable"], [])

    def test_identical_parts_are_skipped_and_nothing_is_written(self):
        parts = self.parts_file(RES)
        out = L.PartCreate().preview(self.ctx("part create", {"file": str(parts)}))
        self.assertEqual((out["targets"], out["changes"]), ([], []))
        facts = L.PartCreate().apply(self.ctx("part create", {"file": str(parts)}, operation_id="op_1"))
        self.assertEqual((facts["change"], facts["persistence"]), ("not_applicable", "unchanged"))
        self.assertEqual((facts["result"]["created"], facts["result"]["skipped"]), ([], ["RES-10K"]))
        self.assertEqual(self.tools.imports(), [])

    def test_a_number_the_library_holds_with_other_content_is_refused(self):
        parts = self.parts_file({**RES, "description": "resistor 10k 1% 0402"})
        error = self.error(lambda: L.PartCreate().preview(self.ctx("part create", {"file": str(parts)})))
        self.assertEqual((error["code"], error["conflict"], error["dispatch_state"]), ("E_CONFLICT", "application_state", "not_started"))
        self.assertIn("RES-10K", error["message"])
        self.assertEqual(error["knowledge"], ["library-converter-rules"])
        self.assertEqual(self.error(lambda: L.PartCreate().apply(self.ctx("part create", {"file": str(parts)})))["code"],
                         "E_CONFLICT")
        self.assertEqual(self.tools.imports(), [])

    def test_a_number_another_partition_holds_is_refused(self):
        self.lmc.write_bytes(registered("PartQuest", "Other"))
        parts = self.parts_file(RES, partition="Other")
        error = self.error(lambda: L.PartCreate().preview(self.ctx("part create", {"file": str(parts)})))
        self.assertEqual(error["code"], "E_VALIDATION")
        self.assertIn("partition PartQuest already", error["message"])

    def test_a_partition_the_library_has_not_registered_needs_the_person(self):
        parts = self.parts_file(LDO, partition="Sensors")
        error = self.error(lambda: L.PartCreate().preview(self.ctx("part create", {"file": str(parts)})))
        self.assertEqual((error["code"], error["action"], error["dispatch_state"]), ("E_HUMAN_REQUIRED", "other", "not_started"))
        self.assertIn("Partition Editor", error["message"])
        self.assertIn("part partition Sensors", error["message"])
        self.assertEqual(self.tools.imports(), [])

    def test_a_registration_is_the_whole_name_in_the_library_file(self):
        self.lmc.write_bytes(lmc_bytes("PartsDBLibs\\MyPartQuest.pdb", "CellDBLibs\\PartQuest.cel", "SymbolLibs\\partquest"))
        missing = L.unregistered_partitions(self.lmc, {"parts": ["PartQuest"], "cells": ["PartQuest"], "symbols": ["PartQuest"]})
        self.assertEqual(missing, ["part partition PartQuest"])

    def test_a_parts_file_that_cannot_be_read(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{", encoding="utf-8")
        self.assertEqual(self.error(lambda: L.PartCreate().preview(self.ctx("part create", {"file": str(bad)})))["code"],
                         "E_VALIDATION")
        missing = {"file": str(self.tmp / "none.json")}
        self.assertEqual(self.error(lambda: L.PartCreate().preview(self.ctx("part create", missing)))["code"], "E_NOT_FOUND")

    def test_the_installation_library_is_never_written(self):
        parts = self.parts_file(LDO)
        with mock.patch.object(env, "find_sdd_home", return_value=self.folder):
            error = self.error(lambda: L.PartCreate().preview(self.ctx("part create", {"file": str(parts)})))
        self.assertEqual(error["code"], "E_VALIDATION")
        self.assertIn("installation", error["message"])

    def test_a_failure_before_anything_was_written_changed_nothing(self):
        self.tools.fail["HKP2PadstackDB"] = {"dialogs": [{"title": "HKP2PadstackDB", "text": "bad record", "dismissed": True}]}
        parts = self.parts_file(LDO)
        error = self.error(lambda: L.PartCreate().apply(self.ctx("part create", {"file": str(parts)}, operation_id="op_1")))
        self.assertEqual((error["code"], error["dispatch_state"]), ("E_NATIVE_FAILURE", "dispatched"))
        self.assertIn("bad record", error["message"])
        self.assertFalse(any((self.root / "SymbolLibs" / "PartQuest" / "sym").glob("LDO*")))

    def test_a_failure_after_the_first_writes_is_partial(self):
        self.tools.log_lines["HKP2CellDB"] = '错误: 无法添加单元 "SOT95P280X145-5N"。正在跳到下一个单元。\n'
        parts = self.parts_file(LDO)
        out = L.PartCreate().apply(self.ctx("part create", {"file": str(parts)}, operation_id="op_1"))
        self.assertEqual((out["change"], out["persistence"]), ("partial", "partial"))
        self.assertIn("HKP2CellDB failed", out["native_message"])
        self.assertNotIn("HKP2PartsDB", [name for name, _ in self.tools.imports()])

    def test_warnings_in_a_converter_log_are_reported(self):
        self.tools.log_lines["HKP2PartsDB"] = 'Processing Part \'X\'\n\t警告: 无效的值 "3.3V" (对于特性 "Value")。\n'
        out = L.PartCreate().apply(self.ctx("part create", {"file": str(self.parts_file(LDO))}, operation_id="op_1"))
        self.assertEqual(out["change"], "applied")
        self.assertIn("无效的值", out["native_message"])

    def test_a_licence_window_during_an_import_leaves_the_outcome_open(self):
        self.tools.fail["HKP2PartsDB"] = {"blocked_by_license": True, "dialogs": []}
        error = self.error(lambda: L.PartCreate().apply(self.ctx("part create", {"file": str(self.parts_file(LDO))})))
        self.assertEqual((error["code"], error["dispatch_state"], error["action"]), ("E_HUMAN_REQUIRED", "dispatched", "activate_license"))

    def test_a_symbol_is_written_as_the_next_version(self):
        folder = self.tmp / "sym"
        folder.mkdir()
        self.assertEqual(L.next_symbol_version(folder, "RES_1"), 1)
        for name in ("RES_1.1", "RES_1.3", "RES_10.7", "RES_1.bak"):
            (folder / name).write_text("", encoding="utf-8")
        self.assertEqual(L.next_symbol_version(folder, "RES_1"), 4)

    def test_hkp_text_goes_to_the_converters_in_the_code_page(self):
        self.assertEqual(L.hkp_bytes('.Number "R1"\n'), b'.Number "R1"\n')
        if sys.platform == "win32":
            error = self.error(lambda: L.hkp_bytes(".Desc \"\U0001F600\"\n"))
            self.assertEqual((error["code"], error["dispatch_state"]), ("E_VALIDATION", "not_started"))

    def test_a_library_on_a_path_that_is_not_ascii_is_refused(self):
        other = self.tmp / "库"
        os.rename(self.folder, other)
        self.prj, self.lmc, self.root = other / "Demo.prj", other / "Library" / "Lib.lmc", other / "Library"
        error = self.error(lambda: L.PartCreate().preview(self.ctx("part create", {"file": str(self.parts_file(LDO))})))
        self.assertEqual((error["code"], error["knowledge"]), ("E_VALIDATION", ["xpedition-ascii-paths-only"]))


class ProjectListTest(unittest.TestCase):
    def test_a_project_without_the_list_gains_it_in_the_board_design_without_repeats(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "Demo.prj"
            project.write_text('SECTION iCDB\nLIST Designs\nVALUE "Board1"\nENDLIST\nENDSECTION\n'
                               'SECTION Board1\nKEY ConfigType "PCB"\nENDSECTION\n', encoding="utf-8")
            self.assertTrue(L.ensure_prj_list(project, "Symbols", "SymbolLibs\\Demo"))
            self.assertFalse(L.ensure_prj_list(project, "Symbols", "SymbolLibs\\demo"))
            text = project.read_text(encoding="utf-8")
            self.assertEqual(text.count('VALUE "SymbolLibs\\Demo"'), 1)
            self.assertLess(text.index("SECTION Board1"), text.index("LIST Symbols"))


if __name__ == "__main__":
    unittest.main()
