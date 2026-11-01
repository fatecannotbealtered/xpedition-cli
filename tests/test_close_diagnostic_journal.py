"""Diagnostic source/history checks against a real, isolated SDK SQLite store."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from xpedition_cli import close_diagnostic as diagnostic
from xpedition_cli import pcb_document_guard as guard
from xpedition_cli import project_isolated_copy
from xpedition_cli._vendor.tool_driver_sdk.store import StateStore
from xpedition_cli.errors import CLIError


def _project(path: Path, board: Path):
    path.write_text(
        'SECTION iCDB\nLIST Designs\nVALUE "Main"\nENDLIST\nENDSECTION\n'
        'SECTION Main\nKEY ConfigType "PCB"\nKEY RootBlock "Root"\n'
        f'KEY PCBDesignPath "{board}"\nENDSECTION\n',
        encoding="utf-8",
    )


@pytest.fixture
def journal_case(tmp_path, monkeypatch):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "ownership"))
    project, alternate, board = (
        tmp_path / "original.prj",
        tmp_path / "alternate.prj",
        tmp_path / "Board.pcb",
    )
    board.write_bytes(b"synthetic closed-copy board")
    _project(project, board)
    _project(alternate, board)
    genesis = {
        "project_sha256": hashlib.sha256(project.read_bytes()).hexdigest(),
        "board_sha256": hashlib.sha256(board.read_bytes()).hexdigest(),
        "file_manifest": [],
    }
    registered = {
        "target_board": {"path": str(board)},
        "origin_project": {"path": str(tmp_path / "source.prj")},
        "source_backup": {"path": str(tmp_path / "source.zip")},
    }
    derived = {"genesis": genesis, "registration": {"data": registered}}
    monkeypatch.setattr(project_isolated_copy, "derive_copy", lambda *args: copy.deepcopy(derived))
    copy_id = "op-abcdefgh12345678"
    receipt = {
        "operation_id": copy_id,
        "authorization_scope_id": "scope-test",
        "target": {"object_id": str(project)},
        "outcome": {
            "execution": "completed",
            "persistence": "saved",
            "verification": {"status": "passed"},
        },
        "extensions": {
            "result": {
                "creation_method": "closed_backup_copy",
                "opened": False,
                "native_open_verified": False,
                "native_calls_dispatched": 0,
                "closed_copy": {"registration": {"data": registered}, "actual_target_manifest": []},
            },
            "file_readback": {"valid": True, "complete": True, "actual_target_manifest": []},
        },
    }
    store = StateStore(tmp_path / "custom-sdk-journal")
    guard.ensure_qualification_store(store, can_initialize=True)
    plan = {
        "target": {"object_id": str(project), "extensions": {"command": "project create"}},
        "effects": ["session", "persist"],
    }
    store.add_plan(
        {
            "operation_id": copy_id,
            "plan_id": "plan-copy",
            "capability_id": "copy",
            "command": "project create",
            "target_key": "file:copy",
            "request_digest": "copy",
            "scope_id": "scope-test",
            "effects": ["session", "persist"],
            "plan": plan,
            "plan_expires_at": 9999999999,
        }
    )
    store.dispatch(
        copy_id,
        fingerprint="fingerprint-copy",
        token_expires=9999999999,
        key=None,
        digest="copy",
        target_keys=["file:copy"],
        lease=60,
    )
    store.finish(copy_id, "succeeded", receipt)
    guard.register_copy_qualification(store, receipt)
    guard.mark_dispatched_copy_use(
        store,
        {"command": "session start", "operation_id": "op-startup"},
        {"project": str(project)},
    )
    origin = {**registered, "copy_operation_id": copy_id}
    yield store, origin, project, alternate, board
    store.close()


def _fresh(case):
    store, origin, project, _, board = case
    return diagnostic.fresh_copy(
        origin,
        str(project),
        str(board),
        current_operation="op-diagnostic",
        startup_operation="op-startup",
        store=store,
        journal=guard.journal_identity(store),
    )


def _history(case, *, status, target):
    store, _, _, _, _ = case
    plan = {
        "target": {"object_id": str(target), "extensions": {"project": str(target)}},
        "effects": ["change", "persist"],
    }
    store.connection().execute(
        "INSERT INTO operations (operation_id,capability_id,command,status,effects,plan,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            "op-history",
            "move",
            "pcb placement move",
            status,
            '["change","persist"]',
            json.dumps(plan),
            2,
            2,
        ),
    )


def test_custom_sdk_journal_is_source_for_copy_and_history(journal_case, tmp_path):
    store, _, _, _, _ = journal_case
    assert not (tmp_path / "ownership" / "driver-v2" / "operations.sqlite3").exists()
    proof = _fresh(journal_case)
    assert proof["operation_id"] == "op-abcdefgh12345678"
    assert store.get(proof["operation_id"])["status"] == "succeeded"


@pytest.mark.parametrize("status", ["partial", "unknown", "failed", "dispatched"])
@pytest.mark.parametrize("alias", ["alternate_project", "board"])
def test_nonplanned_history_of_same_board_rejects_genesis(journal_case, status, alias):
    store, _, _, alternate, board = journal_case
    original_bytes = board.read_bytes()
    _history(
        journal_case, status=status, target=alternate if alias == "alternate_project" else board
    )
    before = copy.deepcopy(store.get("op-history"))
    with pytest.raises(ValueError, match="prior operation"):
        _fresh(journal_case)
    assert store.get("op-history") == before
    assert board.read_bytes() == original_bytes


def test_planned_history_does_not_pollute_copy(journal_case):
    _, _, _, alternate, _ = journal_case
    _history(journal_case, status="planned", target=alternate)
    assert _fresh(journal_case)["operation_id"] == "op-abcdefgh12345678"


def test_many_plans_do_not_exhaust_nonplanned_history_limit(journal_case):
    store, _, _, alternate, _ = journal_case
    plan = json.dumps(
        {
            "target": {"object_id": str(alternate), "extensions": {"project": str(alternate)}},
            "effects": ["change", "persist"],
        }
    )
    store.connection().executemany(
        "INSERT INTO operations (operation_id,capability_id,command,status,effects,plan,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        [
            (f"op-plan-{index}", "move", "pcb placement move", "planned", "[]", plan, 2, 2)
            for index in range(diagnostic.MAX_HISTORY + 1)
        ],
    )
    assert _fresh(journal_case)["operation_id"] == "op-abcdefgh12345678"
    store.connection().execute(
        "UPDATE operations SET status='partial' WHERE operation_id='op-plan-0'"
    )
    with pytest.raises(ValueError, match="prior operation"):
        _fresh(journal_case)


@pytest.mark.skipif(
    os.environ.get("XPEDITION_TEST_SKIP_HARDLINK") == "1",
    reason="Windows denied synthetic hard-link creation in this execution environment",
)
def test_hardlinked_board_alias_is_related(journal_case, tmp_path):
    store, _, _, _, board = journal_case
    link = tmp_path / "alias.pcb"
    os.link(board, link)
    _history(journal_case, status="partial", target=link)
    before = copy.deepcopy(store.get("op-history"))
    with pytest.raises(ValueError, match="prior operation"):
        _fresh(journal_case)
    assert store.get("op-history") == before


def test_mismatched_journal_identity_refuses_before_copy_lookup(
    journal_case, tmp_path, monkeypatch
):
    store, origin, project, _, board = journal_case
    expected = guard.journal_identity(store)
    wrong = StateStore(tmp_path / "other-sdk-journal")
    wrong.connection()
    monkeypatch.setattr(
        wrong, "get", lambda *args: pytest.fail("mismatched journal must be refused before lookup")
    )
    with pytest.raises(ValueError, match="journal identity changed"):
        diagnostic.fresh_copy(
            origin,
            str(project),
            str(board),
            current_operation="op-diagnostic",
            startup_operation="op-startup",
            store=wrong,
            journal=expected,
        )
    wrong.close()


def test_history_project_read_budget_fails_closed(journal_case, monkeypatch):
    store, _, _, alternate, _ = journal_case
    _history(journal_case, status="partial", target=alternate)
    before = copy.deepcopy(store.get("op-history"))
    monkeypatch.setattr(diagnostic, "MAX_HISTORY_PROJECT_BYTES", 1)
    with pytest.raises(ValueError, match="project read budget"):
        _fresh(journal_case)
    assert store.get("op-history") == before


def test_unclassifiable_pcb_history_fails_closed(journal_case):
    store, _, _, _, _ = journal_case
    plan = {"target": {"object_id": "not-a-board", "extensions": {}}, "effects": ["persist"]}
    store.connection().execute(
        "INSERT INTO operations (operation_id,capability_id,command,status,effects,plan,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            "op-unclassified",
            "move",
            "pcb placement move",
            "unknown",
            '["persist"]',
            json.dumps(plan),
            2,
            2,
        ),
    )
    before = copy.deepcopy(store.get("op-unclassified"))
    with pytest.raises(ValueError, match="relationship is unavailable"):
        _fresh(journal_case)
    assert store.get("op-unclassified") == before


def test_unrelated_schematic_session_does_not_pollute_copy(journal_case):
    store, _, _, _, _ = journal_case
    plan = {
        "target": {"object_id": "schematic", "extensions": {"domain": "schematic"}},
        "effects": ["session"],
    }
    store.connection().execute(
        "INSERT INTO operations (operation_id,capability_id,command,status,effects,plan,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            "op-schematic",
            "start",
            "session start",
            "succeeded",
            '["session"]',
            json.dumps(plan),
            2,
            2,
        ),
    )
    before = copy.deepcopy(store.get("op-schematic"))
    assert _fresh(journal_case)["operation_id"] == "op-abcdefgh12345678"
    assert store.get("op-schematic") == before


def test_history_path_count_is_bounded_before_file_reads(journal_case, tmp_path):
    store, _, _, _, _ = journal_case
    paths = [str(tmp_path / f"other-{index}.prj") for index in range(129)]
    plan = {
        "target": {"object_id": paths[0], "extensions": {"input_paths": paths}},
        "effects": ["persist"],
    }
    store.connection().execute(
        "INSERT INTO operations (operation_id,capability_id,command,status,effects,plan,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            "op-many",
            "write",
            "pcb placement move",
            "unknown",
            '["persist"]',
            json.dumps(plan),
            2,
            2,
        ),
    )
    with pytest.raises(ValueError, match="path count exceeded"):
        _fresh(journal_case)


def test_history_header_and_target_command_conflict_refuses(journal_case):
    store, _, _, _, board = journal_case
    plan = {
        "target": {
            "object_id": str(board),
            "extensions": {"command": "pcb placement move", "project": str(board)},
        },
        "effects": ["persist"],
    }
    store.connection().execute(
        "INSERT INTO operations (operation_id,capability_id,command,status,effects,plan,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("op-conflict", "write", "other action", "failed", '["persist"]', json.dumps(plan), 2, 2),
    )
    with pytest.raises(ValueError, match="command disagrees"):
        _fresh(journal_case)


@pytest.mark.parametrize("pruned", [False, True])
def test_unbounded_history_header_fails_before_classification(journal_case, pruned):
    store, _, _, alternate, _ = journal_case
    _history(journal_case, status="partial", target=alternate)
    store.connection().execute(
        "UPDATE operations SET command=?,updated_at=0 WHERE operation_id='op-history'",
        ("x" * 513,),
    )
    if pruned:
        guard.mark_dispatched_copy_use(
            store,
            {"command": "pcb placement move", "operation_id": "op-history"},
            {"project": str(alternate)},
        )
        store.prune()
        assert store.get("op-history") is None
        with pytest.raises(CLIError) as refused:
            _fresh(journal_case)
        assert refused.value.details["pcb_document_guard"]["reason"] == (
            "copy_qualification_already_used"
        )
    else:
        with pytest.raises(ValueError, match="header is invalid or unbounded"):
            _fresh(journal_case)


def test_copy_receipt_read_is_bounded_before_decode(journal_case, monkeypatch):
    store, _, _, _, _ = journal_case
    before = copy.deepcopy(store.get("op-abcdefgh12345678"))
    monkeypatch.setattr(diagnostic, "MAX_HISTORY_BYTES", 8)
    with pytest.raises(ValueError, match="receipt read budget"):
        _fresh(journal_case)
    assert store.get("op-abcdefgh12345678") == before


def test_history_inserted_after_copy_is_seen_when_wall_clock_moves_back(journal_case):
    store, _, _, alternate, _ = journal_case
    _history(journal_case, status="partial", target=alternate)
    store.connection().execute(
        "UPDATE operations SET created_at=-100 WHERE operation_id='op-history'"
    )
    with pytest.raises(ValueError, match="prior operation"):
        _fresh(journal_case)
    assert store.get("op-history")["status"] == "partial"


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_pruned_related_write_keeps_compact_used_marker(journal_case, status):
    store, _, _, alternate, board = journal_case
    _history(journal_case, status=status, target=alternate)
    guard.mark_dispatched_copy_use(
        store,
        {"command": "pcb placement move", "operation_id": "op-history"},
        {"project": str(alternate)},
    )
    board_before = board.read_bytes()
    store.connection().execute("UPDATE operations SET updated_at=0 WHERE operation_id='op-history'")
    store.prune()
    assert store.get("op-history") is None
    row = (
        store.connection()
        .execute(
            "SELECT used_by FROM pcb_close_qualification_v1 WHERE copy_operation_id=?",
            ("op-abcdefgh12345678",),
        )
        .fetchone()
    )
    assert row[0] == "op-history"
    assert (
        store.connection()
        .execute("SELECT COUNT(*) FROM sqlite_master WHERE name='pcb_close_pruned_history'")
        .fetchone()[0]
        == 0
    )
    store.close()
    store.connection()
    with pytest.raises(CLIError) as refused:
        _fresh(journal_case)
    assert (
        refused.value.details["pcb_document_guard"]["reason"] == "copy_qualification_already_used"
    )
    assert board.read_bytes() == board_before


def test_pruned_planned_operation_does_not_pollute_copy(journal_case):
    store, _, _, alternate, _ = journal_case
    _history(journal_case, status="planned", target=alternate)
    store.connection().execute(
        "UPDATE operations SET plan_expires_at=0 WHERE operation_id='op-history'"
    )
    store.prune()
    assert store.get("op-history") is None
    assert _fresh(journal_case)["operation_id"] == "op-abcdefgh12345678"


def test_missing_qualification_store_refuses(journal_case):
    store, _, _, _, _ = journal_case
    store.connection().execute("DROP TABLE pcb_close_qualification_v1")
    with pytest.raises(CLIError) as refused:
        _fresh(journal_case)
    assert refused.value.details["pcb_document_guard"]["reason"] == (
        "qualification_store_missing_or_changed"
    )


def test_copy_without_qualification_is_not_admitted(journal_case):
    store, _, _, _, _ = journal_case
    store.connection().execute("DELETE FROM pcb_close_qualification_v1")
    with pytest.raises(CLIError) as refused:
        _fresh(journal_case)
    assert refused.value.details["pcb_document_guard"]["reason"] == "copy_qualification_unavailable"


def test_large_receipt_does_not_block_sdk_prune_or_token_cleanup(journal_case):
    store, _, _, alternate, _ = journal_case
    _history(journal_case, status="partial", target=alternate)
    guard.mark_dispatched_copy_use(
        store,
        {"command": "pcb placement move", "operation_id": "op-history"},
        {"project": str(alternate)},
    )
    raw = json.dumps({"synthetic": "x" * (8 * 1024 * 1024 + 1)})
    store.connection().execute(
        "UPDATE operations SET updated_at=0,receipt=? WHERE operation_id='op-history'", (raw,)
    )
    store.connection().execute(
        "INSERT INTO consumed_tokens VALUES ('expired-synthetic','op-old',0,0)"
    )
    store.prune()
    assert store.get("op-history") is None
    assert (
        store.connection()
        .execute("SELECT COUNT(*) FROM consumed_tokens WHERE fingerprint='expired-synthetic'")
        .fetchone()[0]
        == 0
    )
    with pytest.raises(CLIError) as refused:
        _fresh(journal_case)
    assert (
        refused.value.details["pcb_document_guard"]["reason"] == "copy_qualification_already_used"
    )


def test_unresolved_unknown_is_never_pruned(journal_case):
    store, _, _, alternate, _ = journal_case
    _history(journal_case, status="unknown", target=alternate)
    guard.mark_dispatched_copy_use(
        store,
        {"command": "pcb placement move", "operation_id": "op-history"},
        {"project": str(alternate)},
    )
    store.connection().execute("UPDATE operations SET updated_at=0 WHERE operation_id='op-history'")
    before = copy.deepcopy(store.get("op-history"))
    store.prune()
    assert store.get("op-history") == before
    with pytest.raises(CLIError) as refused:
        _fresh(journal_case)
    assert (
        refused.value.details["pcb_document_guard"]["reason"] == "copy_qualification_already_used"
    )


def test_plan_inserted_before_copy_but_dispatched_after_is_never_fresh(journal_case):
    store, _, _, alternate, _ = journal_case
    _history(journal_case, status="planned", target=alternate)
    # Model an SDK plan inserted before the copy's operation row. Dispatch,
    # rather than insertion order, is when this copy becomes ineligible.
    store.connection().execute("UPDATE operations SET rowid=-1 WHERE operation_id='op-history'")
    assert _fresh(journal_case)["operation_id"] == "op-abcdefgh12345678"
    record = store.get("op-history")
    store.dispatch(
        "op-history",
        fingerprint="early-plan",
        token_expires=9999999999,
        key=None,
        digest=record["request_digest"],
        target_keys=[record["target_key"]],
        lease=60,
    )
    guard.mark_dispatched_copy_use(store, record, {"project": str(alternate)})
    store.finish("op-history", "partial", None)
    store.connection().execute("UPDATE operations SET updated_at=0 WHERE operation_id='op-history'")
    store.prune()
    assert store.get("op-history") is None
    with pytest.raises(CLIError) as refused:
        _fresh(journal_case)
    assert (
        refused.value.details["pcb_document_guard"]["reason"] == "copy_qualification_already_used"
    )


def test_unrelated_board_write_does_not_invalidate_copy(journal_case, tmp_path):
    store, _, _, _, _ = journal_case
    other = tmp_path / "unrelated.pcb"
    other.write_bytes(b"different synthetic board")
    guard.mark_dispatched_copy_use(
        store,
        {"command": "pcb placement move", "operation_id": "op-other"},
        {"project": str(other)},
    )
    assert _fresh(journal_case)["operation_id"] == "op-abcdefgh12345678"


def test_capacity_rejects_only_new_diagnostic_qualification(journal_case, monkeypatch, tmp_path):
    store, _, _, _, _ = journal_case
    monkeypatch.setattr(guard, "MAX_COPY_QUALIFICATIONS", 1)
    original = store.get("op-abcdefgh12345678")
    second_id = "op-secondcopy123456"
    receipt = copy.deepcopy(original["receipt"])
    receipt["operation_id"] = second_id
    other_board = tmp_path / "other.pcb"
    other_board.write_bytes(b"unrelated copy")
    other_project = tmp_path / "other.prj"
    _project(other_project, other_board)
    receipt["target"]["object_id"] = str(other_project)
    receipt["extensions"]["result"]["closed_copy"]["registration"]["data"]["target_board"][
        "path"
    ] = str(other_board)
    second_plan = copy.deepcopy(original["plan"])
    second_plan["target"]["object_id"] = str(other_project)
    store.add_plan(
        {
            "operation_id": second_id,
            "plan_id": "plan-second",
            "capability_id": "copy",
            "command": "project create",
            "target_key": "file:second",
            "request_digest": "second",
            "scope_id": "scope-test",
            "effects": ["session", "persist"],
            "plan": second_plan,
            "plan_expires_at": 9999999999,
        }
    )
    store.dispatch(
        second_id,
        fingerprint="second",
        token_expires=9999999999,
        key=None,
        digest="second",
        target_keys=["file:second"],
        lease=60,
    )
    store.finish(second_id, "succeeded", receipt)
    with pytest.raises(CLIError) as refused:
        guard.register_copy_qualification(store, receipt)
    assert refused.value.details["pcb_document_guard"]["reason"] == "qualification_capacity_reached"
    assert store.get(second_id)["status"] == "succeeded"
    assert (
        store.connection().execute("SELECT COUNT(*) FROM pcb_close_qualification_v1").fetchone()[0]
        == 1
    )
    assert _fresh(journal_case)["operation_id"] == "op-abcdefgh12345678"
