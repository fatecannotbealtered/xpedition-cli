"""The `.prj` text: designs, lists, library keys and the paths it names."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from xpedition_cli.adapter.plan import project_file

PRJ = (
    "SECTION DesignInfo\r\n"
    'KEY CentralLibrary "D:\\lib\\TemplateLibrary.lmc"\r\n'
    "ENDSECTION\r\n"
    "SECTION iCDB\r\n"
    "LIST Designs\r\n"
    'VALUE "Schematic1"\r\n'
    'VALUE "Board1"\r\n'
    'VALUE "PCB"\r\n'
    "ENDLIST\r\n"
    'KEY iCDBDir ".\\database"\r\n'
    "ENDSECTION\r\n"
    "SECTION Schematic1\r\n"
    'KEY ConfigType "Board1"\r\n'
    "ENDSECTION\r\n"
    "SECTION Board1\r\n"
    "LIST Symbols\r\n"
    'VALUE "SymbolLibs\\Resistors"\r\n'
    "ENDLIST\r\n"
    "LIST PDBs\r\n"
    'VALUE "PartsDBLibs\\Resistors.pdb"\r\n'
    'VALUE "PartsDBLibs\\PartQuest.pdb"\r\n'
    "ENDLIST\r\n"
    'KEY ConfigType "PCB"\r\n'
    'KEY RootBlock "Schematic1"\r\n'
    'KEY PCBDesignPath ""\r\n'
    "ENDSECTION\r\n"
    "SECTION PCB\r\n"
    'KEY ConfigType "Board1"\r\n'
    "ENDSECTION\r\n"
)


def _owned_library_keys(tmp_path):
    target = tmp_path / "new-target"
    library = target / "Library"
    library.mkdir(parents=True)
    (library / "Lib.lmc").write_bytes(b"owned fixture library")
    (library / "Lib.dbc").write_bytes(b"owned fixture dbc")
    database = target / "database"
    database.mkdir()
    (database / "icdb.dat").write_bytes(b"unchanged private fixture database")
    text = (
        "SECTION DesignInfo\r\n"
        f'  KEY CentralLibrary "{library / "Lib.lmc"}" ! preserve comment\r\n'
        f'KEY DBCFile "{library / "Lib.dbc"}"\r\n'
        f'KEY Note "{library / "Lib.lmc"}"\r\n'
        "LIST PDBs\r\n"
        f'VALUE "{library / "PartsDBLibs/Keep.pdb"}"\r\n'
        "ENDLIST\r\nENDSECTION\r\n"
    )
    return target, text


class ProjectFileTest(unittest.TestCase):
    def tmp_dir(self) -> Path:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Path(folder.name).resolve()  # long folder names, as the tool reports them

    def test_new_native_relative_keys_preserve_other_text_and_survive_a_new_root(self):
        tmp_path = self.tmp_dir()
        target, text = _owned_library_keys(tmp_path)
        database = (target / "database/icdb.dat").read_bytes()
        relative, changed = project_file.relative_owned_library_keys(text, target)
        self.assertEqual(changed, [
            {"key": "CentralLibrary", "value": "Library/Lib.lmc"},
            {"key": "DBCFile", "value": "Library/Lib.dbc"},
        ])
        expected = text.replace(
            f'KEY CentralLibrary "{target / "Library/Lib.lmc"}"',
            'KEY CentralLibrary "Library/Lib.lmc"',
        ).replace(f'KEY DBCFile "{target / "Library/Lib.dbc"}"', 'KEY DBCFile "Library/Lib.dbc"')
        self.assertEqual(relative, expected)
        self.assertEqual(project_file.relative_owned_library_keys(relative, target), (relative, []))
        self.assertEqual((target / "database/icdb.dat").read_bytes(), database)
        (target / "New.prj").write_bytes(relative.encode())
        moved = tmp_path / "unrelated-root"
        shutil.copytree(target, moved)
        self.assertEqual(project_file.local_path(moved, changed[0]["value"]), moved / "Library/Lib.lmc")
        project_file.relative_owned_library_keys(relative, moved, require_relative=True)
        # Native saving may choose Windows separators while retaining the same
        # safe relative semantics; this readback never writes another repair.
        windows_relative = relative.replace("Library/Lib.", "Library\\Lib.")
        project_file.relative_owned_library_keys(windows_relative, moved, require_relative=True)

    def test_new_native_relative_keys_reject_unsupported_or_ambiguous_references(self):
        for bad in ["external", "parent_escape", "drive_relative", "missing", "duplicate"]:
            with self.subTest(bad=bad):
                tmp_path = self.tmp_dir()
                target, text = _owned_library_keys(tmp_path)
                original = str(target / "Library/Lib.lmc")
                if bad == "external":
                    external = tmp_path / "shared.lmc"
                    external.write_bytes(b"shared fixture must not be relocated")
                    text = text.replace(original, str(external), 1)
                elif bad == "parent_escape":
                    (tmp_path / "shared.lmc").write_bytes(b"external fixture")
                    text = text.replace(original, "../shared.lmc", 1)
                elif bad == "drive_relative":
                    text = text.replace(original, "D:Library\\Lib.lmc", 1)
                elif bad == "missing":
                    text = text.replace("  KEY CentralLibrary ", "  KEY OtherLibrary ", 1)
                else:
                    text += f'KEY CentralLibrary "{original}"\r\n'
                with self.assertRaises(ValueError):
                    project_file.relative_owned_library_keys(text, target)

    def test_new_native_relative_keys_reject_reparse_before_resolving_it(self):
        tmp_path = self.tmp_dir()
        target, text = _owned_library_keys(tmp_path)
        original = Path.lstat

        def reparse(path):
            if path == target / "Library":
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path)

        with mock.patch.object(Path, "lstat", reparse):
            with self.assertRaisesRegex(ValueError, "target_library_reparse_refused"):
                project_file.relative_owned_library_keys(text, target)

    def test_native_readback_must_retain_relative_keys_without_rewriting_again(self):
        tmp_path = self.tmp_dir()
        target, text = _owned_library_keys(tmp_path)
        with self.assertRaisesRegex(ValueError, "absolute_target_library_reference_after_reopen"):
            project_file.relative_owned_library_keys(text, target, require_relative=True)

    def test_designs_reads_the_icdb_list_and_each_section(self):
        listed = project_file.designs(PRJ)
        self.assertEqual([item.name for item in listed], ["Schematic1", "Board1", "PCB"])
        board = project_file.board_design(listed)
        self.assertIsNotNone(board)
        self.assertEqual(board.name, "Board1")
        self.assertTrue(board.is_board())
        self.assertEqual(board.pcb_path, "")
        self.assertEqual(board.layout_template, "")
        self.assertEqual(project_file.board_design(listed, "PCB").config_type, "Board1")
        self.assertIsNone(project_file.board_design(listed, "Nope"))
        self.assertEqual(project_file.designs("KEY DesignName Board1\n"), [])

    def test_move_design_first_reorders_only_the_designs_list(self):
        moved = project_file.move_design_first(PRJ, "Board1")
        self.assertEqual(project_file.list_entries(moved, "Designs"), ["Board1", "Schematic1", "PCB"])
        self.assertEqual(project_file.list_entries(moved, "PDBs"), project_file.list_entries(PRJ, "PDBs"))
        self.assertIn("\r\n", moved)
        self.assertEqual(moved.count("ENDLIST"), PRJ.count("ENDLIST"))
        self.assertEqual(project_file.move_design_first(moved, "Board1"), moved)
        self.assertEqual(project_file.move_design_first(PRJ, "Nope"), PRJ)

    def test_ensure_list_and_add_entry_keep_the_file_style(self):
        text = project_file.ensure_list(PRJ, project_file.CELL_LIST, "PDBs")
        self.assertEqual(project_file.list_entries(text, project_file.CELL_LIST), [])
        self.assertGreater(text.index("LIST 2dCellLibraries"), text.index("LIST PDBs"))
        self.assertLess(text.index("LIST 2dCellLibraries"), text.index('KEY ConfigType "PCB"'))
        self.assertEqual(project_file.ensure_list(text, project_file.CELL_LIST, "PDBs"), text)
        text, added = project_file.add_entry(text, project_file.CELL_LIST, "CellDBLibs\\PartQuest.cel")
        self.assertTrue(added)
        self.assertEqual(project_file.list_entries(text, project_file.CELL_LIST), [
            "CellDBLibs\\PartQuest.cel"
        ])
        text, again = project_file.add_entry(text, project_file.CELL_LIST, "celldblibs\\partquest.cel")
        self.assertFalse(again)
        self.assertNotIn("\n\n", text.replace("\r\n", "\n"))
        with self.assertRaises(ValueError):
            project_file.ensure_list(PRJ, "X", "Nope")
        with self.assertRaises(ValueError):
            project_file.add_entry(PRJ, "Nope", "x")

    def test_cell_entries_follow_the_parts_partitions_the_library_has(self):
        pdbs = ["PartsDBLibs\\Resistors.pdb", "PartsDBLibs\\PartQuest.pdb", "PartsDBLibs\\Gone.pdb"]
        self.assertEqual(project_file.cell_entries_for(pdbs, ["Resistors", "partquest", "Capacitors"]), [
            "CellDBLibs\\Resistors.cel",
            "CellDBLibs\\PartQuest.cel",
        ])
        self.assertEqual(project_file.cell_entries_for([], ["Resistors"]), [])

    def test_the_paths_a_prj_names_are_windows_paths_on_every_system(self):
        tmp_path = self.tmp_dir()
        local = project_file.local_path
        self.assertEqual(local(tmp_path, "PCB\\Board.pcb"), (tmp_path / "PCB" / "Board.pcb").resolve())
        self.assertEqual(local(tmp_path, "..\\Lib\\Central.lmc"), (tmp_path.parent / "Lib" / "Central.lmc").resolve())
        self.assertEqual(local(tmp_path, "D:\\Lib\\Central.lmc"), Path("D:\\Lib\\Central.lmc"))
        # a drive path names a local file only where paths have drives
        self.assertIs(project_file.foreign("D:\\Lib\\Central.lmc"), os.name != "nt")
        self.assertIs(project_file.foreign("RcLib\\Central.lmc"), False)

    def test_the_central_library_is_read_from_the_prj_on_this_machine(self):
        tmp_path = self.tmp_dir()
        project = tmp_path / "Demo.prj"
        project.write_text('KEY CentralLibrary "Library\\Central.lmc"\n', encoding="utf-8")
        self.assertEqual(project_file.central_library(project), (tmp_path / "Library" / "Central.lmc").resolve())
        project.write_text("SECTION DesignInfo\nENDSECTION\n", encoding="utf-8")
        self.assertIsNone(project_file.central_library(project))
        self.assertIsNone(project_file.central_library(tmp_path / "missing.prj"))

    def test_template_state_tells_a_stock_template_from_an_initialized_project(self):
        tmp_path = self.tmp_dir()
        project = tmp_path / "Demo.prj"
        project.write_text("SECTION iCDB\nLIST Designs\nENDLIST\nENDSECTION\n", encoding="utf-8")
        state = project_file.template_state(project)
        self.assertEqual(state["database"], str((tmp_path / "database").resolve()))
        self.assertEqual((state["initialized"], state["designs"]), (False, []))
        self.assertEqual(state["creation_method"], "native_new_project")
        (tmp_path / "database").mkdir()
        (tmp_path / "database" / "icdb.dat").write_bytes(b"")
        project.write_text(PRJ, encoding="utf-8")
        state = project_file.template_state(project)
        self.assertIs(state["initialized"], True)
        self.assertEqual(state["designs"], ["Schematic1", "Board1", "PCB"])
        self.assertEqual(state["creation_method"], "template_clone")

    def test_parts_databases_beside_the_central_library_are_found(self):
        tmp_path = self.tmp_dir()
        library = tmp_path / "Library"
        (library / "PartsDBLibs").mkdir(parents=True)
        (library / "PartsDBLibs" / "PartQuest.pdb").write_bytes(b"")
        (library / "Central.lmc").write_bytes(b"... PartsDBLibs\\Other.pdb ...")
        project = tmp_path / "Demo.prj"
        project.write_text(
            'KEY CentralLibrary "Library\\Central.lmc"\n'
            'LIST PDBs\nVALUE "PartsDBLibs\\PartQuest.pdb"\nVALUE "PartsDBLibs\\Gone.pdb"\nENDLIST\n',
            encoding="utf-8",
        )
        found = str((library / "PartsDBLibs" / "PartQuest.pdb").resolve())
        self.assertEqual(project_file.pdb_path_repairs(project), [{"from": "PartsDBLibs\\PartQuest.pdb", "to": found}])
        # the library file does not register PartQuest's parts database yet
        self.assertEqual(project_file.pdb_registration_repairs(project), [{"partition": "PartQuest", "parts_database": found}])
        (library / "Central.lmc").write_bytes(b"... PartsDBLibs\\PartQuest.pdb ...")
        self.assertEqual(project_file.pdb_registration_repairs(project), [])

    def test_a_clone_relocates_only_the_library_references_it_owns(self):
        tmp_path = self.tmp_dir()
        source, target = tmp_path / "Template", tmp_path / "Demo"
        shared = tmp_path / "Shared" / "Central.lmc"
        owned = source / "Library" / "Central.lmc"
        text = (
            f'KEY CentralLibrary "{owned}"\r\n'
            f'KEY DBCFile "{shared}"\r\n'
            "LIST Symbols\r\n"
            f'VALUE "{source / "SymbolLibs" / "PartQuest"}"\r\n'
            'VALUE "SymbolLibs\\Relative"\r\n'
            "ENDLIST\r\n"
            f'KEY Note "{owned}"\r\n'
        )
        relocated, rewritten = project_file.relocate_library_references(text, source, target)
        self.assertEqual(
            rewritten,
            [
                {"key": "CentralLibrary", "value": str(target.resolve() / "Library" / "Central.lmc")},
                {"key": "LIST Symbols", "value": str(target.resolve() / "SymbolLibs" / "PartQuest")},
            ],
        )
        self.assertIn(f'KEY DBCFile "{shared}"', relocated)
        self.assertIn('VALUE "SymbolLibs\\Relative"', relocated)
        self.assertIn(f'KEY Note "{owned}"', relocated)
        self.assertEqual(relocated.count("\r\n"), text.count("\r\n"))

    def test_newline_follows_the_file(self):
        self.assertEqual(project_file.newline(PRJ), "\r\n")
        self.assertEqual(project_file.newline('KEY A "b"\n'), "\n")
