"""A failed draw can be resumed instead of redrawn from the first sheet.

A 465-operation, 5-sheet draw failed five times before it succeeded, and each
failure meant redrawing every sheet -- including the ones that had drawn and
saved correctly. The plan saves at the end of every sheet, so a failure now
says which sheets are on disk, and `--sheets` draws only the rest. `--pace`
slows a draw for someone watching Designer, as it already does for `pcb trace`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import main as cli
from xpedition_cli import native_com_adapter as adapter
from xpedition_cli import schematic_layout
from xpedition_cli.backends import native_xpedition
from xpedition_cli.errors import CLIError

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-sensor-board.json"


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))


def demo_ops() -> list[dict]:
    design = json.loads(DEMO.read_text(encoding="utf-8"))
    return schematic_layout.plan_to_params(design, "C:/p/X.prj")["ops"]


# ---- choosing sheets (CLI side) ----


def test_a_chosen_sheet_keeps_its_whole_run_and_nothing_else() -> None:
    ops = demo_ops()
    kept = cli._ops_for_sheets(ops, [2, 3])
    sheets = {entry["sheet"] for entry in cli._annotate_operations(kept)}
    assert sheets == {2, 3}
    assert kept[0] == {"op": "open_sheet", "number": 2}
    # each sheet's run is intact: open, wipe, ..., save
    everything = cli._annotate_operations(ops)
    expected = [entry for entry in everything if entry["sheet"] in (2, 3)]
    assert len(kept) == len(expected)
    assert [op["op"] for op in kept].count("save") == 2


@pytest.mark.parametrize(
    ("raw", "chosen"),
    [(None, None), ("3", [3]), (" 3, 2,3", [2, 3])],
)
def test_sheets_are_read_as_a_sorted_set(raw, chosen) -> None:
    assert cli._sheets_option({"sheets": raw}, [1, 2, 3, 4]) == chosen


@pytest.mark.parametrize("raw", ["two", "1,x", ",", ""])
def test_sheets_that_are_not_numbers_are_refused(raw) -> None:
    with pytest.raises(CLIError) as caught:
        cli._sheets_option({"sheets": raw}, [1, 2, 3, 4])
    assert caught.value.code == "E_VALIDATION"


def test_a_sheet_the_design_does_not_list_is_refused() -> None:
    with pytest.raises(CLIError) as caught:
        cli._sheets_option({"sheets": "3,7"}, [1, 2, 3, 4])
    assert caught.value.code == "E_VALIDATION"
    assert caught.value.details["sheets"] == [7]
    assert caught.value.details["design_sheets"] == [1, 2, 3, 4]


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_the_dry_run_previews_only_the_chosen_sheets(tmp_path, capsys) -> None:
    code, result = run(
        capsys,
        "schematic", "draw", "--project", str(tmp_path / "p.prj"), "--design", str(DEMO),
        "--sheets", "2,3", "--dry-run",
    )  # fmt: skip
    assert code == 0 and result["ok"], result
    data = result["data"]
    assert [change["sheet"] for change in data["preview"]["changes"]] == [2, 3]
    assert data["preview"]["sheets_kept"] == [1, 4]
    assert data["operations"][0]["op"] == "open_sheet"
    assert {entry["sheet"] for entry in data["operations"]} == {2, 3}


def test_pace_is_bounded(tmp_path, capsys) -> None:
    code, result = run(
        capsys,
        "schematic", "draw", "--project", str(tmp_path / "p.prj"), "--design", str(DEMO),
        "--pace", "20", "--dry-run",
    )  # fmt: skip
    assert code == 2 and result["error"]["code"] == "E_VALIDATION"


@pytest.fixture
def native(tmp_path, monkeypatch):
    """A configured NativeBackend whose adapter call is recorded, not run."""
    binary = tmp_path / "native-adapter.exe"
    binary.write_bytes(b"placeholder")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(binary))
    monkeypatch.setattr(native_xpedition.NativeBackend, "require_implemented", lambda self: None)
    calls: list[dict] = []

    def fake_run(argv, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        data = {"applied": 1, "operations": {}, "sheets_drawn": [2], "_untrusted": []}
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": data}), "")

    monkeypatch.setattr(native_xpedition.subprocess, "run", fake_run)
    return calls


def draw(capsys, tmp_path, sheets: str, *extra: str) -> tuple[int, dict]:
    return run(
        capsys,
        "schematic", "draw", "--backend", "native_xpedition", "--project", str(tmp_path / "p.prj"),
        "--design", str(DEMO), "--sheets", sheets, *extra,
    )  # fmt: skip


def test_the_token_binds_the_chosen_sheets(tmp_path, capsys, native) -> None:
    _, preview = draw(capsys, tmp_path, "2", "--dry-run")
    token = preview["data"]["confirm_token"]
    code, result = draw(capsys, tmp_path, "3", "--confirm", token)
    assert code != 0 and result["error"]["code"] == "E_CONFLICT", result
    assert native == [], "a token for other sheets must not reach Designer"


def test_a_confirmed_resume_sends_only_those_sheets(tmp_path, capsys, native) -> None:
    _, preview = draw(capsys, tmp_path, "2", "--dry-run")
    code, result = draw(
        capsys, tmp_path, "2", "--pace", "0.2", "--confirm", preview["data"]["confirm_token"]
    )
    assert code == 0 and result["ok"], result
    [request] = native
    params = request["params"]
    assert request["method"] == "draw"
    assert {op["number"] for op in params["ops"] if op["op"] == "open_sheet"} == {2}
    # the adapter learns the whole design, so the other sheets read as kept
    assert params["design_sheets"] == [1, 2, 3, 4]
    assert params["pace"] == 0.2
    # and the netlist check still covers the whole design
    assert params["verify"]["nets"]


# ---- the adapter's bookkeeping, against a stand-in for Designer ----


class FakeText:
    Size = 0


class FakeBlock:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def AddText(self, text, x, y):  # noqa: N802 - COM method name
        self.texts.append(text)
        return FakeText()


class FakeDocument:
    def __init__(self, fail_on_save: int | None) -> None:
        self.saves = 0
        self.fail_on_save = fail_on_save

    def Save(self) -> None:  # noqa: N802 - COM method name
        self.saves += 1
        if self.fail_on_save == self.saves:
            raise RuntimeError("the automation object went away")


class NoPrompts:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def blocking_dialogs(self) -> list:
        return []


@pytest.fixture
def designer(tmp_path, monkeypatch):
    """Run the adapter's `_draw` on a stand-in Designer with sheets 1-5."""
    project = tmp_path / "Board.prj"
    project.write_text("KEY DesignName Board1\n", encoding="utf-8")
    monkeypatch.setattr(adapter, "_prj_lists", lambda *a: True)
    monkeypatch.setattr(adapter, "_ensure_project", lambda *a: None)
    monkeypatch.setattr(adapter, "_constants", lambda client, names: {"VD_WIRE": 1})
    monkeypatch.setattr(adapter, "_open_sheet", lambda *a: None)
    monkeypatch.setattr(adapter, "_ensure_sheet", lambda *a: None)
    monkeypatch.setattr(adapter, "_PromptAnswerer", NoPrompts)
    monkeypatch.setattr(adapter, "_warn_when_the_library_is_missing", lambda *a: None)
    monkeypatch.setattr(adapter, "_sheet_numbers", lambda app: [1, 2, 3, 4, 5])

    def run_draw(ops, fail_on_save=None, **params):
        app = SimpleNamespace(
            ActiveDocument=FakeDocument(fail_on_save),
            ActiveView=SimpleNamespace(Block=FakeBlock()),
        )
        monkeypatch.setattr(adapter, "_viewdraw_application", lambda client, **kw: app)
        return adapter._draw({"project": str(project), "ops": ops, **params}, client=None)

    return run_draw


def sheet(number: int) -> list[dict]:
    return [
        {"op": "open_sheet", "number": number},
        {"op": "text", "text": f"sheet {number}", "x": 10, "y": 10},
        {"op": "save"},
    ]


def test_an_adapter_error_names_the_sheets_already_saved(designer) -> None:
    ops = sheet(1) + sheet(2) + [{"op": "open_sheet", "number": 3}, {"op": "bogus"}]
    with pytest.raises(adapter.AdapterError) as caught:
        designer(ops)
    details = caught.value.details
    assert details["sheet"] == 3
    assert details["operation"] == "bogus"
    assert details["index"] == 7
    assert details["sheets_drawn"] == [1, 2]
    assert details["sheets_remaining"] == [3]
    assert "--sheets 3" in details["hint"]


def test_a_designer_failure_names_them_too(designer) -> None:
    with pytest.raises(adapter.AdapterError) as caught:
        designer(sheet(1) + sheet(2) + sheet(3) + sheet(4), fail_on_save=3)
    details = caught.value.details
    assert (details["sheet"], details["operation"]) == (3, "save")
    assert details["sheets_drawn"] == [1, 2]
    assert details["sheets_remaining"] == [3, 4]
    assert "--sheets 3,4" in details["hint"]


def test_nothing_saved_means_no_resume_hint(designer) -> None:
    with pytest.raises(adapter.AdapterError) as caught:
        designer(sheet(1) + sheet(2), fail_on_save=1)
    assert caught.value.details["sheets_drawn"] == []
    assert "hint" not in caught.value.details


def test_a_partial_draw_keeps_the_design_sheets_it_skipped(designer) -> None:
    result = designer(sheet(2) + sheet(3), design_sheets=[1, 2, 3, 4])
    assert result["sheets_drawn"] == [2, 3]
    assert result["sheets_kept"] == [1, 4]
    # only a sheet the design does not list at all is a leftover
    assert result["sheets_not_drawn"] == [5]


def test_a_full_draw_reports_no_kept_sheets(designer) -> None:
    result = designer(sheet(1) + sheet(2))
    assert result["sheets_drawn"] == [1, 2]
    assert "sheets_kept" not in result
    assert result["sheets_not_drawn"] == [3, 4, 5]


def test_pace_waits_after_each_drawn_item_only(designer, monkeypatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr(adapter.time, "sleep", waits.append)
    designer(sheet(1) + sheet(2), pace=0.25)
    # two text items; opening and saving a sheet are not paced
    assert waits == [0.25, 0.25]


def test_the_resume_hint_joins_the_raisers_own(designer, monkeypatch) -> None:
    # What the live test hit: the project was closed under the draw on sheet 3, and
    # the sheet guard said "retry the draw" -- which alone would redraw 1 and 2 too.
    def guard(app, number):
        if number == 3:
            raise adapter.AdapterError(
                "E_CONFLICT",
                "sheet 3 is not the active sheet",
                {"hint": "another sheet window took over; retry the draw"},
            )

    monkeypatch.setattr(adapter, "_ensure_sheet", guard)
    with pytest.raises(adapter.AdapterError) as caught:
        designer(sheet(1) + sheet(2) + sheet(3))
    hint = caught.value.details["hint"]
    assert hint.startswith("another sheet window took over; retry the draw; ")
    assert "--sheets 3 draws the rest" in hint
