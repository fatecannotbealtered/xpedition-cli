"""Read-only native-shaped mask inventory; these fixtures never activate CAD."""

from types import SimpleNamespace as NS

import pytest

from xpedition_cli import native_com_adapter as A
from xpedition_cli import native_identity, native_process
from xpedition_cli.cli import pcb
from xpedition_cli.errors import CLIError


def application_identity(app, pid=42):
    doc = app.ActiveDocument
    return {
        "pid": pid,
        "document": {
            "open": doc is not None,
            "path": doc.FullName if doc is not None else None,
            "status": "observed",
        },
    }


def test_attached_mask_inventory_does_not_capture_allocate_or_delete(tmp_path, monkeypatch):
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic board")
    doc = NS(FullName=str(board))
    app, engine, collection = NS(ActiveDocument=doc), object(), NS(Count=3)
    process = {"pid": 42, "executable": "C:/vendor/ExpeditionPCB.exe", "creation_time_100ns": "12"}
    calls = []
    monkeypatch.setattr(A, "_layout_board_path", lambda _params: board)
    monkeypatch.setattr(A, "_application", lambda _client, **_kwargs: app)
    monkeypatch.setattr(A, "_licensed_document", lambda _app: doc)
    monkeypatch.setattr(
        native_identity, "application_identity", lambda app, _: application_identity(app)
    )
    monkeypatch.setattr(native_process, "process_identity", lambda *_: process)

    def get(obj, dispid):
        calls.append((obj, dispid))
        if obj is doc and dispid == 326:
            return engine
        if obj is engine and dispid == 0:
            return collection
        pytest.fail("Unexpected native getter")

    monkeypatch.setattr(A, "_propget", get)
    result = A._board_observation(
        {"project": str(board), "start": False, "pid": 42, "mask_inventory_only": True}, None
    )
    inventory = result["observation"]["temporary_masks"]
    assert calls == [(doc, 326), (engine, 0)]
    assert inventory["count_before"] == inventory["count_after"] == 3
    assert inventory["owner_identity_established"] is False
    assert inventory["cleanup_attempted"] is False
    assert result["observation"]["process_identity"] == process
    assert result["observation"]["application"]["document"]["path"] == str(board)
    assert result["observation"]["engineering_write_dispatched"] is False


@pytest.mark.parametrize("fault", ["changing_count", "invalid_count", "generation", "wrong_pid"])
def test_incomplete_mask_identity_or_count_never_becomes_success(tmp_path, monkeypatch, fault):
    board = tmp_path / "owned.pcb"
    doc = NS(FullName=str(board))
    app, engine = NS(ActiveDocument=doc), object()
    counts = iter(
        [1, 2]
        if fault == "changing_count"
        else [True, True]
        if fault == "invalid_count"
        else [0, 0]
    )

    class Collection:
        @property
        def Count(self):
            return next(counts)

    collection = Collection()
    process = {"pid": 42, "executable": "C:/vendor/ExpeditionPCB.exe", "creation_time_100ns": "12"}
    generations = iter(
        [process, {**process, "creation_time_100ns": "13"} if fault == "generation" else process]
    )
    monkeypatch.setattr(A, "_layout_board_path", lambda _params: board)
    monkeypatch.setattr(A, "_application", lambda *_args, **_kwargs: app)
    monkeypatch.setattr(A, "_licensed_document", lambda _app: doc)
    monkeypatch.setattr(
        native_identity,
        "application_identity",
        lambda app, _: application_identity(app, 99 if fault == "wrong_pid" else 42),
    )
    monkeypatch.setattr(native_process, "process_identity", lambda *_: next(generations))
    monkeypatch.setattr(A, "_propget", lambda obj, _dispid: engine if obj is doc else collection)
    with pytest.raises(A.AdapterError) as error:
        A._board_observation(
            {"project": str(board), "start": False, "pid": 42, "mask_inventory_only": True}, None
        )
    assert error.value.code in {"E_VERIFY_INCOMPLETE", "E_CONFLICT"}
    assert error.value.details["write_attempted"] is False


@pytest.mark.parametrize("switch_to", ["other_board", "no_document"])
def test_mask_inventory_refuses_same_process_active_board_switch(tmp_path, monkeypatch, switch_to):
    board = tmp_path / "owned.pcb"
    doc_a = NS(FullName=str(board))
    doc_b = NS(FullName=str(tmp_path / "other.pcb"))
    app, engine = NS(ActiveDocument=doc_a), object()
    process = {"pid": 42, "executable": "C:/vendor/ExpeditionPCB.exe", "creation_time_100ns": "12"}

    class Collection:
        reads = 0

        @property
        def Count(self):
            self.reads += 1
            if self.reads == 2:
                app.ActiveDocument = doc_b if switch_to == "other_board" else None
            return 3

    collection = Collection()
    monkeypatch.setattr(A, "_layout_board_path", lambda _params: board)
    monkeypatch.setattr(A, "_application", lambda *_args, **_kwargs: app)
    monkeypatch.setattr(A, "_licensed_document", lambda _app: doc_a)
    monkeypatch.setattr(
        native_identity, "application_identity", lambda app, _: application_identity(app)
    )
    monkeypatch.setattr(native_process, "process_identity", lambda *_: process)
    monkeypatch.setattr(A, "_propget", lambda obj, _dispid: engine if obj is doc_a else collection)
    with pytest.raises(A.AdapterError) as error:
        A._board_observation(
            {"project": str(board), "start": False, "pid": 42, "mask_inventory_only": True}, None
        )
    assert collection.reads == 2
    assert doc_a.FullName == str(board)  # The original COM-like proxy remains alive.
    assert error.value.code == "E_VERIFY_INCOMPLETE"
    assert error.value.details["write_attempted"] is False


def test_mask_inventory_refuses_stale_initial_document_before_reading_mask(tmp_path, monkeypatch):
    board = tmp_path / "owned.pcb"
    doc_a = NS(FullName=str(board))
    doc_b = NS(FullName=str(tmp_path / "other.pcb"))
    app = NS(ActiveDocument=doc_b)
    monkeypatch.setattr(A, "_layout_board_path", lambda _params: board)
    monkeypatch.setattr(A, "_application", lambda *_args, **_kwargs: app)
    monkeypatch.setattr(A, "_licensed_document", lambda _app: doc_a)
    monkeypatch.setattr(
        native_identity, "application_identity", lambda app, _: application_identity(app)
    )
    monkeypatch.setattr(A, "_propget", lambda *_: pytest.fail("must not read a stale mask engine"))
    with pytest.raises(A.AdapterError) as error:
        A._board_observation(
            {"project": str(board), "start": False, "pid": 42, "mask_inventory_only": True}, None
        )
    assert error.value.code == "E_CONFLICT"
    assert error.value.details["write_attempted"] is False


def test_mask_inventory_refuses_unobserved_final_active_document(tmp_path, monkeypatch):
    board = tmp_path / "owned.pcb"
    doc = NS(FullName=str(board))
    app, engine, collection = NS(ActiveDocument=doc), object(), NS(Count=0)
    identities = iter([application_identity(app), {"pid": 42, "document": {"status": "unknown"}}])
    process = {"pid": 42, "executable": "C:/vendor/ExpeditionPCB.exe", "creation_time_100ns": "12"}
    monkeypatch.setattr(A, "_layout_board_path", lambda _params: board)
    monkeypatch.setattr(A, "_application", lambda *_args, **_kwargs: app)
    monkeypatch.setattr(A, "_licensed_document", lambda _app: doc)
    monkeypatch.setattr(native_identity, "application_identity", lambda *_: next(identities))
    monkeypatch.setattr(native_process, "process_identity", lambda *_: process)
    monkeypatch.setattr(A, "_propget", lambda obj, _dispid: engine if obj is doc else collection)
    with pytest.raises(A.AdapterError) as error:
        A._board_observation(
            {"project": str(board), "start": False, "pid": 42, "mask_inventory_only": True}, None
        )
    assert error.value.code == "E_VERIFY_INCOMPLETE"
    assert error.value.details["write_attempted"] is False


def test_public_mask_inventory_requires_attached_isolated_selection(monkeypatch, tmp_path):
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic board")
    calls = []
    backend = NS(
        read_timeout_seconds=300,
        invoke=lambda method, params, **kwargs: calls.append((method, params)) or {},
    )
    monkeypatch.setattr(pcb, "reader", lambda _options: backend)
    base = {"project": str(board), "live_geometry": True, "pid": 42, "mask_inventory": True}
    pcb.info(base)
    assert calls[0][0] == "board_info" and calls[0][1]["mask_inventory_only"] is True
    for extra in (
        {"nets": "A", "layers": "1"},
        {"components": True},
        {"drc_hazards": True},
        {"live_geometry": False},
    ):
        with pytest.raises(CLIError):
            pcb.info({**base, **extra})
    assert len(calls) == 1
