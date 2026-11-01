"""Offline COM/UI stand-ins: never launch Layout or obtain a license."""

from types import SimpleNamespace

import pytest
from test_drc_engine import report_files

from xpedition_cli import cell_refresh, drc_engine, native_identity
from xpedition_cli import native_com_adapter as bridge


class Collection(list):
    @property
    def Count(self):  # noqa: N802 - COM property
        return len(self)

    def Item(self, index):  # noqa: N802 - COM method
        return self[index - 1]


@pytest.fixture
def native_drc(tmp_path, monkeypatch):
    pcb = tmp_path / "PCB/fixture.pcb"
    report_files(pcb)
    commands, reads = [], []
    hazards = []

    def get_hazards(kind):
        reads.append(kind)
        return Collection(hazards)

    doc = SimpleNamespace(FullName=str(pcb), GetHazards=get_hazards)

    def process(command):
        commands.append(command)
        return True

    app = SimpleNamespace(Gui=SimpleNamespace(ProcessCommand=process))
    monkeypatch.setattr(bridge, "_layout_board_path", lambda params: pcb)
    monkeypatch.setattr(bridge, "_application", lambda *args, **kwargs: app)
    monkeypatch.setattr(bridge, "_open_layout_document", lambda *args: (doc, []))
    monkeypatch.setattr(bridge, "_licensed_document", lambda app: doc)

    def save_close(*args, lifecycle):
        lifecycle.update(save_count=1, close_count=1, save_acknowledged=True, closed_observed=True)

    monkeypatch.setattr(bridge, "_save_close_board", save_close)
    monkeypatch.setattr(
        cell_refresh, "capture", lambda *args: {"components": [], "model": {"layers": 4}}
    )
    monkeypatch.setattr(
        native_identity, "application_identity", lambda *args: {"product_version": "151 fixture"}
    )
    monkeypatch.setattr(drc_engine, "registered_engine", lambda: "MGCPCBEngines.DRC.151")

    class Engine:
        failure = None
        emit = True
        Errors = []
        calls = 0

        def Go(self):  # noqa: N802
            self.calls += 1
            if self.failure:
                raise self.failure
            if self.emit:
                report_files(pcb)

        def WriteSetupFile(self):  # noqa: N802
            pytest.fail("Hidden WriteSetupFile must never be dispatched")

    engine = Engine()
    client = SimpleNamespace(Dispatch=lambda progid: engine)
    return SimpleNamespace(
        pcb=pcb,
        app=app,
        commands=commands,
        reads=reads,
        hazards=hazards,
        engine=engine,
        client=client,
    )


def test_deadline_never_reads_previous_hazards_or_reports_a_pass(native_drc):
    native_drc.engine.failure = TimeoutError("synthetic native deadline")
    with pytest.raises(bridge.AdapterError) as caught:
        bridge._batch_drc({"project": str(native_drc.pcb)}, native_drc.client)
    result = caught.value.details["native_result"]
    assert caught.value.code == "E_TIMEOUT"
    assert result["completion"]["timed_out"] is True
    assert not result["ran"] and not result["passes"] and not result["source"]["complete"]
    assert native_drc.reads == [] and native_drc.engine.calls == 1


def test_engine_return_without_new_report_is_not_completion(native_drc):
    native_drc.engine.emit = False
    with pytest.raises(bridge.AdapterError) as caught:
        bridge._batch_drc({"project": str(native_drc.pcb)}, native_drc.client)
    result = caught.value.details["native_result"]
    assert not result["ran"] and not result["passes"]
    assert result["board_save_acknowledged"] and result["board_close_observed"]
    assert result["persistence_state"] == "partial"
    assert native_drc.reads == [] and native_drc.engine.calls == 1


def test_fresh_engine_report_uses_no_processing_window_heuristic(native_drc):
    native_drc.app.Gui.ProcessCommand = lambda command: pytest.fail("GUI checker heuristic")
    result = bridge._batch_drc({"project": str(native_drc.pcb)}, native_drc.client)
    assert result["ran"] and result["completed"] and result["count"] == 1
    assert not result["passes"]
    assert result["source"]["freshness"] == "current_run"
    assert native_drc.reads == [] and native_drc.engine.calls == 1


def test_no_run_reports_existing_hazards_without_claiming_a_current_pass(native_drc):
    native_drc.hazards.append(
        SimpleNamespace(Type=91, Description="existing finding", Accepted=False)
    )
    result = bridge._batch_drc({"project": str(native_drc.pcb), "run": False}, None)
    assert native_drc.commands == [] and native_drc.reads == [bridge.HAZARDS_BATCH]
    assert result["count"] == 1 and result["hazards"][0]["description"] == "existing finding"
    assert result["source"]["freshness"] == "existing"
    assert result["verification"]["status"] == "not_run" and not result["passes"]


def test_rejected_engine_does_not_read_or_certify_hazards(native_drc):
    native_drc.engine.failure = RuntimeError("synthetic engine rejection")
    with pytest.raises(bridge.AdapterError) as caught:
        bridge._batch_drc({"project": str(native_drc.pcb)}, native_drc.client)
    assert caught.value.code == "E_NATIVE_FAILURE" and native_drc.reads == []
    assert native_drc.engine.calls == 1


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_wait_bound_is_rejected_before_the_command(native_drc, timeout):
    with pytest.raises(bridge.AdapterError) as caught:
        bridge._batch_drc({"project": str(native_drc.pcb), "timeout": timeout}, None)
    assert caught.value.code == "E_VALIDATION" and native_drc.commands == []
