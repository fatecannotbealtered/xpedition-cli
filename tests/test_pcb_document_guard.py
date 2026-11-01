"""Physical-board conflicts, preserving exact original SDK targets and history."""

from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path

import pytest
from test_session_ownership import started as _fixture_started  # noqa: F401 - pytest fixture
from test_session_start_generation import startup  # noqa: F401 - dependency of started

from xpedition_cli import driver
from xpedition_cli import pcb_document_guard as G
from xpedition_cli.backends import NativeBackend
from xpedition_cli.cli import sessions as S
from xpedition_cli.errors import CLIError


@pytest.fixture(name="started")
def _started(request):
    return request.getfixturevalue("_fixture_started")


@pytest.fixture(autouse=True)
def _hard_no_native_backend(monkeypatch):
    hits = []

    def refuse(*args, **kwargs):
        hits.append(True)
        pytest.fail("This regression suite must never invoke an actual native backend")

    monkeypatch.setattr(NativeBackend, "invoke", refuse)
    monkeypatch.setattr(S, "_stop_domain", lambda options: "pcb")
    yield
    assert hits == [], "Default native-backend calls are forbidden even if the CLI catches errors"


def _project(path, board):
    path.write_text(
        'SECTION iCDB\nLIST Designs\nVALUE "Main"\nENDLIST\nENDSECTION\n'
        'SECTION Main\nKEY ConfigType "PCB"\nKEY RootBlock "Root"\n'
        f'KEY PCBDesignPath "{board}"\nENDSECTION\n',
        encoding="utf-8",
    )
    return path


def _unknown(case, monkeypatch, *, board=None, old_pid=None, foreign_scope=False, activate=True):
    board = board or case.startup.board
    project = _project(board.parent / ("Legacy" + board.stem + ".prj"), board)
    backend = case.startup.backend
    original_invoke, original_handler = backend.invoke, driver._handler
    scope_path = Path(os.environ["XPEDITION_CLI_SCOPE_FILE"])
    scope_bytes = scope_path.read_bytes()

    def invoke(method, params, **kwargs):
        params = copy.deepcopy(params)
        if method == "snapshot":
            params["pid"] = 22220
        result = original_invoke(method, params, **kwargs)
        if method == "identity":
            result["application"]["pid"] = old_pid or case.actual["pid"]
            result["application"]["document"]["path"] = str(board)
            result["application"]["document"]["open"] = True
        if method == "snapshot":
            result["project"] = str(board)
        return result

    def handler(command):
        if command.path == "pcb placement move":
            return lambda options: {"confirm_token": "synthetic-observed-move", "changes": []}
        return original_handler(command)

    monkeypatch.setattr(backend, "invoke", invoke)
    monkeypatch.setattr(driver, "_handler", handler)
    if foreign_scope:
        value = json.loads(scope_bytes)
        value["scope_id"] = "another-original-task-scope"
        scope_path.write_text(json.dumps(value))
    try:
        preview = case.cli.run(
            [
                "pcb",
                "placement",
                "move",
                "--project",
                str(project),
                "--refdes",
                "R1",
                "--to",
                "1,2",
                "--dry-run",
            ]
        )
        assert preview.envelope["ok"], preview.envelope
    finally:
        scope_path.write_bytes(scope_bytes)
        monkeypatch.setattr(backend, "invoke", original_invoke)
        monkeypatch.setattr(driver, "_handler", original_handler)
    operation_id = preview.envelope["data"]["preview"]["operation_id"]
    store = case.cli._store_open()
    if activate:
        _activate_unknown(case, operation_id)
    return operation_id, copy.deepcopy(store.get(operation_id))


def _activate_unknown(case, operation_id):
    store = case.cli._store_open()
    record = store.get(operation_id)
    store.dispatch(
        operation_id,
        fingerprint="synthetic-dispatched-" + operation_id,
        token_expires=time.time() + 60,
        key=None,
        digest=record["request_digest"],
        target_keys=[record["target_key"]],
        lease=60,
    )
    store.finish(operation_id, "unknown")


def _close(case):
    return [
        "session",
        "close",
        "--kind",
        "pcb",
        "--pid",
        str(case.actual["pid"]),
        "--save-project",
        str(case.startup.board),
    ]


@pytest.mark.parametrize("new_generation", [False, True])
@pytest.mark.parametrize("foreign_scope", [False, True])
def test_public_exact_PCB_save_close_blocks_original_PRJ_unknown_across_PID_scope(
    started,
    monkeypatch,
    new_generation,
    foreign_scope,  # noqa: F811
):
    operation_id, before = _unknown(
        started,
        monkeypatch,
        old_pid=17171 if new_generation else None,
        foreign_scope=foreign_scope,
    )
    calls_before = len(started.startup.calls)
    result = started.cli.run([*_close(started), "--dry-run"])
    assert not result.envelope["ok"], result.envelope
    details = result.envelope["error"]["details"]
    assert "pcb_document_guard" in details, result.envelope
    assert details["pcb_document_guard"]["reason"] == "same_physical_pcb_unresolved"
    assert details["related_operation_id"] == operation_id
    assert details["dispatch_state"] == "not_started"
    assert not any(
        m in {"close", "save", "open", "start"} for m, _ in started.startup.calls[calls_before:]
    )
    assert started.cli._store_open().get(operation_id) == before
    assert before["plan"]["target"]["object_id"].endswith(".prj")
    assert Path(_close(started)[-1]).suffix == ".pcb"


def test_public_new_close_preview_then_related_unknown_refuses_original_confirm(
    started, monkeypatch
):
    args = _close(started)
    preview = started.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    assert "operation_effects" not in preview.envelope["data"]["preview"]["target"]["extensions"]
    operation_id, before = _unknown(started, monkeypatch, old_pid=17171)
    calls_before = len(started.startup.calls)
    refused = started.cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not refused.envelope["ok"], refused.envelope
    assert refused.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert not any(
        m in {"close", "save", "open", "start"} for m, _ in started.startup.calls[calls_before:]
    )
    assert started.cli._store_open().get(operation_id) == before


def test_complete_other_physical_board_does_not_block_current_board(started, monkeypatch, tmp_path):
    other = tmp_path / "other" / started.startup.board.name
    other.parent.mkdir()
    other.write_bytes(started.startup.board.read_bytes())
    operation_id, before = _unknown(started, monkeypatch, board=other, old_pid=17171)
    result = started.cli.run([*_close(started), "--dry-run"])
    assert result.envelope["ok"], result.envelope
    assert started.cli._store_open().get(operation_id) == before


def test_hardlinked_board_alias_is_same_physical_PCB(started, monkeypatch, tmp_path):
    other = tmp_path / "linked" / "DifferentName.pcb"
    other.parent.mkdir()
    os.link(started.startup.board, other)
    operation_id, before = _unknown(started, monkeypatch, board=other, old_pid=17171)
    result = started.cli.run([*_close(started), "--dry-run"])
    assert not result.envelope["ok"]
    assert result.envelope["error"]["details"]["related_operation_id"] == operation_id
    assert started.cli._store_open().get(operation_id) == before


@pytest.mark.parametrize(
    "missing", ["native_document", "PRJ_relation", "authority", "effects_forged_observe"]
)
def test_incomplete_or_contradictory_related_evidence_refuses(started, monkeypatch, missing):
    operation_id, record = _unknown(started, monkeypatch)
    changed = copy.deepcopy(record)
    if missing == "native_document":
        del changed["plan"]["base_snapshot"]["extensions"]["facts"]["application"]["document"]
    elif missing == "PRJ_relation":
        Path(changed["plan"]["target"]["object_id"]).write_text("not a linked PCB project")
    elif missing == "authority":
        changed["scope_id"] = "different-from-original-plan"
    else:
        changed["effects"] = changed["plan"]["effects"] = ["observe"]
    monkeypatch.setattr(
        started.cli.adapter, "engineering_active_records", lambda _exclude=None: [changed]
    )
    result = started.cli.run([*_close(started), "--dry-run"])
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"
    if missing == "effects_forged_observe":
        assert (
            result.envelope["error"]["details"]["pcb_document_guard"]["reason"]
            == "active_engineering_authority_incomplete"
        )
    assert started.cli._store_open().get(operation_id) == record


def test_preservation_backup_and_empty_Quit_are_not_ordinary_engineering_actions(started):
    caps = started.cli.adapter.engineering_capabilities
    backup = {
        "mode": "file",
        "engine_id": "xpedition-cli-files",
        "extensions": {"command": "project backup"},
    }
    empty_quit = {
        "mode": "session",
        "engine_id": "xpedition-pcb",
        "extensions": {"command": "session close", "domain": "pcb", "project": None},
    }
    assert G.applies(backup, caps) is False
    assert G.applies(empty_quit, caps) is False


@pytest.mark.parametrize("limit", ["count", "single_bytes", "total_bytes"])
def test_active_evidence_limit_refuses_before_original_get_decodes_JSON(
    started, monkeypatch, limit
):
    _unknown(started, monkeypatch)
    if limit == "count":
        monkeypatch.setattr(G, "MAX_ACTIVE_RECORDS", 0)
    elif limit == "single_bytes":
        monkeypatch.setattr(G, "MAX_RECORD_BYTES", 16)
    else:
        monkeypatch.setattr(G, "MAX_ACTIVE_BYTES", 16)
    store = started.cli._store_open()
    monkeypatch.setattr(store, "get", lambda *args: pytest.fail("over-limit JSON must not decode"))
    with pytest.raises(CLIError):
        G.read_active_records(store)


def test_malformed_active_JSON_and_read_budget_fail_closed(started, monkeypatch):
    operation_id, _record = _unknown(started, monkeypatch)
    store = started.cli._store_open()
    store.connection().execute(
        "UPDATE operations SET plan=? WHERE operation_id=?", ("{", operation_id)
    )
    with pytest.raises(CLIError):
        G.read_active_records(store)
    monkeypatch.setattr(G, "MAX_SQL_INSTRUCTIONS", -1)
    with pytest.raises(CLIError):
        G.read_active_records(store)


def test_legacy_signed_target_missing_marker_still_requires_guard(started):
    target = {
        "mode": "session",
        "engine_id": "xpedition-pcb",
        "extensions": {
            "domain": "pcb",
            "command": "session close",
            "project": str(started.startup.board),
        },
    }
    assert "operation_effects" not in target["extensions"]
    assert G.applies(target, started.cli.adapter.engineering_capabilities) is True


def test_public_apply_rechecks_related_active_after_confirmation_revision(started, monkeypatch):
    old_id, _planned = _unknown(started, monkeypatch, old_pid=17171, activate=False)
    args = _close(started)
    preview = started.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    actual_apply = started.cli.adapter.apply

    def after_dispatch(call, plan):
        _activate_unknown(started, old_id)
        return actual_apply(call, plan)

    monkeypatch.setattr(started.cli.adapter, "apply", after_dispatch)
    calls_before = len(started.startup.calls)
    result = started.cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert result.envelope["error"]["details"]["related_operation_id"] == old_id
    assert not any(
        m in {"close", "save", "open", "start"} for m, _ in started.startup.calls[calls_before:]
    )
    assert started.cli._store_open().get(old_id)["status"] == "unknown"


def test_missing_octet_length_fails_unsupported_before_JSON_get(started, monkeypatch):
    _unknown(started, monkeypatch)
    store = started.cli._store_open()
    store.connection().create_function("octet_length", 1, None)
    monkeypatch.setattr(
        store, "get", lambda *args: pytest.fail("unsupported reader must not decode")
    )
    with pytest.raises(CLIError) as caught:
        G.read_active_records(store)
    assert caught.value.code == "E_UNSUPPORTED"
    assert (
        caught.value.details["pcb_document_guard"]["reason"]
        == "active_store_octet_length_unavailable"
    )
