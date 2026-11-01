"""Real SDK closed-copy receipts in an owned temporary operation journal.

The project and library bytes are synthetic. No CAD process, COM connection,
adapter executable, or developer operation journal is used.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3

import pytest
from test_driver_v2 import _scope
from test_project_isolated_copy import fixture as source_fixture

from xpedition_cli import component_copy_origin as O
from xpedition_cli import project_backup as B
from xpedition_cli import project_isolated_copy as I
from xpedition_cli.audit import config_dir
from xpedition_cli.backends import NativeBackend
from xpedition_cli.cli import project as project_cli
from xpedition_cli.driver import Adapter, runtime


def _args(source, archive, target):
    return [
        "project",
        "create",
        "--template",
        str(source),
        "--project",
        str(target),
        "--closed-copy",
        "--source-backup",
        str(archive),
        "--dangerous",
    ]


def _journal():
    return config_dir() / "driver-v2/operations.sqlite3"


@pytest.fixture
def file_only(monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("A closed file copy must not acquire or invoke Designer/Layout")

    monkeypatch.setattr(Adapter, "_identity", forbidden)
    monkeypatch.setattr(NativeBackend, "invoke", forbidden)
    monkeypatch.setattr(project_cli, "native", forbidden)
    yield calls
    assert calls == []


@pytest.fixture
def copied(tmp_path, monkeypatch, file_only):
    _scope(tmp_path, monkeypatch)
    source, archive, target, files = source_fixture(tmp_path)
    archive_bytes = archive.read_bytes()
    args = _args(source, archive, target)
    # Use the normal default state directory: verify_origin must find the SDK's
    # own record under this test's config_dir/driver-v2, not a fabricated Store.
    with runtime() as cli:
        preview = cli.run([*args, "--dry-run"])
    assert preview.exit_code == 0, preview.envelope
    assert not target.parent.exists()
    planned = preview.envelope["data"]["preview"]["extensions"]["legacy_preview"]
    assert planned["opened"] is False and planned["native_calls_dispatched"] == 0
    token = preview.envelope["data"]["confirm_token"]
    with runtime() as cli:
        result = cli.run([*args, "--confirm", token])
    assert result.exit_code == 0, result.envelope
    receipt = result.envelope["data"]["receipt"]
    origin = copy.deepcopy(receipt["extensions"]["result"]["closed_copy"]["registration"]["data"])
    origin["copy_operation_id"] = receipt["operation_id"]
    return {
        "source": source,
        "archive": archive,
        "archive_bytes": archive_bytes,
        "target": target,
        "board": target.parent / "PCB/Board.pcb",
        "files": files,
        "receipt": receipt,
        "origin": origin,
        "native_calls": file_only,
    }


def _rewrite_record(copied, mutate_receipt=None, **columns):
    """Corrupt only a real SDK-created record, in this test's private journal."""
    receipt = copy.deepcopy(copied["receipt"])
    if mutate_receipt is not None:
        mutate_receipt(receipt)
        columns["receipt"] = json.dumps(receipt)
    with sqlite3.connect(_journal()) as connection:
        for key, value in columns.items():
            assert key in {"command", "status", "scope_id", "receipt"}
            connection.execute(
                f"UPDATE operations SET {key}=? WHERE operation_id=?",
                (value, copied["origin"]["copy_operation_id"]),
            )


def _verify(copied, origin=None):
    return O.verify_origin(
        copied["origin"] if origin is None else origin,
        str(copied["target"]),
        str(copied["board"]),
    )


def test_public_sdk_copy_and_independent_readback_authenticate_original_origin(copied):
    receipt = copied["receipt"]
    producer = receipt["extensions"]["result"]
    proof = receipt["extensions"]["file_readback"]
    expected = copied["origin"]["genesis"]["file_manifest"]
    assert receipt["outcome"]["execution"] == "completed"
    assert receipt["outcome"]["persistence"] == "saved"
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert producer["creation_method"] == "closed_backup_copy"
    assert producer["created"] is True and producer["file_copy_verified"] is True
    assert producer["opened"] is False and producer["native_open_verified"] is False
    assert producer["native_calls_dispatched"] == 0 and copied["native_calls"] == []
    assert proof["valid"] is True and proof["complete"] is True
    assert proof["native_open_verified"] is False
    assert proof["actual_target_manifest"] == expected
    assert producer["closed_copy"]["actual_target_manifest"] == expected
    assert set(expected) == set(copied["files"])
    for name, raw in copied["files"].items():
        assert (copied["source"].parent / name).read_bytes() == raw
        assert (
            hashlib.sha256((copied["target"].parent / name).read_bytes()).hexdigest()
            == expected[name]
        )
    assert not (copied["target"].parent / B.MANIFEST).exists()
    assert copied["archive"].read_bytes() == copied["archive_bytes"]

    with runtime() as cli:
        shown = cli.run(["operation", "show", "--id", receipt["operation_id"]])
    status = shown.envelope["data"]["operation_status"]
    assert shown.exit_code == 0 and status["status"] == "succeeded"
    assert status["receipt"] == receipt
    with sqlite3.connect(_journal()) as connection:
        row = connection.execute(
            "SELECT command,status,scope_id,receipt FROM operations WHERE operation_id=?",
            (receipt["operation_id"],),
        ).fetchone()
    assert row[:3] == ("project create", "succeeded", receipt["authorization_scope_id"])
    assert json.loads(row[3]) == receipt

    journal_before = _journal().read_bytes()
    authenticated = _verify(copied)
    assert authenticated["operation_id"] == receipt["operation_id"]
    assert authenticated["genesis"] == copied["origin"]["genesis"]
    assert len(authenticated["receipt_sha256"]) == 64
    assert "successful public project create --closed-copy" in authenticated["source"]
    assert _journal().read_bytes() == journal_before


@pytest.mark.parametrize("operation_id", [None, True, 123, "verified", "op_00000000"])
def test_unknown_or_invented_operation_cannot_authenticate_manual_verified_origin(
    copied, operation_id
):
    origin = copy.deepcopy(copied["origin"])
    origin["verified"] = True
    if operation_id is None:
        origin.pop("copy_operation_id")
    else:
        origin["copy_operation_id"] = operation_id
    with pytest.raises(ValueError):
        _verify(copied, origin)


@pytest.mark.parametrize(
    "fault", ["manual_verified", "project_board_only", "wrong_project", "wrong_board"]
)
def test_matching_project_and_board_alone_do_not_prove_full_copy_origin(copied, fault):
    origin = copy.deepcopy(copied["origin"])
    project, board = copied["target"], copied["board"]
    if fault == "manual_verified":
        origin["verified"] = True
    elif fault == "project_board_only":
        origin = {
            key: origin[key]
            for key in (
                "copy_operation_id",
                "origin_project",
                "origin_board",
                "target_project",
                "target_board",
            )
        }
    elif fault == "wrong_project":
        project = project.parent.parent / "another/Board.prj"
    else:
        board = board.with_name("Another.pcb")
    with pytest.raises(ValueError):
        O.verify_origin(origin, str(project), str(board))


@pytest.mark.parametrize("fault", ["wrong_command", "failed", "unknown", "wrong_scope"])
def test_origin_requires_successful_matching_sdk_journal_record(copied, fault):
    if fault == "wrong_command":
        _rewrite_record(copied, command="pcb create")
    elif fault == "wrong_scope":
        _rewrite_record(copied, scope_id="different-scope")
    else:
        _rewrite_record(copied, status=fault)
    with pytest.raises(ValueError):
        _verify(copied)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_proof",
        "null_proof",
        "null_outcome",
        "null_producer",
        "incomplete_proof",
        "invalid_proof",
        "project_board_proof_only",
        "producer_manifest",
        "native_count",
        "boolean_native_count",
        "string_native_count",
        "opened",
        "native_open_verified",
        "unverified_outcome",
        "wrong_receipt_id",
        "wrong_target",
        "wrong_method",
    ],
)
def test_origin_refuses_incomplete_or_inconsistent_success_receipt(copied, fault):
    def corrupt(receipt):
        producer = receipt["extensions"]["result"]
        proof = receipt["extensions"]["file_readback"]
        if fault == "missing_proof":
            receipt["extensions"].pop("file_readback")
        elif fault == "null_proof":
            receipt["extensions"]["file_readback"] = None
        elif fault == "null_producer":
            receipt["extensions"]["result"] = None
        elif fault == "null_outcome":
            receipt["outcome"] = None
        elif fault in {"incomplete_proof", "invalid_proof"}:
            proof["complete" if fault == "incomplete_proof" else "valid"] = False
        elif fault == "project_board_proof_only":
            proof["actual_target_manifest"] = {
                key: value
                for key, value in proof["actual_target_manifest"].items()
                if key in {"Board.prj", "PCB/Board.pcb"}
            }
        elif fault == "producer_manifest":
            producer["closed_copy"]["actual_target_manifest"].pop("database/icdb.dat")
        elif fault in {"native_count", "boolean_native_count", "string_native_count"}:
            producer["native_calls_dispatched"] = {
                "native_count": 1,
                "boolean_native_count": False,
                "string_native_count": "0",
            }[fault]
        elif fault in {"opened", "native_open_verified"}:
            producer[fault] = True
        elif fault == "unverified_outcome":
            receipt["outcome"]["verification"]["status"] = "incomplete"
        elif fault == "wrong_receipt_id":
            receipt["operation_id"] = "op_11111111"
        elif fault == "wrong_target":
            receipt["target"]["object_id"] = str(copied["target"].with_name("Another.prj"))
        else:
            producer["creation_method"] = "native_new_project"

    _rewrite_record(copied, corrupt)
    with pytest.raises(ValueError):
        _verify(copied)


@pytest.mark.parametrize("fault", ["source_library", "source_database", "source_extra", "archive"])
def test_origin_rederives_complete_source_and_archive_after_success(copied, fault):
    if fault == "archive":
        with copied["archive"].open("ab") as stream:
            stream.write(b"changed after the public copy")
    elif fault == "source_extra":
        (copied["source"].parent / "extra.txt").write_bytes(b"unrecorded")
    else:
        relative = "Library/Library.lmc" if fault == "source_library" else "database/icdb.dat"
        (copied["source"].parent / relative).write_bytes(b"changed after copy")
    # PRJ and PCB pins still match; full-source/archive provenance does not.
    assert copied["source"].read_bytes() == copied["files"]["Board.prj"]
    assert (copied["source"].parent / "PCB/Board.pcb").read_bytes() == copied["files"][
        "PCB/Board.pcb"
    ]
    with pytest.raises(ValueError):
        _verify(copied)


@pytest.mark.parametrize("fault", ["source_library", "source_extra", "archive", "target"])
def test_public_copy_refuses_drift_between_sdk_preview_and_confirm(
    tmp_path, monkeypatch, file_only, fault
):
    _scope(tmp_path, monkeypatch)
    source, archive, target, _ = source_fixture(tmp_path)
    args = _args(source, archive, target)
    with runtime() as cli:
        preview = cli.run([*args, "--dry-run"])
    assert preview.exit_code == 0, preview.envelope
    if fault == "archive":
        with archive.open("ab") as stream:
            stream.write(b"changed after preview")
    elif fault == "target":
        target.parent.mkdir()
        (target.parent / "occupied.txt").write_bytes(b"retain")
    else:
        relative = "Library/Library.lmc" if fault == "source_library" else "extra.txt"
        (source.parent / relative).write_bytes(b"changed after preview")
    with runtime() as cli:
        result = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert result.exit_code != 0 and result.envelope["ok"] is False
    assert not target.exists() and file_only == []
    if fault == "target":
        assert (target.parent / "occupied.txt").read_bytes() == b"retain"
    else:
        assert not target.parent.exists()


@pytest.mark.parametrize("fault", ["target_library", "target_extra", "source_library", "archive"])
def test_sdk_independent_readback_detects_drift_after_producer_return(
    tmp_path, monkeypatch, file_only, fault
):
    _scope(tmp_path, monkeypatch)
    source, archive, target, _ = source_fixture(tmp_path)
    original = project_cli.create

    def changing_producer(options):
        result = original(options)
        if options.get("dry_run"):
            return result
        assert result["file_copy_verified"] is True
        assert result["closed_copy"]["target_verification"]["complete"] is True
        if fault == "archive":
            with archive.open("ab") as stream:
                stream.write(b"after producer verification")
        elif fault == "source_library":
            (source.parent / "Library/Library.lmc").write_bytes(b"changed source")
        else:
            relative = "Library/Library.lmc" if fault == "target_library" else "extra.txt"
            (target.parent / relative).write_bytes(b"changed target")
        return result

    monkeypatch.setattr(project_cli, "create", changing_producer)
    args = _args(source, archive, target)
    with runtime() as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.exit_code == 0, preview.envelope
        original_validate, violations = cli.validator.validate, []

        def checked(name, value, **kwargs):
            try:
                return original_validate(name, value, **kwargs)
            except ValueError as error:
                violations.extend(getattr(error, "errors", [str(error)]))
                raise

        monkeypatch.setattr(cli.validator, "validate", checked)
        result = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert result.exit_code != 0 and result.envelope["ok"] is False
    assert "receipt" in result.envelope["error"]["details"], (
        json.dumps(result.envelope, indent=2),
        violations,
    )
    receipt = result.envelope["error"]["details"]["receipt"]
    assert receipt["extensions"]["result"]["file_copy_verified"] is True
    proof = receipt["extensions"]["file_readback"]
    assert proof["valid"] is False and proof["complete"] is False
    assert receipt["outcome"]["verification"]["status"] != "passed"
    assert target.exists() and file_only == []
    origin = copy.deepcopy(receipt["extensions"]["result"]["closed_copy"]["registration"]["data"])
    origin["copy_operation_id"] = receipt["operation_id"]
    with pytest.raises(ValueError):
        O.verify_origin(origin, str(target), str(target.parent / "PCB/Board.pcb"))


@pytest.mark.parametrize("fault", ["stock", "external"])
def test_sdk_refuses_stock_or_external_library_source_without_native_fallback(
    tmp_path, monkeypatch, file_only, fault
):
    _scope(tmp_path, monkeypatch)
    source, archive, target, _ = source_fixture(tmp_path, external=fault == "external")
    if fault == "stock":
        (source.parent / "database/icdb.dat").unlink()
        archive.unlink()
        B.create(source, archive)
    with runtime() as cli:
        result = cli.run([*_args(source, archive, target), "--dry-run"])
    assert result.exit_code != 0 and result.envelope["ok"] is False
    assert not target.parent.exists() and file_only == []


@pytest.mark.parametrize(
    "fault",
    ["missing", "not_sqlite", "no_table", "bad_json", "json_null", "json_list", "missing_fields"],
)
def test_origin_loader_fails_closed_on_unavailable_or_malformed_owned_journal(
    tmp_path, monkeypatch, file_only, fault
):
    # Loader error cases alone may use a minimal SQLite schema. Success cases
    # above always require a real SDK-created record and complete readback.
    owned_config = tmp_path / "owned-config"
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(owned_config))
    path = _journal()
    operation_id = "op_0123456789abcdef"
    if fault != "missing":
        path.parent.mkdir()
        if fault == "not_sqlite":
            path.write_bytes(b"this is not a SQLite database")
        else:
            with sqlite3.connect(path) as connection:
                if fault != "no_table":
                    connection.execute(
                        "CREATE TABLE operations (operation_id TEXT, command TEXT, status TEXT, "
                        "scope_id TEXT, receipt TEXT)"
                    )
                    payload = {
                        "bad_json": "{",
                        "json_null": "null",
                        "json_list": "[]",
                        "missing_fields": "{}",
                    }[fault]
                    connection.execute(
                        "INSERT INTO operations VALUES (?,?,?,?,?)",
                        (operation_id, "project create", "succeeded", "owned-scope", payload),
                    )
    with pytest.raises(ValueError):
        O.verify_origin(
            {"copy_operation_id": operation_id},
            str(tmp_path / "Board.prj"),
            str(tmp_path / "Board.pcb"),
        )
    assert file_only == []


def test_origin_authenticates_genesis_without_claiming_live_native_database_bytes(copied):
    (copied["target"].parent / "database/icdb.dat").write_bytes(b"later native save bytes")
    authenticated = _verify(copied)
    assert authenticated["genesis"] == copied["origin"]["genesis"]
    assert I.verify_copy(copied["receipt"]["extensions"]["result"])["valid"] is False
