"""Stock templates must initialize iCDB; an acknowledgement cannot replace readback."""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as bridge
from xpedition_cli import project_file
from xpedition_cli.driver import _project_creation_readback


def _stock(tmp_path):
    folder = tmp_path / "stock"
    folder.mkdir()
    library = folder / "Library"
    library.mkdir()
    (library / "Lib.lmc").write_bytes(b"fixture library")
    (library / "Lib.dbc").write_bytes(b"fixture dbc")
    path = folder / "Default.prj"
    path.write_text(
        f'SECTION DesignInfo\nKEY CentralLibrary "{library / "Lib.lmc"}"\n'
        f'KEY DBCFile "{library / "Lib.dbc"}"\nENDSECTION\n'
        'SECTION iCDB\nKEY iCDBDir "./database"\nENDSECTION\n',
        encoding="utf-8",
    )
    return path


class DeclaredRaw:
    """Pure 151 dispatch-shaped fixture; never obtains a native pointer."""

    iid = None
    members = {}

    def QueryInterface(self, iid, wrapped=None):
        if iid == bridge._IUNKNOWN_IID:
            return self.identity
        assert (iid, wrapped) == (self.iid, bridge._IDISPATCH_IID)
        return self

    def GetIDsOfNames(self, lcid, name):
        assert lcid == 0
        return self.members[name][0]

    def InvokeTypes(self, dispid, lcid, kind, result, arguments, *values):
        selected = [(name, row) for name, row in self.members.items() if row[0] == dispid]
        assert len(selected) == 1
        name, (_member, output, inputs) = selected[0]
        assert (lcid, kind, result, arguments) == (0, 1, (output, 0), inputs)
        assert len(values) == len(inputs)
        return getattr(self, name)(*values)


class Strings(DeclaredRaw):
    iid = bridge._STRING_LIST_IID
    members = {"GetCount": (1, 3, ()), "GetItem": (2, 8, ((3, 1),))}

    def __init__(self, values):
        self.values = values
        self.identity = object()

    def GetCount(self):
        return len(self.values)

    def GetItem(self, index):
        return self.values[index - 1]


class ProjectData(SimpleNamespace, DeclaredRaw):
    iid = bridge._PROJECT_DATA_IID
    members = {
        "GetProjectFilePath": (3, 8, ()),
        "GetiCDBDesigns": (9, 9, ()),
        "AddiCDBDesign": (36, 11, ((8, 1),)),
    }

    def __init__(self, **values):
        super().__init__(**values)
        self.identity = object()


class ProjectApp(DeclaredRaw):
    members = {"GetProjectData": (228, 9, ())}

    def __init__(self, *, initialize=True):
        self.identity = object()
        self.initialize = initialize
        self.path = None
        self.designs = []
        self.calls = []
        self.ActiveView = object()
        self.ActiveDocument = SimpleNamespace(Save=lambda: self.calls.append("save"))
        self.data = ProjectData(
            GetProjectFilePath=lambda: str(self.path),
            GetiCDBDesigns=lambda: Strings(self.designs),
            AddiCDBDesign=self.add_design,
        )
        self.GetProjectData = lambda: self.data

    def IsProjectOpened(self):
        return self.path is not None

    def NewProject(self, target, library, server, template):
        self.calls.append("new_project")
        self.path = Path(target)
        assert not self.path.parent.exists(), "native initialization must precede folder copying"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(Path(template).read_text(encoding="utf-8"), encoding="utf-8")
        if self.initialize:
            database = self.path.parent / "database"
            database.mkdir()
            (database / "icdb.dat").write_bytes(b"native fixture iCDB")
        return True

    def add_design(self, name):
        self.designs.append(name)
        text = self.path.read_text(encoding="utf-8")
        text = text.replace(
            'KEY iCDBDir "./database"',
            'KEY iCDBDir "./database"\nLIST Designs\nVALUE "' + name + '"\nENDLIST',
        )
        text += "\nSECTION " + name + '\nKEY ConfigType "PCB"\nENDSECTION\n'
        self.path.write_text(text, encoding="utf-8")
        return True


class EmptyViewProjectApp(ProjectApp):
    """Exercise the actual ensure/save/close/reopen orchestration with no initial view."""

    def __init__(self):
        super().__init__()
        self.ActiveView = None
        self.ActiveDocument = None
        self.Documents = SimpleNamespace(Open=self.open_design, SaveAll=self.save_all)

    def GetActiveDesign(self):
        self.calls.append("get_active_design")
        return self.designs[0] if self.designs else ""

    def add_design(self, name):
        self.calls.append("add_design")
        return super().add_design(name)

    def open_design(self, name):
        assert name in self.designs, "No document open before initial design creation"
        self.calls.append("open_design")
        self.ActiveView = object()
        self.ActiveDocument = SimpleNamespace(Save=lambda: self.calls.append("save"))

    def save_all(self):
        self.calls.append("save_all")

    def CloseProject(self):
        self.calls.append("close_project")
        self.path = None
        self.designs = []
        self.ActiveView = None
        self.ActiveDocument = None
        return True

    def OpenProject(self, path):
        self.calls.append("open_project")
        self.path = Path(path)
        self.designs = project_file.template_state(self.path)["designs"]
        return True


class CreatedButClosedProjectApp(EmptyViewProjectApp):
    """Observed NewProject behavior: disk initialized, project not yet open."""

    def __init__(self):
        super().__init__()
        self.data.GetProjectFilePath = lambda: str(self.path) if self.path else ""

    def NewProject(self, *args):
        accepted = super().NewProject(*args)
        self.path = None
        return accepted


def test_new_project_without_open_context_reaches_open_before_add(tmp_path, monkeypatch):
    from contextlib import nullcontext

    app = CreatedButClosedProjectApp()
    template = _stock(tmp_path)
    target = tmp_path / "not-open" / "New.prj"
    monkeypatch.setattr(bridge, "_viewdraw_application", lambda *a, **k: app)
    monkeypatch.setattr(bridge, "_settle", lambda *a: None)
    monkeypatch.setattr(bridge, "_PromptAnswerer", lambda *a, **k: nullcontext())
    result = bridge._clone_project({"project": str(target), "template": str(template)}, object())
    assert result["persistence_verified"] is True
    assert app.calls.index("open_project") < app.calls.index("add_design")
    assert app.calls.count("open_project") == 2
    assert app.designs == ["Board1"]


@pytest.mark.parametrize("cad_restores_absolute", [False, True])
def test_new_native_project_uses_relative_library_keys_and_checks_reopen(
    tmp_path,
    monkeypatch,
    cad_restores_absolute,
):
    from contextlib import nullcontext

    from xpedition_cli.backends import NativeBackend

    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        raise AssertionError("relative-key tests cannot use a real native/COM boundary")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    monkeypatch.setattr(NativeBackend, "invoke", forbidden)
    monkeypatch.setattr(NativeBackend, "status", forbidden)
    monkeypatch.setattr(bridge, "_import_com", forbidden)
    template = _stock(tmp_path)
    original_source = template.read_bytes()
    target = tmp_path / "new-relative" / "New.prj"
    app = EmptyViewProjectApp()
    original_open = app.OpenProject

    def open_with_prj_check(path):
        text = Path(path).read_text(encoding="utf-8")
        assert 'KEY CentralLibrary "Library/Lib.lmc"' in text
        assert 'KEY DBCFile "Library/Lib.dbc"' in text
        result = original_open(path)
        if cad_restores_absolute:
            Path(path).write_text(
                text.replace("Library/Lib.lmc", str(target.parent / "Library/Lib.lmc")),
                encoding="utf-8",
            )
        return result

    app.OpenProject = open_with_prj_check
    monkeypatch.setattr(bridge, "_viewdraw_application", lambda *a, **k: app)
    monkeypatch.setattr(bridge, "_settle", lambda *a: None)
    monkeypatch.setattr(bridge, "_PromptAnswerer", lambda *a, **k: nullcontext())
    if cad_restores_absolute:
        with pytest.raises(bridge.AdapterError) as failure:
            bridge._clone_project({"project": str(target), "template": str(template)}, object())
        assert failure.value.code == "E_VERIFY_INCOMPLETE"
        assert failure.value.details["native_reopen_returned"] is True
        assert str(target.parent / "Library/Lib.lmc") in target.read_text(encoding="utf-8")
    else:
        result = bridge._clone_project(
            {"project": str(target), "template": str(template)}, object()
        )
        assert result["relative_library_keys"] == [
            {"key": "CentralLibrary", "value": "Library/Lib.lmc"},
            {"key": "DBCFile", "value": "Library/Lib.dbc"},
        ]
        assert result["persistence_verified"] is True
    assert app.calls.count("open_project") == 1
    assert template.read_bytes() == original_source
    assert (target.parent / "database/icdb.dat").read_bytes() == b"native fixture iCDB"
    assert hits == []


def test_empty_design_without_view_uses_real_ensure_and_reopen(tmp_path, monkeypatch):
    from contextlib import nullcontext

    template = _stock(tmp_path)
    target = tmp_path / "new-empty" / "New.prj"
    app = EmptyViewProjectApp()
    trace = tmp_path / "boundaries.jsonl"
    monkeypatch.setenv("XPEDITION_NATIVE_TRACE_FILE", str(trace))
    monkeypatch.setattr(bridge, "_viewdraw_application", lambda *a, **k: app)
    monkeypatch.setattr(bridge, "_settle", lambda *a: None)
    monkeypatch.setattr(bridge, "_PromptAnswerer", lambda *a, **k: nullcontext())
    result = bridge._clone_project({"project": str(target), "template": str(template)}, object())
    assert result["persistence_verified"] is True
    assert app.designs == ["Board1"]
    assert app.calls.index("add_design") < app.calls.index("open_design")
    assert app.calls.index("save_all") < app.calls.index("close_project")
    assert app.calls.count("open_project") == 1
    assert app.calls[-1] == "open_design"
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    ends = [row["step"] for row in rows if row["event"] == "end"]
    assert ends.index("create.NewProject") < ends.index("create.AddiCDBDesign")
    assert ends.index("close.Documents.SaveAll") < ends.index("close.CloseProject")
    assert "create.final_designs" in ends
    assert all(row["elapsed_ms"] >= 0 for row in rows)


def test_boundary_begin_is_flushed_before_call_and_secrets_are_not_traced(
    tmp_path, monkeypatch, capsys
):
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv("XPEDITION_NATIVE_TRACE_FILE", str(trace))

    def blocked():
        assert json.loads(trace.read_text(encoding="utf-8"))["event"] == "begin"
        raise RuntimeError("private-token-and-path")

    with pytest.raises(RuntimeError, match="private-token"):
        bridge._native_call("fixture.call", blocked)
    text = trace.read_text(encoding="utf-8")
    assert "private-token-and-path" not in text
    assert [json.loads(line)["event"] for line in text.splitlines()] == ["begin", "error"]
    assert capsys.readouterr().out == ""


def test_nested_boundary_ids_are_distinct_even_with_coarse_clock(tmp_path, monkeypatch):
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv("XPEDITION_NATIVE_TRACE_FILE", str(trace))
    monkeypatch.setattr(bridge.time, "monotonic", lambda: 1.0)
    bridge._native_call("outer", lambda: bridge._native_call("inner", lambda: True))
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert len({r["id"] for r in rows if r["event"] == "begin"}) == 2


@pytest.mark.parametrize("failure", ["write", "fsync"])
@pytest.mark.parametrize("failed_event", [1, 2])
def test_trace_end_failure_preserves_returned_call_without_retry(
    tmp_path, monkeypatch, capsys, failure, failed_event
):
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv("XPEDITION_NATIVE_TRACE_FILE", str(trace))
    calls = []
    real_open = Path.open
    real_fsync = bridge.os.fsync
    attempts = 0

    def fail_second_open(path, *args, **kwargs):
        nonlocal attempts
        if path == trace and args and args[0] == "a":
            attempts += 1
            if attempts == failed_event:
                raise PermissionError("private trace path must not be printed")
        return real_open(path, *args, **kwargs)

    def fail_second_fsync(fd):
        nonlocal attempts
        attempts += 1
        if attempts == failed_event:
            raise OSError("private fsync failure")
        return real_fsync(fd)

    if failure == "write":
        monkeypatch.setattr(Path, "open", fail_second_open)
    else:
        monkeypatch.setattr(bridge.os, "fsync", fail_second_fsync)
    result = bridge._native_call("fixture.returned", lambda: calls.append("once") or True)
    assert result is True and calls == ["once"]
    output = capsys.readouterr()
    assert output.out == ""
    assert "trace" in output.err.lower() and "private" not in output.err


def test_stock_new_project_initializes_database_and_reopens(tmp_path, monkeypatch):
    template = _stock(tmp_path)
    target = tmp_path / "new" / "New.prj"
    app = ProjectApp()
    monkeypatch.setattr(bridge, "_viewdraw_application", lambda *a, **kw: app)
    monkeypatch.setattr(bridge, "_ensure_project", lambda *a: None)
    monkeypatch.setattr(bridge, "_reopen_project", lambda *a: app.calls.append("reopen"))
    result = bridge._clone_project({"project": str(target), "template": str(template)}, object())
    assert app.calls == ["new_project", "save", "reopen"]
    assert result["creation_method"] == "native_new_project"
    assert result["persistence_verified"] is True
    assert project_file.template_state(target)["designs"] == ["Board1"]
    assert project_file.central_library(target).parent == target.parent / "Library"


def test_initialized_clone_relocates_owned_library_lists_before_open(tmp_path, monkeypatch):
    template = _stock(tmp_path)
    root = template.parent
    database = root / "database"
    database.mkdir()
    (database / "icdb.dat").write_bytes(b"initialized fixture")
    text = template.read_text(encoding="utf-8").replace(
        'KEY iCDBDir "./database"',
        'KEY iCDBDir "./database"\nLIST Designs\nVALUE "Board1"\nENDLIST',
    )
    targets = {
        "Symbols": "Library/SymbolLibs/QA",
        "PDBs": "Library/PartsDBLibs/QA.pdb",
        "2dCellLibraries": "Library/CellDBLibs/QA.cel",
    }
    for relative in targets.values():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix:
            path.write_bytes(b"owned fixture")
        else:
            path.mkdir()
    external = tmp_path / "shared/External.pdb"
    external.parent.mkdir()
    external.write_bytes(b"shared fixture")
    text += '\nSECTION Board1\nKEY ConfigType "PCB"\n'
    for name, relative in targets.items():
        text += f'LIST {name}\nVALUE "{root / relative}"\nENDLIST\n'
    text += f'LIST PDBs\nVALUE "{external}"\nENDLIST\n'
    text += 'LIST Symbols\nVALUE "SymbolLibs/Relative"\nENDLIST\n'
    text += f'KEY Note "{root / targets["PDBs"]}"\nENDSECTION\n'
    template.write_text(text, encoding="utf-8")
    target = tmp_path / "copy/New.prj"
    app = EmptyViewProjectApp()
    monkeypatch.setattr(bridge, "_viewdraw_application", lambda *a, **kw: app)
    monkeypatch.setattr(bridge, "_settle", lambda *a: None)
    result = bridge._clone_project({"project": str(target), "template": str(template)}, object())
    actual = target.read_text(encoding="utf-8")
    assert f'KEY CentralLibrary "{target.parent / "Library/Lib.lmc"}"' in actual
    assert "relative_library_keys" not in result
    for name, relative in targets.items():
        assert f'LIST {name}\nVALUE "{target.parent / relative}"' in actual
    assert str(external) in actual and 'VALUE "SymbolLibs/Relative"' in actual
    assert f'KEY Note "{root / targets["PDBs"]}"' in actual
    assert template.read_text(encoding="utf-8") == text
    assert result["persistence_verified"] and app.calls.count("open_project") == 2


def test_ghost_designs_fail_before_copy_or_com(tmp_path, monkeypatch):
    template = _stock(tmp_path)
    text = template.read_text(encoding="utf-8").replace(
        'KEY iCDBDir "./database"', 'KEY iCDBDir "./database"\nLIST Designs\nVALUE "Ghost"\nENDLIST'
    )
    template.write_text(
        text + '\nSECTION Ghost\nKEY ConfigType "PCB"\nENDSECTION\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        bridge, "_viewdraw_application", lambda *a, **kw: pytest.fail("must fail before COM")
    )
    target = tmp_path / "new" / "New.prj"
    with pytest.raises(bridge.AdapterError) as error:
        bridge._clone_project({"project": str(target), "template": str(template)}, object())
    assert error.value.code == "E_PROJECT_INVALID"
    assert error.value.details["write_attempted"] is False
    assert not target.parent.exists()


def test_new_project_true_without_database_remains_partial(tmp_path, monkeypatch):
    template = _stock(tmp_path)
    app = ProjectApp(initialize=False)
    monkeypatch.setattr(bridge, "_viewdraw_application", lambda *a, **kw: app)
    target = tmp_path / "new" / "New.prj"
    with pytest.raises(bridge.AdapterError) as error:
        bridge._clone_project({"project": str(target), "template": str(template)}, object())
    assert error.value.details["partial"] is True
    assert app.calls == ["new_project"]
    assert target.exists()


@pytest.mark.parametrize(
    "wrong", ["input_echo", "closed", "other_path", "other_design", "no_database"]
)
def test_driver_project_verification_rejects_missing_or_false_native_readback(tmp_path, wrong):
    path = _stock(tmp_path)
    app = ProjectApp()
    app.path = path
    app.add_design("Board1")
    (path.parent / "database").mkdir()
    (path.parent / "database/icdb.dat").write_bytes(b"fixture")
    result = {
        "persistence_verified": True,
        "verification": {"source": "native_getters_after_save_close_open"},
    }
    document = {"open": True, "path": str(path), "designs": ["Board1"]}
    if wrong == "input_echo":
        result["verification"]["source"] = "input_echo"
    elif wrong == "closed":
        document["open"] = False
    elif wrong == "other_path":
        document["path"] = str(tmp_path / "other.prj")
    elif wrong == "other_design":
        document["designs"] = ["Other"]
    else:
        (path.parent / "database/icdb.dat").unlink()
    after = {"extensions": {"facts": {"application": {"document": document}}}}
    plan = {"extensions": {"legacy_preview": {"template_state": {"initialized": False}}}}
    assert _project_creation_readback(result, {"project": str(path)}, plan, after)["valid"] is False
