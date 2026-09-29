"""The adapter's side of the library: exports with a cache, imports with symbols."""

from __future__ import annotations

from pathlib import Path

import pytest

from xpedition_cli import library_hkp as H
from xpedition_cli import native_com_adapter as adapter


@pytest.fixture
def library(tmp_path, monkeypatch) -> dict:
    project = tmp_path / "Demo.prj"
    project.write_text("", encoding="utf-8")
    root = tmp_path / "Library"
    for folder in ("PartsDBLibs", "CellDBLibs", "Layout", "SymbolLibs"):
        (root / folder).mkdir(parents=True)
    lmc = root / "Library.lmc"
    lmc.write_text("", encoding="utf-8")
    for name in ("PartQuest.pdb", "Other.pdb"):
        (root / "PartsDBLibs" / name).write_bytes(b"db")
    (root / "CellDBLibs" / "PartQuest.cel").write_bytes(b"db")
    (root / "Layout" / "PadstackDB.psk").write_bytes(b"db")
    runs: list[tuple[str, list[str]]] = []

    def run(tool, arguments, timeout=adapter.LIBRARY_TOOL_TIMEOUT):
        runs.append((tool, list(arguments)))
        output = Path(arguments[arguments.index("-o") + 1])
        if "-a" in arguments:  # an export: the converter writes its text
            output.write_text(f"! {tool}\n", encoding="utf-8")
        return {"tool": tool, "exit_code": 0, "dialogs": [], "stdout": ""}

    monkeypatch.setattr(adapter, "_central_library", lambda path: (lmc, root))
    monkeypatch.setattr(adapter, "_run_library_tool", run)
    return {"project": project, "root": root, "runs": runs, "cache": tmp_path / "cache"}


def _export(library: dict, **params) -> dict:
    return adapter._library_export(
        {"project": str(library["project"]), "cache": str(library["cache"]), **params}
    )


def test_an_export_runs_each_converter_once_until_a_database_changes(library) -> None:
    first = _export(library)
    tools = sorted(tool for tool, _ in library["runs"])
    assert tools == ["CellDB2HKP", "PadstackDB2HKP", "PartsDB2HKP", "PartsDB2HKP"]
    assert first["exported"] == 4 and first["failed"] == []
    assert {(f["kind"], f["partition"]) for f in first["files"]} == {
        ("parts", "PartQuest"),
        ("parts", "Other"),
        ("cells", "PartQuest"),
        ("padstacks", ""),
    }
    for _tool, arguments in library["runs"]:
        assert arguments[arguments.index("-u") + 1] == "mm" and "-a" in arguments
    library["runs"].clear()
    second = _export(library)
    assert library["runs"] == [] and second["exported"] == 0
    assert all(f["cached"] for f in second["files"])
    changed = library["root"] / "PartsDBLibs" / "Other.pdb"
    changed.write_bytes(b"a bigger database")
    _export(library)
    assert [tool for tool, _ in library["runs"]] == ["PartsDB2HKP"]


def test_an_export_can_be_limited_to_partitions_and_kinds(library) -> None:
    result = _export(library, kinds=["parts"], partitions=["other"])
    assert [(f["kind"], f["partition"]) for f in result["files"]] == [("parts", "Other")]
    assert result["symbols"].endswith("SymbolLibs")


def test_a_converter_that_fails_is_reported_and_not_cached(library, monkeypatch) -> None:
    def fail(tool, arguments, timeout=0):
        return {"tool": tool, "exit_code": 1, "dialogs": [{"title": tool, "text": "no"}]}

    monkeypatch.setattr(adapter, "_run_library_tool", fail)
    result = _export(library, kinds=["parts"])
    assert result["files"] == [] and len(result["failed"]) == 2
    assert result["failed"][0]["dialogs"][0]["text"] == "no"


def test_an_import_writes_symbols_and_replaces_parts_only_when_asked(library, monkeypatch):
    monkeypatch.setattr(adapter, "_viewdraw_application", _no_designer)
    monkeypatch.setattr(adapter, "_tool_log", lambda path: {"errors": [], "warnings": []})
    monkeypatch.setattr(adapter, "_sync_prj_cells", lambda path, root: [])
    registered: list[tuple[str, str]] = []
    monkeypatch.setattr(adapter, "_ensure_prj_pdb", lambda path, entry: True)
    monkeypatch.setattr(
        adapter,
        "_ensure_prj_list",
        lambda path, name, entry: registered.append((name, entry)) or True,
    )
    params = {
        "project": str(library["project"]),
        "partition": "PartQuest",
        "parts": "parts",
        "symbols": {"LDO_12345678": "V 53\nE\n"},
        "replace": False,
    }
    result = adapter._library_import(params, None)
    written = library["root"] / "SymbolLibs" / "PartQuest" / "sym" / "LDO_12345678.1"
    assert result["ok"] is True and written.read_text(encoding="utf-8") == "V 53\nE\n"
    assert result["symbols_written"] == ["LDO_12345678"]
    assert ("Symbols", "SymbolLibs\\PartQuest") in registered
    parts_run = next(args for tool, args in library["runs"] if tool == "HKP2PartsDB")
    assert "-r" not in parts_run
    adapter._library_import({**params, "replace": True, "symbols": {}}, None)
    parts_run = [args for tool, args in library["runs"] if tool == "HKP2PartsDB"][-1]
    assert "-r" in parts_run
    with pytest.raises(adapter.AdapterError, match="plain file name"):
        adapter._library_import({**params, "symbols": {"../escape": "x"}}, None)


def _no_designer(client, attach_only=False):
    raise adapter.AdapterError("E_BACKEND_UNAVAILABLE", "Designer is not running")


def test_warnings_in_a_converter_log_are_reported(tmp_path) -> None:
    # UTF-8 and ASCII bytes read the same on every runner; `.encode("mbcs")` would not
    # (it raises off Windows, and an English code page cannot encode the Chinese)
    log = tmp_path / "parts.log"
    log.write_bytes(
        'Processing Part \'X\'\n\t警告: 无效的值 "3.3V" (对于特性 "Value")。\n'.encode()
    )
    summary = adapter._tool_log(log)
    assert summary["errors"] == [] and len(summary["warnings"]) == 1
    log.write_bytes(b"Processing Part 'X'\r\n\tWarning: invalid value \"3.3V\" (Value).\r\n")
    assert len(adapter._tool_log(log)["warnings"]) == 1


def test_tool_output_is_read_as_utf8_first_and_never_fails() -> None:
    assert adapter._tool_text("警告: 无效的值".encode()) == "警告: 无效的值"
    assert adapter._tool_text(b"\xef\xbb\xbfDone.\r\n") == "Done.\r\n"
    # a Chinese code page's bytes: whatever this machine's code page makes of them,
    # the reader returns text and keeps the ASCII around them
    text = adapter._tool_text("警告".encode("gbk") + b" ERROR: stop\r\n")
    assert "ERROR: stop" in text


@pytest.mark.parametrize(
    "text, value",
    [
        ("10k 1%", "10k"),
        ("100nF/16V", "100n"),
        ("4.7k", "4.7k"),
        ("100", "100"),
        ("2.2uH", "2.2u"),
        ("LED-GREEN", None),
        ("3.3V", None),
    ],
)
def test_the_library_value_is_the_number_a_component_value_begins_with(text, value) -> None:
    assert H.library_value(text) == value


def test_placeholder_parts_carry_a_value_the_library_can_keep() -> None:
    import json

    example = Path(__file__).resolve().parents[1] / "examples" / "demo-sensor-board.json"
    plan = H.plan_library(json.loads(example.read_text(encoding="utf-8")))
    # the parts database keeps 10k and stores "10k 1%" as 0: the number goes in, or nothing
    assert plan.parts["10k 1%"].properties == {"Value": "10k"}
    assert plan.parts["100nF/16V"].properties == {"Value": "100n"}
    assert plan.parts["LED-GREEN"].properties == {}


def test_the_import_copies_another_librarys_symbol_file_byte_for_byte(
    tmp_path, monkeypatch
) -> None:
    library = tmp_path / "Lib"
    library.mkdir()
    (library / "Lib.lmc").write_text("", encoding="utf-8")
    project = tmp_path / "P.prj"
    project.write_text(
        f'KEY CentralLibrary "{library / "Lib.lmc"}"\nLIST Symbols\nENDLIST\nLIST PDBs\nENDLIST\n',
        encoding="utf-8",
    )

    def no_designer(client, attach_only=True):
        raise adapter.AdapterError("E_BACKEND_UNAVAILABLE", "Designer is not running")

    monkeypatch.setattr(adapter, "_viewdraw_application", no_designer)
    source = tmp_path / "RES_1.3"
    raw = b"V 54\n" + "贴片".encode("gbk")  # a code page's bytes, not UTF-8
    source.write_bytes(raw)
    unit = {
        "partition": "Company Parts",
        "symbols": {"RES_1": "x"},
        "symbol_files": {"RES_1": str(source)},
    }
    first = adapter._library_import({"project": str(project), "units": [unit]}, None)
    folder = library / "SymbolLibs" / "Company Parts" / "sym"
    assert (folder / "RES_1.1").read_bytes() == raw and first["units"][0]["symbols_registered"]
    # written again, it is the next version: the file Designer takes
    adapter._library_import({"project": str(project), "units": [unit]}, None)
    assert (folder / "RES_1.2").read_bytes() == raw
    assert 'VALUE "SymbolLibs\Company Parts"' in project.read_text(encoding="utf-8")


def test_an_ascii_hkp_text_is_written_as_it_is() -> None:
    assert adapter._hkp_bytes('.Number "R1"\n') == b'.Number "R1"\n'
