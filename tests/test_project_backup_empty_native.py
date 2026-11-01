"""An installed adapter is not a reason to RPC when every CAD inventory is empty."""

from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import backends, native_process, session
from xpedition_cli.cli import project as P
from xpedition_cli.errors import CLIError


@pytest.fixture
def empty_scene(tmp_path, monkeypatch):
    project = tmp_path / "official-copy.prj"
    project.write_bytes(b"fixture file-only archive source")
    state = NS(
        project=project, rows={"library": [], "schematic": [], "pcb": []}, reads=[], calls=[]
    )

    def processes(domain):
        state.reads.append(domain)
        return copy.deepcopy(state.rows[domain])

    monkeypatch.setattr(native_process, "processes", processes)

    class AvailableBackend:
        def status(self):
            state.calls.append("status")
            return {"available": True}

        def invoke(self, method, params, **kwargs):
            state.calls.append((method, copy.deepcopy(params)))
            return {"same_exact_owned_route": True, "quiescent": True}

    monkeypatch.setattr(backends, "NativeBackend", AvailableBackend)
    monkeypatch.setattr(
        session, "clear_native", lambda: pytest.fail("Old ownership state must stay unchanged")
    )
    return state


def test_available_adapter_but_all_inventories_empty_uses_no_rpc_or_save(empty_scene):
    state = empty_scene
    preview = P.release_preflight(state.project)
    assert state.reads == ["library", "schematic", "pcb"] and state.calls == []
    assert preview["read_only"] is True and preview["save_count"] == preview["close_count"] == 0
    _, result = P._release_for_archive(state.project, preview)
    assert state.reads == ["library", "schematic", "pcb"] * 2
    assert state.calls == [] and result["quiescent"] is True
    assert "save_count" not in result and "designer_closed" not in result
    assert result["release_preflight"] == preview


@pytest.mark.parametrize("domain", ["library", "schematic", "pcb"])
def test_nonempty_inventory_keeps_the_existing_owned_native_release_route(empty_scene, domain):
    state = empty_scene
    state.rows[domain] = [{"pid": 4242, "fixture": True}]
    preview = P.release_preflight(state.project)
    assert state.calls[0] == "status" and state.calls[1][0] == "release_project_preflight"
    state.calls.clear()
    _, result = P._release_for_archive(state.project, preview)
    assert result["same_exact_owned_route"] is True
    assert state.calls[0] == "status" and state.calls[1][0] == "release_project"
    assert state.calls[1][1]["expected_release_preflight"] == preview


def test_empty_preview_then_writer_arrives_refuses_before_rpc(empty_scene):
    state = empty_scene
    preview = P.release_preflight(state.project)
    state.rows["schematic"] = [{"pid": 4242, "fixture": True}]
    with pytest.raises(CLIError) as error:
        P._release_for_archive(state.project, preview)
    assert error.value.code == "E_CONFLICT"
    assert error.value.details["write_attempted"] is False
    assert error.value.details["save_count"] == error.value.details["close_count"] == 0
    assert state.calls == []


def test_incomplete_inventory_is_never_observed_empty(empty_scene):
    state = empty_scene
    state.rows["library"] = None
    with pytest.raises(CLIError) as error:
        P.release_preflight(state.project)
    assert error.value.code == "E_HUMAN_REQUIRED"
    assert error.value.details["process_inventory_complete"] is False
    assert state.calls == []
