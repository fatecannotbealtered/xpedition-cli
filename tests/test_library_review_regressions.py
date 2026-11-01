"""Review reproductions: dependency registration and isolated owned cleanup."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fakes import FakeLibrary
from test_library_import import _read
from test_native_library_manager import application

from xpedition_cli import library_import as importer
from xpedition_cli import native_com_adapter as adapter
from xpedition_cli import native_library_manager as lm


def kept_dependency_plan(tmp_path, action):
    source = _read(FakeLibrary(tmp_path / "Source"))
    target = _read(FakeLibrary(tmp_path / "Target"))
    part = source.find_parts("RES-10K")[0]
    part["partition"] = "NewParts"
    source.texts[("parts", "NewParts")] = source.texts[("parts", "PartQuest")]
    target.parts = [row for row in target.parts if row["number"] != "RES-10K"]
    if action == "replace":
        target.parts.append({**part, "description": "different held description"})
    # The identical cell is actually resolved to the target's other partition.
    target.find_cell(part["cell"])["partition"] = "KeptCells"
    return importer.plan(source, target, ["RES-10K"])


@pytest.mark.parametrize("action", ["add", "replace"])
def test_complete_registration_is_separate_from_kept_dependency_payload(tmp_path, action):
    plan = kept_dependency_plan(tmp_path, action)
    assert plan.parts[0]["action"] == action
    assert all(row["action"] == "keep" for row in plan.symbols + plan.cells)
    assert len(plan.units) == 1 and plan.units[0]["partition"] == "NewParts"
    assert not plan.units[0]["symbols"] and not plan.units[0]["cells"]
    assert plan.required_partitions == {
        "parts": ["NewParts"],
        "symbols": ["PartQuest"],
        "cells": ["KeptCells"],
    }


@pytest.mark.parametrize("action", ["add", "replace"])
@pytest.mark.parametrize("missing_kind", ["symbols", "cells"])
def test_kept_unregistered_dependency_stops_before_any_definition_or_designer_work(
    tmp_path, monkeypatch, action, missing_kind
):
    plan = kept_dependency_plan(tmp_path, action)
    project = tmp_path / "QA.prj"
    project.write_text("unchanged", encoding="utf-8")
    lmc = tmp_path / "QA.lmc"
    lmc.write_bytes(b"fixture")
    tables = {1: ["NewParts"], 2: ["PartQuest"], 3: ["KeptCells"]}
    tables[lm.PARTITION_TYPES[missing_kind]] = []
    app = application(lmc, tables)

    @contextmanager
    def connected(client, target, **kwargs):
        yield app, "Registered.LM"

    monkeypatch.setattr(lm, "connected", connected)
    monkeypatch.setattr(lm, "application_identity", lambda *args: {"pid": 42})
    monkeypatch.setattr(adapter, "_central_library", lambda path: (lmc, tmp_path))
    monkeypatch.setattr(
        adapter,
        "_viewdraw_application",
        lambda *a, **kw: pytest.fail("Touched Designer before gate"),
    )
    monkeypatch.setattr(
        adapter, "_run_library_tool", lambda *a, **kw: pytest.fail("Ran converter before gate")
    )
    with pytest.raises(adapter.AdapterError) as failure:
        adapter._library_import(
            {
                "project": str(project),
                "units": plan.units,
                "required_partitions": {
                    "parts": ["NewParts"],
                    "symbols": ["PartQuest"],
                    "cells": ["KeptCells"],
                },
            },
            object(),
        )
    assert failure.value.code == "E_HUMAN_REQUIRED"
    assert failure.value.details["missing"] == [
        {"kind": missing_kind, "name": "PartQuest" if missing_kind == "symbols" else "KeptCells"}
    ]
    assert failure.value.details["native_definition_write_started"] is False
    assert project.read_text(encoding="utf-8") == "unchanged"
    assert not (tmp_path / "Work").exists()


class CleanupApp:
    def __init__(self, failure):
        self.failure = failure
        self.reads = 0
        self.calls = []
        self.library = SimpleNamespace(Close=self.close)

    @property
    def ActiveLibrary(self):
        self.reads += 1
        if self.reads > 1:
            self.calls.append("ActiveLibrary")
            if self.failure == "ActiveLibrary":
                raise RuntimeError("cleanup getter failed")
        return self.library

    def close(self):
        self.calls.append("Close")
        if self.failure == "Close":
            raise RuntimeError("cleanup close failed")

    def Quit(self):
        self.calls.append("Quit")
        if self.failure == "Quit":
            raise RuntimeError("cleanup quit failed")


@pytest.mark.parametrize("failed_cleanup", [None, "ActiveLibrary", "Close", "Quit"])
@pytest.mark.parametrize("primary_fails", [False, True])
def test_owned_cleanup_preserves_primary_and_attempts_quit(
    tmp_path, monkeypatch, failed_cleanup, primary_fails
):
    app = CleanupApp(failed_cleanup)
    attached = False

    def active(client):
        nonlocal attached
        if not attached:
            attached = True
            raise adapter.AdapterError("E_NOT_FOUND", "absent")
        return app, "Registered.LM"

    probes = 0

    def processes(domain):
        nonlocal probes
        probes += 1
        return [] if probes == 1 else [{"pid": 222, "parent_pid": 111}]

    monkeypatch.setattr(lm, "active", active)
    monkeypatch.setattr(lm, "processes", processes)
    monkeypatch.setattr(lm, "application_identity", lambda *a: {"pid": 222})
    monkeypatch.setattr(lm, "snapshot", lambda *a: {})
    monkeypatch.setattr(adapter, "_native_executable", lambda domain: (tmp_path / "exe", tmp_path))
    monkeypatch.setattr(lm.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(pid=111))
    primary = adapter.AdapterError(
        "E_HUMAN_REQUIRED",
        "missing required partition",
        {
            "missing": [{"kind": "symbols", "name": "Kept"}],
            "native_definition_write_started": False,
        },
    )
    report = {}
    if failed_cleanup is None and not primary_fails:
        with lm.connected(object(), tmp_path / "QA.lmc", cleanup_report=report):
            pass
        assert report["quit_requested"] is True
        assert report["process_exit"]["status"] == "running"
        assert report["status"] == "incomplete"
        return
    with pytest.raises(adapter.AdapterError) as failure:
        with lm.connected(object(), tmp_path / "QA.lmc", cleanup_report=report):
            if primary_fails:
                raise primary
    assert "Quit" in app.calls
    if primary_fails:
        assert failure.value is primary
        assert failure.value.details["missing"] == [{"kind": "symbols", "name": "Kept"}]
        assert failure.value.details["native_definition_write_started"] is False
    else:
        assert failure.value.code == "E_HUMAN_REQUIRED"
    cleanup = failure.value.details["cleanup"]
    if failed_cleanup is not None:
        assert any(
            row["action"] == failed_cleanup and row["status"] == "failed"
            for row in cleanup["steps"]
        )
    assert cleanup["process_exit"]["status"] != "exited"
