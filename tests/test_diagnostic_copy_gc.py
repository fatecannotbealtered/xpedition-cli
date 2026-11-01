"""Exit-referenced closed-copy rows survive bounded qualification GC."""

from __future__ import annotations

import copy

import pytest

from xpedition_cli import diagnostic_exit as E
from xpedition_cli import pcb_document_guard as G
from xpedition_cli.errors import CLIError

pytest_plugins = ("test_close_diagnostic_journal", "test_diagnostic_exit")


def _new_copy(store, receipt, suffix):
    """Register a distinct successful fake SDK copy without native dispatch."""
    receipt = copy.deepcopy(receipt)
    operation_id = "op-new-copy-" + suffix
    receipt["operation_id"] = operation_id
    store.connection().execute(
        "INSERT INTO operations (operation_id,capability_id,command,status,effects,plan,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (operation_id, "copy", "project create", "succeeded", "[]", "{}", 2, 2),
    )
    G.register_copy_qualification(store, receipt)
    return operation_id


def _copy_row(store, copy_id):
    row = (
        store.connection()
        .execute("SELECT * FROM pcb_close_qualification_v1 WHERE copy_operation_id=?", (copy_id,))
        .fetchone()
    )
    return tuple(row) if row else None


@pytest.mark.parametrize("state", ["reserved", "sealed", "ready", "observing", "claimed"])
def test_each_live_exit_reference_protects_consumed_copy(journal_case, state):
    store, origin, *_ = journal_case
    copy_id = origin["copy_operation_id"]
    store.connection().execute(
        "UPDATE pcb_close_qualification_v1 SET used_by='op-diagnostic' WHERE copy_operation_id=?",
        (copy_id,),
    )
    E.ensure_table(store)
    store.connection().execute(
        f"INSERT INTO {E.TABLE} (diagnostic_id,state,scope_sha256,copy_operation_id) "
        "VALUES ('op-diagnostic',?,'scope',?)",
        (state, copy_id),
    )
    before = _copy_row(store, copy_id)
    _new_copy(store, store.get(copy_id)["receipt"], state)
    assert _copy_row(store, copy_id) == before


def test_unbound_reservation_protects_consumed_copy_conservatively(journal_case):
    store, origin, *_ = journal_case
    copy_id = origin["copy_operation_id"]
    store.connection().execute(
        "UPDATE pcb_close_qualification_v1 SET used_by='op-diagnostic' WHERE copy_operation_id=?",
        (copy_id,),
    )
    E.ensure_table(store)
    store.connection().execute(
        f"INSERT INTO {E.TABLE} (diagnostic_id,state,scope_sha256) "
        "VALUES ('op-diagnostic','reserved','scope')"
    )
    before = _copy_row(store, copy_id)
    _new_copy(store, store.get(copy_id)["receipt"], "unbound")
    assert _copy_row(store, copy_id) == before


def test_no_exit_table_does_not_disable_ordinary_copy_gc(journal_case):
    store, origin, *_ = journal_case
    copy_id = origin["copy_operation_id"]
    store.connection().execute(
        "UPDATE pcb_close_qualification_v1 SET used_by='op-diagnostic' WHERE copy_operation_id=?",
        (copy_id,),
    )
    new_id = _new_copy(store, store.get(copy_id)["receipt"], "without-exit")
    assert _copy_row(store, copy_id) is None
    assert _copy_row(store, new_id) is not None


def test_corrupt_exit_schema_never_evicts_consumed_copy(journal_case):
    store, origin, *_ = journal_case
    copy_id = origin["copy_operation_id"]
    store.connection().execute(
        "UPDATE pcb_close_qualification_v1 SET used_by='op-diagnostic' WHERE copy_operation_id=?",
        (copy_id,),
    )
    store.connection().execute(f"CREATE TABLE {E.TABLE} (diagnostic_id TEXT)")
    before = _copy_row(store, copy_id)
    with pytest.raises(CLIError):
        _new_copy(store, store.get(copy_id)["receipt"], "corrupt")
    assert _copy_row(store, copy_id) == before


def test_new_copy_preserves_ready_exit_and_success_releases_for_gc(
    completed_diagnostic, monkeypatch
):
    from test_diagnostic_exit import _exit_args, _install_exit_worker

    case, operation_id, original = completed_diagnostic
    store = case.cli._store_open()
    copy_id = case.origin["copy_operation_id"]
    copy_receipt = store.get(copy_id)["receipt"]
    before = _copy_row(store, copy_id)

    def original_bytes():
        return tuple(
            store.connection()
            .execute(
                "SELECT status,receipt,resolution,extra FROM operations WHERE operation_id=?",
                (operation_id,),
            )
            .fetchone()
        )

    immutable = original_bytes()
    _new_copy(store, copy_receipt, "while-ready")
    assert _copy_row(store, copy_id) == before
    assert original_bytes() == immutable
    _install_exit_worker(case, monkeypatch)
    args = _exit_args(operation_id, token=case.exit_qualification_token)
    preview = case.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    token = preview.envelope["data"]["confirm_token"]
    result = case.cli.run([*args, "--confirm", token])
    assert result.envelope["ok"], result.envelope
    assert case.events.count("Application.Quit") == 1
    assert original_bytes() == immutable
    assert store.get(operation_id) == original
    assert (
        store.connection()
        .execute(f"SELECT 1 FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()
        is None
    )
    _new_copy(store, copy_receipt, "after-release")
    assert _copy_row(store, copy_id) is None
    assert original_bytes() == immutable
    requests, events = list(case.requests), list(case.events)
    repeated = case.cli.run([*args, "--confirm", token])
    assert not repeated.envelope["ok"], repeated.envelope
    assert case.requests == requests and case.events == events
    with pytest.raises(CLIError):
        E.reserve(store, operation_id, case.cli._scope())
    assert original_bytes() == immutable


def test_references_do_not_protect_unrelated_consumed_rows(journal_case):
    store, origin, *_ = journal_case
    copy_id = origin["copy_operation_id"]
    receipt = store.get(copy_id)["receipt"]
    E.ensure_table(store)
    store.connection().execute(
        f"INSERT INTO {E.TABLE} (diagnostic_id,state,scope_sha256,copy_operation_id) "
        "VALUES ('op-diagnostic','ready','scope',?)",
        (copy_id,),
    )
    unrelated = _new_copy(store, receipt, "unrelated")
    store.connection().execute(
        "UPDATE pcb_close_qualification_v1 SET used_by='op-other-diagnostic' "
        "WHERE copy_operation_id=?",
        (unrelated,),
    )
    before = _copy_row(store, copy_id)
    _new_copy(store, receipt, "gc-unrelated")
    assert _copy_row(store, copy_id) == before
    assert _copy_row(store, unrelated) is None


def test_unbound_reservation_keeps_copy_capacity_bounded(journal_case, monkeypatch):
    store, origin, *_ = journal_case
    copy_id = origin["copy_operation_id"]
    E.ensure_table(store)
    store.connection().execute(
        f"INSERT INTO {E.TABLE} (diagnostic_id,state,scope_sha256) "
        "VALUES ('op-diagnostic','reserved','scope')"
    )
    monkeypatch.setattr(G, "MAX_COPY_QUALIFICATIONS", 1)
    before = _copy_row(store, copy_id)
    with pytest.raises(CLIError):
        _new_copy(store, store.get(copy_id)["receipt"], "capacity")
    assert _copy_row(store, copy_id) == before
    assert (
        store.connection().execute("SELECT COUNT(*) FROM pcb_close_qualification_v1").fetchone()[0]
        == 1
    )
