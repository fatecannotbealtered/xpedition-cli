"""library import: parts of another Xpedition central library, with what they use."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from fakes import LIBRARY_PARTS, FakeLibrary

from xpedition_cli import library_import as I
from xpedition_cli import library_read as R

CAPACITOR = {
    "partition": "PartQuest",
    "parts": [
        {
            "number": "CAP-100N",
            "description": "capacitor 100n 0402",
            "prefix": "C",
            "value": "100n",
            "symbol": {"kind": "CAP"},
            "footprint": {"family": "chip", "size": "0402"},
        }
    ],
}


def _read(fake: FakeLibrary) -> R.Library:
    """What `load_library` makes of a fake library, the export texts kept."""
    library = R.Library(root=str(fake.folder))
    for path in sorted(fake.cache.glob("*.hkp")):
        kind, _, partition = path.stem.partition("-")
        text = path.read_text(encoding="utf-8")
        library.texts[(kind, partition)] = text
        if kind == "parts":
            library.parts += R.parse_parts(text, partition)
        elif kind == "cells":
            library.cells += R.parse_cells(text, partition)
        else:
            library.padstacks = R.parse_padstacks(text)
    for partition, name, path in R.symbol_files(fake.symbols):
        symbol = R.parse_symbol(path.read_text(encoding="utf-8"), partition, name)
        symbol.update(version=int(path.suffix[1:]), path=str(path))
        library.symbols.append(symbol)
    return library


def _spec(**changes) -> dict:
    """LIBRARY_PARTS with the resistor changed."""
    spec = copy.deepcopy(LIBRARY_PARTS)
    spec["parts"][0].update(changes)
    return spec


# -- the export's records as text --------------------------------------------------------


def test_an_export_splits_into_its_header_and_its_items(tmp_path) -> None:
    fake = FakeLibrary(tmp_path / "Lib")
    text = (fake.cache / "parts-PartQuest.hkp").read_text(encoding="utf-8")
    header, items = R.hkp_blocks(text)
    assert ".Filetype ASCII_PDB" in header and ".Number" not in header
    assert sorted(name for keyword, name, _ in items if keyword == "Number") == [
        "MCU-8",
        "RES-10K",
    ]
    subset = R.hkp_subset(text, {("Number", "MCU-8")})
    assert [p["number"] for p in R.parse_parts(subset)] == ["MCU-8"]
    assert subset.startswith(header.rstrip("\n")) and R.hkp_subset(text, {("Number", "X")}) == ""


# -- the plan ---------------------------------------------------------------------------


def test_a_part_comes_with_its_symbol_cell_padstacks_pads(tmp_path) -> None:
    source = _read(FakeLibrary(tmp_path / "Source"))
    target = _read(FakeLibrary(tmp_path / "Target", CAPACITOR))
    plan = I.plan(source, target, ["MCU-8"])
    assert plan.parts == [{"number": "MCU-8", "partition": "PartQuest", "action": "add"}]
    assert [s["action"] for s in plan.symbols] == ["add"]
    assert [(c["name"], c["action"]) for c in plan.cells] == [("SOIC127P600X175-8N", "add")]
    assert {p["kind"] for p in plan.padstacks} == {"padstack", "pad"}
    assert not plan.replaces and plan.cell_partitions == ["PartQuest"]
    unit = plan.units[0]
    assert [p["number"] for p in R.parse_parts(unit["parts"])] == ["MCU-8"]
    assert [c["name"] for c in R.parse_cells(unit["cells"])] == ["SOIC127P600X175-8N"]
    assert list(unit["symbols"]) == [plan.symbols[0]["name"]] and unit["replace"] is False
    stacks = R.parse_padstacks(plan.padstack_text)
    assert set(stacks["padstacks"]) == {
        p["name"] for p in plan.padstacks if p["kind"] == "padstack"
    }


def test_what_the_library_holds_identically_is_kept_and_not_sent(tmp_path) -> None:
    source = _read(FakeLibrary(tmp_path / "Source"))
    target = _read(FakeLibrary(tmp_path / "Target"))
    plan = I.plan(source, target, ["RES-10K", "MCU-8"])
    assert {
        item["action"]
        for kind in ("parts", "symbols", "cells", "padstacks")
        for item in getattr(plan, kind)
    } == {"keep"}
    assert plan.empty() and not plan.replaces


def test_other_content_under_a_held_name_is_a_replacement(tmp_path) -> None:
    source = _read(FakeLibrary(tmp_path / "Source"))
    target = _read(FakeLibrary(tmp_path / "Target", _spec(description="resistor 10k 1%")))
    plan = I.plan(source, target, ["RES-10K"])
    assert plan.parts[0]["action"] == "replace" and plan.replaces == ["part RES-10K"]
    assert plan.units[0]["replace"] is True


def test_a_padstack_whose_pad_differs_is_not_the_same_padstack(tmp_path) -> None:
    source = _read(FakeLibrary(tmp_path / "Source"))
    target_fake = FakeLibrary(tmp_path / "Target")
    path = target_fake.cache / "padstacks-.hkp"
    header, blocks = R.hkp_blocks(path.read_text(encoding="utf-8"))
    # one pad larger in the library than in the source, under the same name
    pad = next(name for keyword, name, _ in blocks if keyword == "PAD")
    grown = "".join(
        block.replace("...WIDTH ", "...WIDTH 9") if (keyword, name) == ("PAD", pad) else block
        for keyword, name, block in blocks
    )
    path.write_text(header + grown, encoding="utf-8")
    target = _read(target_fake)
    target.cells = []  # the library lacks the cells, so their padstacks are met with its own
    plan = I.plan(source, target, ["RES-10K", "MCU-8"])
    replaced = {(p["kind"], p["name"]) for p in plan.padstacks if p["action"] == "replace"}
    users = {name for name, stack in source.padstacks["padstacks"].items() if pad in stack.values()}
    assert ("pad", pad) in replaced and users
    assert {("padstack", name) for name in users} <= replaced


def test_a_part_the_source_lacks_is_named(tmp_path) -> None:
    source = _read(FakeLibrary(tmp_path / "Source"))
    with pytest.raises(I.LibraryImportError) as caught:
        I.plan(source, R.Library(), ["NOPE", "MCU-8"])
    assert caught.value.code == "E_NOT_FOUND" and caught.value.details["parts"] == ["NOPE"]


def test_a_part_number_or_cell_held_elsewhere_is_refused(tmp_path) -> None:
    source = _read(FakeLibrary(tmp_path / "Source"))
    elsewhere = copy.deepcopy(LIBRARY_PARTS)
    elsewhere["partition"] = "Other"
    target = _read(FakeLibrary(tmp_path / "Target", elsewhere))
    with pytest.raises(I.LibraryImportError) as caught:
        I.plan(source, target, ["MCU-8"])
    assert caught.value.code == "E_CONFLICT"
    assert "part MCU-8 is in partition Other" in caught.value.details["conflicts"][0]


def test_an_identical_cell_in_another_partition_is_used_where_it_is(tmp_path) -> None:
    source = _read(FakeLibrary(tmp_path / "Source"))
    cells_elsewhere = copy.deepcopy(LIBRARY_PARTS)
    cells_elsewhere["partition"] = "Other"
    target = _read(FakeLibrary(tmp_path / "Target", cells_elsewhere))
    # the target holds the cells, not the parts
    target.parts = []
    plan = I.plan(source, target, ["MCU-8"])
    assert [(c["action"], c["partition"]) for c in plan.cells] == [("keep", "Other")]
    assert plan.cell_partitions == ["Other"] and not plan.units[0]["cells"]


def test_a_partition_name_that_is_not_a_plain_ascii_name_is_refused(tmp_path) -> None:
    spec = copy.deepcopy(LIBRARY_PARTS)
    spec["partition"] = "Parts"
    source = _read(FakeLibrary(tmp_path / "Source", spec))
    for part in source.parts:
        part["partition"] = "电阻"
    source.texts[("parts", "电阻")] = source.texts.pop(("parts", "Parts"))
    with pytest.raises(I.LibraryImportError) as caught:
        I.plan(source, R.Library(), ["MCU-8"])
    assert caught.value.code == "E_VALIDATION" and caught.value.details["partitions"] == ["电阻"]


def test_a_part_whose_cell_the_source_lacks_is_refused(tmp_path) -> None:
    source = _read(FakeLibrary(tmp_path / "Source"))
    source.cells = []
    with pytest.raises(I.LibraryImportError) as caught:
        I.plan(source, R.Library(), ["MCU-8"])
    assert caught.value.details["missing"]["cells"] == ["SOIC127P600X175-8N"]


def test_the_numbers_named_are_taken_once_and_bounded() -> None:
    assert I.numbers(["A", " A ", "B"]) == ["A", "B"]
    with pytest.raises(I.LibraryImportError):
        I.numbers([])
    with pytest.raises(I.LibraryImportError):
        I.numbers([f"P{i}" for i in range(I.MAX_PARTS + 1)])


# -- the command ------------------------------------------------------------------------


@pytest.fixture
def libraries(adapter, tmp_path) -> tuple[FakeLibrary, FakeLibrary]:
    """The project's library (the target) and another one to import from."""
    target = FakeLibrary(tmp_path / "Target", CAPACITOR)
    source = FakeLibrary(tmp_path / "Source")
    adapter.on(
        "library_export",
        lambda params: (source if params.get("library") else target).export(params),
    )
    adapter.on("library_import", target.absorb)
    return target, source


def test_import_previews_then_imports_and_reads_back(cli, adapter, libraries, project) -> None:
    target, source = libraries
    args = ["library", "import", "--project", str(project), "--from", str(source.lmc)]
    code, payload = cli(*args, "--parts", "MCU-8", "--dry-run")
    preview = payload["data"]["preview"]
    assert code == 0 and preview["dangerous"] is False and preview["replaces"] == []
    assert preview["summary"]["parts"] == {"add": 1, "keep": 0, "replace": 0}
    assert [c["action"] for c in preview["changes"]] == [
        "merge_padstacks",
        "write_symbols",
        "merge_cells",
        "merge_parts",
        "register_in_project",
    ]
    # the dry run reads both libraries and writes nothing
    assert adapter.methods() == ["library_export", "library_export"]
    assert adapter.params("library_export")[1]["library"] == str(source.lmc.resolve())
    token = payload["data"]["confirm_token"]
    code, payload = cli(*args, "--parts", "MCU-8", "--confirm", token)
    sent = adapter.last("library_import")
    assert code == 0 and payload["data"]["verification"]["verified"] is True
    assert [u["partition"] for u in sent["units"]] == ["PartQuest"]
    assert (
        '.Number "MCU-8"' in sent["units"][0]["parts"]
        and "RES-10K" not in sent["units"][0]["parts"]
    )
    assert sent["padstacks"] and sent["cell_partitions"] == ["PartQuest"]


def test_import_of_what_the_library_holds_changes_nothing(cli, adapter, tmp_path, project) -> None:
    target = FakeLibrary(tmp_path / "Target")
    source = FakeLibrary(tmp_path / "Source")
    adapter.on(
        "library_export",
        lambda params: (source if params.get("library") else target).export(params),
    )
    args = ["library", "import", "--project", str(project), "--from", str(source.lmc)]
    code, payload = cli(*args, "--parts", "RES-10K", "--dry-run")
    assert code == 0 and "changes nothing" in payload["data"]["preview"]["note"]
    code, payload = cli(*args, "--parts", "RES-10K", "--confirm", payload["data"]["confirm_token"])
    assert code == 0 and payload["data"]["imported"] is False
    assert "library_import" not in adapter.methods()


def test_import_that_replaces_needs_dangerous(cli, adapter, tmp_path, project) -> None:
    target = FakeLibrary(tmp_path / "Target", _spec(description="resistor 10k 1%"))
    source = FakeLibrary(tmp_path / "Source")
    adapter.on(
        "library_export",
        lambda params: (source if params.get("library") else target).export(params),
    )
    adapter.on("library_import", target.absorb)
    args = ["library", "import", "--project", str(project), "--from", str(source.lmc)]
    code, payload = cli(*args, "--parts", "RES-10K", "--dry-run")
    preview = payload["data"]["preview"]
    assert preview["dangerous"] is True and preview["replaces"] == ["part RES-10K"]
    token = payload["data"]["confirm_token"]
    code, payload = cli(*args, "--parts", "RES-10K", "--confirm", token)
    assert code == 5 and payload["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    code, payload = cli(*args, "--parts", "RES-10K", "--dangerous", "--confirm", token)
    assert code == 0 and adapter.last("library_import")["units"][0]["replace"] is True


def test_import_takes_the_source_from_a_project(cli, adapter, libraries, project, tmp_path) -> None:
    _target, source = libraries
    other = tmp_path / "Other.prj"
    other.write_text(f'KEY CentralLibrary "{source.lmc}"\n', encoding="utf-8")
    code, payload = cli(
        "library",
        "import",
        "--project",
        str(project),
        "--from",
        str(other),
        "--parts",
        "MCU-8",
        "--dry-run",
    )
    assert code == 0 and payload["data"]["preview"]["source"] == str(source.lmc.resolve())


def test_import_refuses_what_it_cannot_take(cli, adapter, libraries, project, tmp_path) -> None:
    target, source = libraries
    args = ["library", "import", "--project", str(project)]
    code, payload = cli(*args, "--from", str(source.lmc), "--parts", "NOPE", "--dry-run")
    assert code == 3 and payload["error"]["code"] == "E_NOT_FOUND"
    code, payload = cli(*args, "--from", str(target.lmc), "--parts", "CAP-100N", "--dry-run")
    assert code == 2 and "own central library" in payload["error"]["message"]
    elsewhere = tmp_path / "parts.txt"
    elsewhere.write_text("", encoding="utf-8")
    code, payload = cli(*args, "--from", str(elsewhere), "--parts", "MCU-8", "--dry-run")
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION"
    code, payload = cli(
        *args, "--from", str(tmp_path / "none.lmc"), "--parts", "MCU-8", "--dry-run"
    )
    assert code == 3


def test_the_readers_read_a_library_by_its_lmc(cli, adapter, libraries, project) -> None:
    _target, source = libraries
    code, payload = cli("library", "list", "--library", str(source.lmc), "--query", "MCU")
    assert code == 0 and payload["data"]["project"] is None
    assert [item["number"] for item in payload["data"]["items"]] == ["MCU-8"]
    assert adapter.last("library_export")["library"] == str(source.lmc.resolve())
    code, payload = cli("library", "show", "--library", str(source.lmc), "--part", "MCU-8")
    assert code == 0 and payload["data"]["part"]["number"] == "MCU-8"
    code, payload = cli("library", "check", "--library", str(source.lmc))
    assert code == 0 and payload["data"]["parts"] == 2
    code, payload = cli("library", "list", "--project", str(project), "--library", str(source.lmc))
    assert code == 2
    code, payload = cli("library", "list")
    assert code == 2 and "--library" in payload["error"]["message"]


def test_render_draws_a_part_of_a_library_named_by_its_lmc(
    cli, adapter, libraries, tmp_path
) -> None:
    _target, source = libraries
    output = tmp_path / "part.png"
    code, payload = cli(
        "library",
        "render",
        "--library",
        str(source.lmc),
        "--part",
        "MCU-8",
        "--output",
        str(output),
    )
    assert code == 0 and Path(payload["data"]["pictures"][0]["path"]).is_file()
