"""Guard wrong libraries, duplicate startup, false registration and unsafe cleanup."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as adapter
from xpedition_cli import native_library_manager as lm


class Collection(list):
    @property
    def Count(self):
        return len(self)


def application(path, tables=None):
    tables = tables or {}
    library = SimpleNamespace(
        FullName=str(path),
        Partitions=lambda kind: Collection(
            SimpleNamespace(Name=name, Reservation="", Count=2) for name in tables.get(kind, [])
        ),
    )
    return SimpleNamespace(ActiveLibrary=library)


def test_registered_partition_getters_are_distinct_from_files(tmp_path, monkeypatch):
    lmc = tmp_path / "QA.lmc"
    lmc.write_bytes(b"native")
    (tmp_path / "PartsDBLibs").mkdir()
    (tmp_path / "PartsDBLibs" / "Unregistered.pdb").write_bytes(b"present")
    monkeypatch.setattr(lm, "application_identity", lambda *args: {"pid": 42})
    observed = lm.snapshot(application(lmc, {1: ["Registered"], 3: ["Cells"]}), lmc, "LM.155")
    assert [r["name"] for r in observed["partitions"]["parts"]] == ["Registered"]
    assert [r["name"] for r in observed["partitions"]["cells"]] == ["Cells"]
    assert observed["application"]["pid"] == 42


def test_read_does_not_switch_launch_or_activate(tmp_path, monkeypatch):
    wrong = application(tmp_path / "Other.lmc")
    client = SimpleNamespace(GetActiveObject=lambda name: wrong)
    monkeypatch.setattr(lm, "application_progids", lambda domain: ("Registered.LM",))
    result = lm.observe(client, tmp_path / "Requested.lmc")
    assert result["status"] == "not_observed"
    assert result["error"]["code"] == "E_CONFLICT"
    assert wrong.ActiveLibrary.FullName.endswith("Other.lmc")


def test_existing_pending_manager_prevents_duplicate_start(tmp_path, monkeypatch):
    def absent(client):
        raise adapter.AdapterError("E_NOT_FOUND", "not active")

    monkeypatch.setattr(lm, "active", absent)
    monkeypatch.setattr(lm, "processes", lambda domain: [{"pid": 77}])
    with (
        pytest.raises(adapter.AdapterError) as failure,
        lm.connected(object(), tmp_path / "QA.lmc"),
    ):
        pytest.fail("A pending process must not yield a usable manager")
    assert failure.value.code == "E_HUMAN_REQUIRED"
    assert failure.value.details["processes"] == [{"pid": 77}]


def test_an_existing_exact_manager_is_never_closed(tmp_path, monkeypatch):
    lmc = tmp_path / "QA.lmc"
    lmc.write_bytes(b"native")
    app = application(lmc)
    monkeypatch.setattr(lm, "active", lambda client: (app, "Registered.LM"))
    monkeypatch.setattr(lm, "application_identity", lambda *args: {"pid": 42})
    with lm.connected(object(), lmc) as (observed, progid):
        assert observed is app and progid == "Registered.LM"
    # The fake deliberately has neither Close nor Quit: calling either fails.


def test_missing_partitions_stop_before_any_converter_or_symbol_write(tmp_path, monkeypatch):
    project = tmp_path / "QA.prj"
    project.write_text("unchanged", encoding="utf-8")
    lmc = tmp_path / "Library.lmc"
    lmc.write_bytes(b"native")
    app = application(lmc, {3: ["QA"]})

    @contextmanager
    def connected(client, target, **kwargs):
        yield app, "Registered.LM"

    monkeypatch.setattr(lm, "connected", connected)
    monkeypatch.setattr(lm, "application_identity", lambda *args: {"pid": 42})
    monkeypatch.setattr(adapter, "_central_library", lambda path: (lmc, tmp_path))
    with pytest.raises(adapter.AdapterError) as failure:
        adapter._library_import(
            {
                "project": str(project),
                "partition": "QA",
                "parts": "parts",
                "symbols": {"QA": "sym"},
            },
            None,
        )
    assert failure.value.code == "E_HUMAN_REQUIRED"
    assert failure.value.details["native_definition_write_started"] is False
    assert failure.value.details["missing"] == [
        {"kind": "parts", "name": "QA"},
        {"kind": "symbols", "name": "QA"},
    ]
    assert project.read_text(encoding="utf-8") == "unchanged"
    assert not (tmp_path / "Work").exists()
    assert not (tmp_path / "SymbolLibs").exists()


def test_ownership_mismatch_never_quits_an_unverified_instance(tmp_path, monkeypatch):
    calls = 0
    app = SimpleNamespace(Quit=lambda: pytest.fail("Unverified instance was terminated"))

    def attach(client):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise adapter.AdapterError("E_NOT_FOUND", "absent")
        return app, "Registered.LM"

    monkeypatch.setattr(lm, "active", attach)
    monkeypatch.setattr(lm, "application_identity", lambda *args: {"pid": 777})
    monkeypatch.setattr(lm, "processes", lambda domain: [])
    monkeypatch.setattr(
        adapter, "_native_executable", lambda domain: (tmp_path / "launcher", tmp_path)
    )
    monkeypatch.setattr(lm.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=100))
    with (
        pytest.raises(adapter.AdapterError) as failure,
        lm.connected(object(), tmp_path / "QA.lmc"),
    ):
        pytest.fail("Wrong ownership was accepted")
    assert failure.value.code == "E_CONFLICT"


def test_no_com_client_cannot_accidentally_launch_a_native_application(tmp_path, monkeypatch):
    monkeypatch.setattr(
        lm.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("Test crossed the native boundary"),
    )
    with pytest.raises(adapter.AdapterError) as failure, lm.connected(None, tmp_path / "QA.lmc"):
        pytest.fail("A missing COM client must be rejected before activation")
    assert failure.value.code == "E_BACKEND_UNAVAILABLE"
