"""Closed public file-copy provenance; synthetic bytes, no native or SQLite calls."""

import copy
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from xpedition_cli import project_backup as B
from xpedition_cli import project_isolated_copy as I
from xpedition_cli.cli import project as CLI
from xpedition_cli.reference_data import reference


def fixture(tmp_path, *, external=False):
    root = tmp_path / "source"
    library = root / "Library"
    text = (
        'SECTION DesignInfo\nKEY CentralLibrary "' + str(library / "Library.lmc") + '"\n'
        'KEY DBCFile "' + str(library / "Database.dbc") + '"\n'
        'KEY Note "' + str(root / "do-not-rewrite-this-unrelated-text") + '"\nENDSECTION\n'
        'SECTION iCDB\nKEY iCDBDir "./database"\nLIST Designs\n'
        'VALUE "Board1"\nENDLIST\nENDSECTION\n'
        'SECTION Board1\nKEY ConfigType "PCB"\nKEY PCBDesignPath "PCB\\Board.pcb"\n'
        'LIST Symbols\nVALUE "SymbolLibs\\Globals"\nENDLIST\n'
        'LIST PDBs\nVALUE "PartsDBLibs\\P.pdb"\nENDLIST\n'
        'LIST 2dCellLibraries\nVALUE "CellDBLibs\\P.cel"\nENDLIST\nENDSECTION\n'
    )
    if external:
        text = text.replace(str(library / "Library.lmc"), str(tmp_path / "shared.lmc"))
        (tmp_path / "shared.lmc").write_bytes(b"external")
    files = {
        "Board.prj": text.encode("utf-8"),
        "PCB/Board.pcb": b"synthetic board bytes",
        "database/icdb.dat": b"unique synthetic database payload",
        "Library/Library.lmc": b"library",
        "Library/Database.dbc": b"dbc",
        "Library/SymbolLibs/Globals/global.1": b"symbol",
        "Library/PartsDBLibs/P.pdb": b"pdb",
        "Library/CellDBLibs/P.cel": b"cell",
        "Work/retained.tmp": b"all-file copy",
        "PCB/LogFiles/retained.txt": b"retained log",
        "old.bak": b"retained backup",
    }
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    source, archive = root / "Board.prj", tmp_path / "source.zip"
    B.create(source, archive)
    return source, archive, tmp_path / "isolated/Board.prj", files


def rewrite_archive(archive, mutate):
    with zipfile.ZipFile(archive) as source:
        payload = {name: source.read(name) for name in source.namelist()}
    mutate(payload)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as target:
        for name, raw in payload.items():
            target.writestr(name, raw)


def test_derivation_is_file_only_and_changes_only_public_library_references(tmp_path):
    source, archive, target, files = fixture(tmp_path)
    derived = I.derive_copy(source, target, archive)
    assert not target.parent.exists()
    assert derived["contract"] == I.CONTRACT
    expected = derived["genesis"]["file_manifest"]
    assert set(expected) == set(files)
    assert {
        name for name in files if expected[name] != hashlib.sha256(files[name]).hexdigest()
    } == {"Board.prj"}
    assert derived["exact_differences"][0]["kind"] == "public_prj_library_references_only"
    assert [
        row["key"]
        for row in derived["registration"]["data"]["provenance"]["project_library_relocation"][
            "rewritten"
        ]
    ] == ["CentralLibrary", "DBCFile"]
    assert len(derived["library_closure"]) == 5


@pytest.mark.parametrize("empty", [False, True])
def test_complete_copy_and_independent_full_target_verification(tmp_path, empty):
    source, archive, target, files = fixture(tmp_path)
    if empty:
        target.parent.mkdir()
    plan = I.plan_copy(source, target, archive)
    result = I.apply_copy(plan)
    assert result["created"] and result["file_copy_verified"]
    assert result["opened"] is False and result["native_calls_dispatched"] == 0
    assert result["native_open_verified"] is False
    proof = I.verify_copy(result)
    assert proof["valid"] and proof["complete"]
    assert proof["actual_target_manifest"] == plan["genesis"]["file_manifest"]
    assert set(proof["actual_target_files"]) == set(files)
    assert result["closed_copy"]["actual_target_manifest"] == proof["actual_target_manifest"]
    assert str(target.parent / "Library/Library.lmc") in target.read_text(encoding="utf-8")
    assert str(source.parent / "do-not-rewrite-this-unrelated-text") in target.read_text(
        encoding="utf-8"
    )
    assert not (target.parent / B.MANIFEST).exists()
    for name, raw in files.items():
        assert (source.parent / name).read_bytes() == raw
        if name != "Board.prj":
            assert (target.parent / name).read_bytes() == raw
    # Pure derivation is intentionally available after the copy, without reading target.
    assert I.derive_copy(source, target, archive)["genesis"] == plan["genesis"]


@pytest.mark.parametrize("fault", ["add", "delete", "change", "registration"])
def test_independent_verifier_refuses_any_target_or_registration_drift(tmp_path, fault):
    source, archive, target, _ = fixture(tmp_path)
    result = I.apply_copy(I.plan_copy(source, target, archive))
    if fault == "add":
        (target.parent / "extra.txt").write_bytes(b"extra")
    elif fault == "delete":
        (target.parent / "old.bak").unlink()
    elif fault == "change":
        (target.parent / "database/icdb.dat").write_bytes(b"changed")
    else:
        result["closed_copy"]["registration"]["data"]["verified"] = True
    assert I.verify_copy(result)["valid"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "source_changed",
        "source_extra",
        "no_icdb",
        "skipped",
        "hash",
        "traversal",
        "case_duplicate",
        "crc",
    ],
)
def test_invalid_or_incomplete_source_archive_never_creates_target(tmp_path, fault):
    source, archive, target, _ = fixture(tmp_path)
    if fault == "source_changed":
        (source.parent / "database/icdb.dat").write_bytes(b"changed")
    elif fault == "source_extra":
        (source.parent / "extra.txt").write_bytes(b"extra")
    elif fault == "no_icdb":
        (source.parent / "database/icdb.dat").unlink()
    elif fault in {"skipped", "hash"}:

        def mutate(payload):
            meta = json.loads(payload[B.MANIFEST])
            if fault == "skipped":
                meta["all_files"] = False
            else:
                meta["sha256"]["PCB/Board.pcb"] = "0" * 64
            payload[B.MANIFEST] = json.dumps(meta).encode()

        rewrite_archive(archive, mutate)
    elif fault == "traversal":
        rewrite_archive(archive, lambda payload: payload.update({"../escape.txt": b"unsafe"}))
    elif fault == "case_duplicate":
        rewrite_archive(
            archive, lambda payload: payload.update({"board.prj": payload["Board.prj"]})
        )
    else:
        rewrite_archive(archive, lambda payload: None)
        archive.write_bytes(
            archive.read_bytes().replace(
                b"unique synthetic database payload", b"broken synthetic database payload"
            )
        )
    with pytest.raises(I.CopyError):
        I.plan_copy(source, target, archive)
    assert not target.parent.exists()


def test_locked_source_icdb_is_not_skipped_or_replaced_by_a_source_only_hash(tmp_path, monkeypatch):
    source, archive, target, _ = fixture(tmp_path)
    original = Path.open

    def locked(path, *args, **kwargs):
        if path == source.parent / "database/icdb.dat":
            raise PermissionError("synthetic locked database")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", locked)
    with pytest.raises(I.CopyError):
        I.plan_copy(source, target, archive)
    assert not target.parent.exists()


def test_external_library_reference_is_not_silently_preserved_as_closed(tmp_path):
    source, archive, target, _ = fixture(tmp_path, external=True)
    with pytest.raises(I.CopyError):
        I.plan_copy(source, target, archive)
    assert not target.parent.exists()


@pytest.mark.parametrize("fault", ["renamed", "nested", "non_ascii", "occupied"])
def test_closed_copy_target_scope_is_explicit_and_disjoint(tmp_path, fault):
    source, archive, target, _ = fixture(tmp_path)
    if fault == "renamed":
        target = target.with_name("Renamed.prj")
    elif fault == "nested":
        target = source.parent / "nested/Board.prj"
    elif fault == "non_ascii":
        target = tmp_path / "隔离/Board.prj"
    else:
        target.parent.mkdir()
        (target.parent / "protected.txt").write_bytes(b"keep")
    with pytest.raises(I.CopyError):
        I.plan_copy(source, target, archive)


def test_write_failure_retains_partial_files_and_never_replays_or_cleans(tmp_path, monkeypatch):
    source, archive, target, _ = fixture(tmp_path)
    plan = I.plan_copy(source, target, archive)
    original = Path.open

    def fail(path, mode="r", *args, **kwargs):
        if path == target.parent / "PCB/Board.pcb" and mode == "xb":
            raise PermissionError("synthetic write failure")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(I.CopyError) as error:
        I.apply_copy(plan)
    known = error.value.details["native_result"]
    assert known["closed_copy"]["written_files"] and known["native_calls_dispatched"] == 0
    assert target.exists() and known["created"] is False
    with pytest.raises(I.CopyError):
        I.apply_copy(plan)
    assert target.exists()


def test_public_handler_preview_confirm_and_token_replay_have_zero_native_calls(
    cli, tmp_path, monkeypatch
):
    source, archive, target, _ = fixture(tmp_path)
    monkeypatch.setattr(
        CLI, "native", lambda: pytest.fail("Closed-copy must never get a native backend")
    )
    args = [
        "project",
        "create",
        "--template",
        str(source),
        "--project",
        str(target),
        "--closed-copy",
        "--source-backup",
        str(archive),
    ]
    code, planned = cli(*args, "--dry-run")
    assert code == 0, planned
    assert not target.parent.exists() and planned["data"]["preview"]["native_calls_dispatched"] == 0
    code, done = cli(*args, "--confirm", planned["data"]["confirm_token"])
    assert code == 0, done
    assert done["data"]["file_copy_verified"] and not done["data"]["opened"]
    code, _ = cli(*args, "--confirm", planned["data"]["confirm_token"])
    assert code != 0 and target.exists()


def test_create_discovery_declares_closed_copy_without_new_leaf():
    command = reference(command="project create")["commands"][0]
    assert {"closed-copy", "source-backup"} <= {row["name"] for row in command["params"]}
    assert "complete byte copying is not native openability" in command["description"]


def test_plan_fingerprint_and_current_source_are_rechecked_before_copy(tmp_path):
    source, archive, target, _ = fixture(tmp_path)
    plan = I.plan_copy(source, target, archive)
    altered = copy.deepcopy(plan)
    altered["fingerprint"] = "0" * 64
    with pytest.raises(I.CopyError):
        I.apply_copy(altered)
    assert not target.parent.exists()
