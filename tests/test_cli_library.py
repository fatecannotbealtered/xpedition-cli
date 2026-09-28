"""The library commands (library import is covered in test_kicad_import_command)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import LIBRARY_PARTS, FakeLibrary

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "demo-sensor-board.json"


def _design(tmp_path: Path) -> Path:
    path = tmp_path / "design.json"
    path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def _imported(params: dict) -> dict:
    return {
        "project": params["project"],
        "library": "C:/Lib/Lib.lmc",
        "partition": params["partition"],
        "steps": [],
        "failed": [],
        "pdb_registered": True,
        "cells_registered": [],
        "cells_missing": [],
        "ok": True,
        "hint": None,
    }


@pytest.fixture
def library(adapter, tmp_path) -> FakeLibrary:
    fake = FakeLibrary(tmp_path / "Lib")
    adapter.on("library_export", fake.export)
    adapter.on("library_import", fake.absorb)
    return fake


def test_the_dry_run_plans_every_part_reading_only_the_library(
    cli, adapter, library, project, tmp_path
) -> None:
    design = _design(tmp_path)
    code, payload = cli(
        "library", "build", "--project", str(project), "--design", str(design), "--dry-run"
    )
    preview = payload["data"]["preview"]
    assert code == 0 and preview["partition"] == "PartQuest"
    assert preview["parts"] > 0 and preview["cells"] > 0 and preview["padstacks"] > 0
    assert preview["summary"]["parts"] and preview["library_parts"] == []
    assert "package_design" not in [change["action"] for change in preview["changes"]]
    # the parts it holds are read, to see which placeholders it would replace
    assert adapter.methods() == ["library_export"]
    assert adapter.last("library_export")["kinds"] == ["parts"]


def test_the_confirmed_run_imports_and_with_package_packages(
    cli, adapter, library, project, tmp_path
) -> None:
    design = _design(tmp_path)
    adapter.on("library_import", _imported)
    adapter.on("package", {"packaged": True, "errors": []})
    args = ["library", "build", "--project", str(project), "--design", str(design), "--package"]
    _, payload = cli(*args, "--dry-run")
    assert payload["data"]["preview"]["changes"][-1] == {"action": "package_design"}
    code, payload = cli(*args, "--confirm", payload["data"]["confirm_token"])
    assert code == 0 and payload["data"]["ok"] is True
    assert payload["data"]["package"] == {"packaged": True, "errors": []}
    assert [m for m in adapter.methods() if m != "library_export"] == ["library_import", "package"]
    sent = adapter.last("library_import")
    assert sent["partition"] == "PartQuest" and {"padstacks", "cells", "parts"} <= set(sent)


def test_a_failed_import_is_not_packaged(cli, adapter, library, project, tmp_path) -> None:
    design = _design(tmp_path)
    adapter.on(
        "library_import", lambda params: {**_imported(params), "ok": False, "failed": ["cells"]}
    )
    args = ["library", "build", "--project", str(project), "--design", str(design), "--package"]
    _, payload = cli(*args, "--dry-run")
    code, payload = cli(*args, "--confirm", payload["data"]["confirm_token"])
    assert code == 0 and payload["data"]["ok"] is False and "package" not in payload["data"]
    assert [m for m in adapter.methods() if m != "library_export"] == ["library_import"]


def test_the_token_binds_the_package_choice_and_the_partition(
    cli, adapter, library, project, tmp_path
) -> None:
    design = _design(tmp_path)
    args = ["library", "build", "--project", str(project), "--design", str(design)]
    _, payload = cli(*args, "--dry-run")
    token = payload["data"]["confirm_token"]
    code, payload = cli(*args, "--package", "--confirm", token)
    assert code == 6 and payload["error"]["code"] == "E_CONFLICT"
    _, payload = cli(*args, "--dry-run")
    code, payload = cli(
        *args, "--partition", "Other", "--confirm", payload["data"]["confirm_token"]
    )
    assert code == 6
    assert "library_import" not in adapter.methods()


def test_a_design_that_cannot_be_packaged_is_refused(
    cli, adapter, library, project, tmp_path
) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"sheets": "nope"}), encoding="utf-8")
    code, payload = cli(
        "library", "build", "--project", str(project), "--design", str(broken), "--dry-run"
    )
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION"
    code, payload = cli(
        "library",
        "build",
        "--project",
        str(project),
        "--design",
        str(tmp_path / "none.json"),
        "--dry-run",
    )
    assert code == 3 and payload["error"]["code"] == "E_NOT_FOUND"
    not_json = tmp_path / "bad.json"
    not_json.write_text("{", encoding="utf-8")
    code, payload = cli(
        "library", "build", "--project", str(project), "--design", str(not_json), "--dry-run"
    )
    assert code == 2


def test_build_without_the_adapter_says_so(cli, project, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", "Z:/no/such/adapter.exe")
    design = _design(tmp_path)
    args = ["library", "build", "--project", str(project), "--design", str(design)]
    code, payload = cli(*args, "--dry-run")
    assert code == 4 and payload["error"]["code"] == "E_BACKEND_UNAVAILABLE"
    assert payload["error"]["details"]["hint"]


# ---- library list | show | check | add | render, against a faked central library ----


def _parts_file(tmp_path: Path, parts: list[dict], partition: str = "PartQuest") -> Path:
    path = tmp_path / "parts.json"
    path.write_text(json.dumps({"partition": partition, "parts": parts}), encoding="utf-8")
    return path


LDO = {
    "number": "LDO-3V3",
    "description": "LDO regulator 3.3 V, SOT-23-5",
    "prefix": "U",
    "properties": {"Manufacturer": "Acme"},
    "symbol": {
        "left": [["1", "IN"], ["3", "EN"]],
        "right": [["5", "OUT"], ["4", "NC"]],
        "bottom": [["2", "GND"]],
    },
    "footprint": {
        "family": "gullwing",
        "pins": 5,
        "positions": 6,
        "omit": [5],
        "pitch": 0.95,
        "span": [2.6, 3.0],
        "terminal": [0.3, 0.6],
        "lead_width": [0.3, 0.5],
        "body": [1.6, 2.9],
        "height": 1.45,
    },
}


def test_list_pages_the_parts_and_asks_only_for_the_partitions_named(
    cli, adapter, library, project
) -> None:
    code, payload = cli("library", "list", "--project", str(project), "--limit", "1")
    data = payload["data"]
    assert code == 0 and data["kind"] == "parts" and data["total"] == 2
    assert data["count"] == 1 and data["has_more"] is True
    assert data["items"][0]["number"] == "MCU-8" and data["items"][0]["pins"] == 8
    assert adapter.last("library_export")["partitions"] is None
    code, payload = cli(
        "library", "list", "--project", str(project), "--kind", "cells", "--partition", "PartQuest"
    )
    cells = {row["name"]: row for row in payload["data"]["items"]}
    assert code == 0 and set(cells) == {"RESC1005X35N", "SOIC127P600X175-8N"}
    assert cells["SOIC127P600X175-8N"]["pin_numbers"] == 8
    assert adapter.last("library_export")["partitions"] == ["PartQuest"]


def test_list_symbols_and_padstacks_and_query(cli, library, project) -> None:
    code, payload = cli("library", "list", "--project", str(project), "--kind", "symbols")
    names = {row["reference"] for row in payload["data"]["items"]}
    assert code == 0 and any(name.startswith("PartQuest:RES_") for name in names)
    code, payload = cli(
        "library", "list", "--project", str(project), "--kind", "padstacks", "--query", "RRECT1.95"
    )
    rows = payload["data"]["items"]
    assert code == 0 and rows and all("RRECT1.95" in row["name"] for row in rows)
    assert rows[0]["type"] == "PIN_SMD" and rows[0]["width"] == 1.95


def test_show_a_part_gives_its_pin_map_symbol_cell_and_padstacks(cli, library, project) -> None:
    code, payload = cli("library", "show", "--project", str(project), "--part", "MCU-8")
    data = payload["data"]
    assert code == 0 and data["part"]["cell"] == "SOIC127P600X175-8N"
    mapping = {row["name"]: row["number"] for row in data["part"]["pins"]}
    assert mapping["VDD"] == "1" and mapping["SWCLK"] == "5"
    assert data["symbols"][0]["pins"][0]["name"] == "VDD"
    assert len(data["cell"]["pins"]) == 8 and data["findings"] == []
    stack = data["padstacks"]["SMD-RRECT1.95X0.6R0.15"]
    assert stack["pad"]["width"] == 1.95 and stack["mask"]["width"] == 2.05


def test_show_a_cell_lists_the_parts_that_use_it(cli, library, project) -> None:
    code, payload = cli(
        "library", "show", "--project", str(project), "--cell", "SOIC127P600X175-8N"
    )
    assert code == 0 and payload["data"]["used_by"] == ["MCU-8"]


def test_show_needs_one_of_part_and_cell_and_names_what_is_missing(cli, library, project) -> None:
    code, payload = cli("library", "show", "--project", str(project))
    assert code == 2 and payload["error"]["code"] == "E_USAGE"
    code, payload = cli("library", "show", "--project", str(project), "--part", "A", "--cell", "B")
    assert code == 2 and payload["error"]["code"] == "E_USAGE"
    code, payload = cli("library", "show", "--project", str(project), "--part", "NOPE")
    assert payload["error"]["code"] == "E_NOT_FOUND" and "library list" in json.dumps(payload)


def test_check_is_clean_on_a_consistent_library(cli, library, project) -> None:
    code, payload = cli("library", "check", "--project", str(project))
    data = payload["data"]
    assert code == 0 and data["clean"] is True and data["parts"] == 2
    assert data["counts"] == {"high": 0, "medium": 0, "low": 0}


def test_check_finds_a_part_whose_cell_is_missing(cli, library, project) -> None:
    broken = library.cache / "parts-Extra.hkp"
    text = (library.cache / "parts-PartQuest.hkp").read_text(encoding="utf-8")
    broken.write_text(
        text.replace('"MCU-8"', '"MCU-9"').replace("SOIC127P600X175-8N", "NO_SUCH_CELL"),
        encoding="utf-8",
    )
    code, payload = cli("library", "check", "--project", str(project))
    data = payload["data"]
    rules = {(f["rule"], f["item"]) for f in data["items"]}
    assert code == 0 and data["clean"] is False
    assert ("part_cell_missing", "part MCU-9") in rules
    assert data["items"][0]["severity"] == "high"


def test_add_previews_then_imports_and_verifies(cli, adapter, library, project, tmp_path) -> None:
    parts = _parts_file(tmp_path, [LDO, {**LIBRARY_PARTS["parts"][0], "number": "RES-4K7"}])
    code, payload = cli(
        "library", "add", "--project", str(project), "--file", str(parts), "--dry-run"
    )
    preview = payload["data"]["preview"]
    assert code == 0 and preview["dangerous"] is False and preview["replaces"] == []
    actions = {a["number"]: a["action"] for a in preview["actions"]["parts"]}
    assert actions == {"LDO-3V3": "add", "RES-4K7": "add"}
    # the resistor's symbol and cell are in the library already: left alone
    symbols = {a["action"] for a in preview["actions"]["symbols"] if a["name"].startswith("RES_")}
    assert symbols == {"keep"}
    cells = {a["name"]: a["action"] for a in preview["actions"]["cells"]}
    assert cells["RESC1005X35N"] == "keep" and cells["SOT95P280X145-5N"] == "add"
    assert "library_import" not in adapter.methods()
    token = payload["data"]["confirm_token"]
    code, payload = cli(
        "library", "add", "--project", str(project), "--file", str(parts), "--confirm", token
    )
    data = payload["data"]
    assert code == 0 and data["ok"] is True and data["verification"]["verified"] is True
    sent = adapter.last("library_import")
    assert sent["replace"] is False and sent["partition"] == "PartQuest"
    assert all(name.startswith("LDO") for name in sent["symbols"])
    assert '.Number "LDO-3V3"' in sent["parts"] and "RESC1005X35N" not in sent["cells"]
    assert '"Manufacturer",\t"Acme"' in sent["parts"]


def test_add_replacing_a_part_needs_dangerous(cli, adapter, library, project, tmp_path) -> None:
    changed = {**LIBRARY_PARTS["parts"][0], "description": "resistor 10k 1% 0402"}
    parts = _parts_file(tmp_path, [changed])
    code, payload = cli(
        "library", "add", "--project", str(project), "--file", str(parts), "--dry-run"
    )
    preview = payload["data"]["preview"]
    assert preview["dangerous"] is True and preview["replaces"] == ["part RES-10K"]
    token = payload["data"]["confirm_token"]
    args = ["library", "add", "--project", str(project), "--file", str(parts)]
    code, payload = cli(*args, "--confirm", token)
    assert payload["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    assert "library_import" not in adapter.methods()
    code, payload = cli(*args, "--dangerous", "--confirm", token)
    assert code == 0 and adapter.last("library_import")["replace"] is True


def test_add_refuses_a_number_another_partition_holds(cli, library, project, tmp_path) -> None:
    parts = _parts_file(tmp_path, [LIBRARY_PARTS["parts"][0]], partition="Other")
    code, payload = cli(
        "library", "add", "--project", str(project), "--file", str(parts), "--dry-run"
    )
    assert payload["error"]["code"] == "E_VALIDATION"
    assert "partition PartQuest already" in payload["error"]["message"]


def test_add_refuses_a_symbol_and_cell_that_do_not_match(cli, library, project, tmp_path) -> None:
    wrong = {**LDO, "number": "LDO-X", "footprint": {**LDO["footprint"], "omit": []}}
    wrong["footprint"]["pins"] = 6
    parts = _parts_file(tmp_path, [wrong])
    code, payload = cli(
        "library", "add", "--project", str(project), "--file", str(parts), "--dry-run"
    )
    assert payload["error"]["code"] == "E_VALIDATION"
    assert "cell pins ['6'] have no symbol pin" in payload["error"]["message"]


def test_render_draws_a_parts_file_without_the_library(cli, adapter, tmp_path) -> None:
    pytest.importorskip("PIL")
    parts = _parts_file(tmp_path, [LDO, LIBRARY_PARTS["parts"][1]])
    output = tmp_path / "parts.png"
    code, payload = cli("library", "render", "--file", str(parts), "--output", str(output))
    pictures = payload["data"]["pictures"]
    assert code == 0 and [p["part"] for p in pictures] == ["LDO-3V3", "MCU-8"]
    assert all(Path(p["path"]).is_file() for p in pictures)
    assert pictures[0]["lands"] == 5 and pictures[1]["symbol_pins"] == 8
    assert adapter.calls == []
    code, payload = cli("library", "render", "--file", str(parts), "--output", str(output))
    assert payload["error"]["code"] == "E_CONFLICT"


def test_render_draws_a_library_part(cli, library, project, tmp_path) -> None:
    pytest.importorskip("PIL")
    output = tmp_path / "mcu.png"
    code, payload = cli(
        "library", "render", "--project", str(project), "--part", "MCU-8", "--output", str(output)
    )
    picture = payload["data"]["pictures"][0]
    assert code == 0 and output.is_file() and picture["lands"] == 8
    assert picture["extent_mm"][0] > 6
