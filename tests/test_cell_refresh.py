"""Synthetic failure and public SDK tests; not native geometry acceptance."""

import copy
from types import SimpleNamespace

import pytest

from xpedition_cli import cell_refresh as refresh
from xpedition_cli import native_com_adapter as native


def fixture_plan():
    def pad(w, h):
        return {
            "shape": "polygon",
            "bounds_mm": [-w / 2, -h / 2, w / 2, h / 2],
            "points_mil": [
                [x / 0.0254 for x in [-w / 2, -w / 2, w / 2, w / 2, -w / 2]],
                [y / 0.0254 for y in [-h / 2, h / 2, h / 2, -h / 2, -h / 2]],
                [0] * 5,
            ],
        }

    def state(w):
        return {
            "components": [
                {
                    "refdes": "R1",
                    "uid": "62",
                    "footprint": "R0805",
                    "rotation": 0,
                    "geometry": [
                        {
                            "number": "1",
                            "net": "SIGNAL",
                            "center_mm": [0, 0],
                            "copper": [{"layer": 1, "geometry": [pad(w, 1.4)]}],
                            "fabrication": [
                                {
                                    "name": str(w + 0.1),
                                    "type": 2,
                                    "side": 1,
                                    "layer": 1,
                                    "geometry": [pad(w + 0.1, 1.5)],
                                },
                                {
                                    "name": str(w),
                                    "type": 1,
                                    "side": 1,
                                    "layer": 1,
                                    "geometry": [pad(w, 1.4)],
                                },
                            ],
                            "holes": [],
                        }
                    ],
                }
            ],
            "model": {"outline": [[0, 0], [50, 50]], "traces": []},
            "stackup": [1, 2, 3, 4],
        }

    def shape(w, h):
        return {"shape": "RECTANGLE", "width": w, "height": h}

    definition = {
        "R0805": {
            "cell": {"pins": [{"number": "1", "padstack": "SMD", "rotation": 0}]},
            "padstacks": {
                "SMD": {
                    "pad": shape(1.225, 1.4),
                    "mask": shape(1.325, 1.5),
                    "paste": shape(1.225, 1.4),
                }
            },
        }
    }
    return {
        "before": state(1.025),
        "affected_instances": ["R1"],
        "definitions": {"central": definition, "local": {}},
        "pcb": "fixture.pcb",
        "fingerprint": "bound",
        "no_change": False,
    }, state(1.225)


@pytest.mark.parametrize(
    "fault",
    [None, "old_width", "mask", "center", "uid", "net", "layer", "outline", "local_definition"],
)
def test_three_levels_and_invariants_are_independent(fault):
    plan, state = fixture_plan()
    local = copy.deepcopy(plan["definitions"]["central"])
    pin = state["components"][0]["geometry"][0]
    if fault == "old_width":
        state = plan["before"]
    elif fault == "mask":
        pin["fabrication"][0]["geometry"][0]["bounds_mm"][2] += 0.2
    elif fault == "center":
        pin["copper"][0]["geometry"][0]["bounds_mm"][0] += 0.2
        pin["copper"][0]["geometry"][0]["bounds_mm"][2] += 0.2
    elif fault == "uid":
        state["components"][0]["uid"] = "replacement"
    elif fault == "net":
        pin["net"] = "OTHER"
    elif fault == "layer":
        pin["copper"][0]["layer"] = 4
    elif fault == "outline":
        state["model"]["outline"][1] = [60, 60]
    elif fault == "local_definition":
        local = {}
    assert refresh.compare(plan, state, local)["valid"] is (fault is None)


def test_drift_refuses_before_native_command(monkeypatch):
    plan, _ = fixture_plan()
    app = SimpleNamespace(
        Gui=SimpleNamespace(ProcessCommand=lambda *a: pytest.fail("write dispatched"))
    )
    monkeypatch.setattr(refresh, "make_plan", lambda *args: (plan, app, None))
    with pytest.raises(native.AdapterError) as error:
        refresh.execute({"apply": True, "expected": "different"}, None, native)
    assert error.value.code == "E_CONFLICT"
    assert error.value.details["write_attempted"] is False


def test_preflight_read_error_keeps_not_started(monkeypatch):
    def fail(*args):
        raise native.AdapterError("E_NOT_FOUND", "missing cell")

    monkeypatch.setattr(refresh, "make_plan", fail)
    with pytest.raises(native.AdapterError) as error:
        refresh.execute({"apply": True}, None, native)
    assert error.value.code == "E_NOT_FOUND"
    assert error.value.details["write_attempted"] is False


@pytest.mark.parametrize("unreadable", [False, True])
def test_library_container_save_survives_later_preparation_failure(
    tmp_path, monkeypatch, unreadable
):
    plan, _ = fixture_plan()
    library = tmp_path / "test.lmc"
    library.write_bytes(b"before")
    plan["definitions"].update(central_library=str(library), sources=[refresh._file(library)])
    monkeypatch.setattr(refresh, "make_plan", lambda *args: (plan, None, None))

    def fail_after_native_save(*args):
        library.write_bytes(b"native lifecycle saved container")
        if unreadable:

            def unavailable(path):
                raise OSError("independent file read unavailable")

            monkeypatch.setattr(refresh, "_file", unavailable)
        raise native.AdapterError("E_CONFLICT", "later identity failed", {"write_attempted": False})

    monkeypatch.setattr(refresh, "prepare_library_services", fail_after_native_save)
    with pytest.raises(native.AdapterError) as error:
        refresh.execute({"apply": True, "expected": "bound"}, None, native)
    result = error.value.details["native_result"]
    assert error.value.details["write_attempted"] is True
    assert result["stages"]["local_definition"] == "not_started"
    if unreadable:
        assert result["library_container"]["status"] == "not_observed"
        assert result["change_status"] == result["persistence_state"] == "unknown"
    else:
        assert result["library_container"]["changed"] is True
        assert result["change_status"] == result["persistence_state"] == "partial"
    assert result["saved"] is False  # board save was never reached


@pytest.mark.parametrize("stage", ["stale_log", "native_error", "readback_error", "success"])
def test_native_refresh_is_dispatched_once_and_preserves_stage_facts(tmp_path, monkeypatch, stage):
    plan, state = fixture_plan()
    board = tmp_path / "PCB/test.pcb"
    board.parent.mkdir()
    plan.update(pcb=str(board), project=str(tmp_path / "test.prj"))
    lmc = tmp_path / "library.lmc"
    lmc.write_bytes(b"synthetic library")
    plan["definitions"].update(central_library=str(lmc), sources=[refresh._file(lmc)])
    log = board.parent / "LogFiles/UpdateLocalDBs.txt"
    log.parent.mkdir()
    log.write_text("The Local Cell And Padstack DBs have been updated with no errors or warnings.")
    calls = []

    def dispatch(*args):
        calls.append(args)
        if stage == "native_error":
            raise native.AdapterError("E_SERVER", "native failed after dispatch")
        if stage != "stale_log":
            log.write_text(
                "The Local Cell And Padstack DBs have been updated with no errors or warnings."
            )
        return True

    app = SimpleNamespace(Gui=SimpleNamespace(ProcessCommand=dispatch))
    monkeypatch.setattr(refresh, "make_plan", lambda *args: (plan, app, None))
    monkeypatch.setattr(refresh, "capture", lambda *args: state)
    monkeypatch.setattr(refresh, "after_services", lambda *args: (app, None))
    monkeypatch.setattr(
        refresh, "prepare_library_services", lambda *args: {"status": "normally_closed"}
    )
    monkeypatch.setattr(native, "_licensed_document", lambda *args: object())
    monkeypatch.setattr(native, "_application", lambda *args, **kwargs: app)
    monkeypatch.setattr(native, "_reopen_board", lambda *args: object())

    def definitions(*args):
        if stage == "readback_error":
            raise native.AdapterError("E_IO", "export unavailable after saved reopen")
        return {"local": plan["definitions"]["central"]}

    monkeypatch.setattr(refresh, "definitions", definitions)
    if stage == "success":
        result = refresh.execute(
            {"apply": True, "expected": "bound", "cache": str(tmp_path)}, None, native
        )
        assert result["verification"]["valid"]
        assert result["stages"]["instances"] == "verified"
    else:
        with pytest.raises(native.AdapterError) as error:
            refresh.execute(
                {"apply": True, "expected": "bound", "cache": str(tmp_path)}, None, native
            )
        result = error.value.details["native_result"]
        assert result["persistence_verified"] is (stage == "readback_error")
        if stage == "stale_log":
            assert result["native_log"]["status"] == "not_generated"
    assert calls == [(refresh.COMMAND, True)]


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "instance_identity",
        "library_input",
        "old_instance",
        "container_only",
        "external_library",
    ],
)
def test_public_sdk_refresh_receipt_and_duplicate_confirm(tmp_path, monkeypatch, project, fault):
    from test_driver_v2 import _scope, _worker_fixture

    from xpedition_cli.cli import pcb
    from xpedition_cli.driver import Adapter, runtime
    from xpedition_cli.errors import CLIError

    _scope(tmp_path, monkeypatch)
    library = project.parent / "Lib/Lib.lmc"
    library.parent.mkdir()
    library.write_bytes(b"synthetic source")
    if fault == "external_library":
        external = tmp_path.parent / (tmp_path.name + "-shared") / "shared.lmc"
        external.parent.mkdir()
        external.write_bytes(b"outside this task's write scope")
        project.write_text(project.read_text().replace(str(library), str(external)))
    project.write_text(
        project.read_text().replace('KEY PCBDesignPath ""', 'KEY PCBDesignPath "PCB/board.pcb"')
    )
    plan, after = fixture_plan()
    calls = []
    current = copy.deepcopy(plan["before"])

    class Backend:
        def invoke(self, method, params, **kwargs):
            nonlocal current
            if method == "identity":
                assert params["domain"] == "pcb", "Geometry-only update must not require Designer"
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [{"refdes": "R1", "uid": "62"}], "fixture": True}
            if method == "cell_refresh_state":
                return {"geometry": copy.deepcopy(current)}
            assert method == "refresh_cells"
            if not params.get("apply"):
                return copy.deepcopy(plan)
            calls.append("apply")
            if fault == "container_only":
                raise CLIError(
                    "E_NATIVE_FAILURE",
                    "Failure after native LMC save",
                    {
                        "write_attempted": True,
                        "native_result": {
                            "applied": False,
                            "saved": False,
                            "change_status": "partial",
                            "persistence_state": "partial",
                            "library_container": {"changed": True},
                            "stages": {
                                "local_definition": "not_started",
                                "instances": "not_started",
                            },
                        },
                    },
                )
            if fault != "old_instance":
                current = copy.deepcopy(after)
            return {
                "outcome": "cells_refreshed",
                "applied": True,
                "persistence_verified": True,
                "definitions_after": {"local": plan["definitions"]["central"]},
            }

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    args = ["pcb", "update", "--project", str(project), "--refresh-cells", "--dangerous"]
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run"])
        if fault == "external_library":
            assert not preview.envelope["ok"]
            assert preview.envelope["error"]["code"] == "E_FORBIDDEN"
            assert not calls
            return
        assert preview.envelope["ok"], preview.envelope
        token = preview.envelope["data"]["confirm_token"]
        if fault == "instance_identity":
            current["components"][0]["uid"] = "different"
        if fault == "library_input":
            library.write_bytes(b"changed input")
        applied = cli.run([*args, "--confirm", token])
        if fault in {"instance_identity", "library_input"}:
            assert applied.envelope["error"]["code"] == "E_CONFLICT"
            assert applied.envelope["error"]["details"]["dispatch_state"] == "not_started"
            assert not calls
            return
        if fault == "old_instance":
            assert not applied.envelope["ok"]
            receipt = applied.envelope["error"]["details"]["receipt"]
            assert receipt["outcome"]["persistence"] == "saved"
            assert receipt["outcome"]["verification"]["status"] == "failed"
            assert calls == ["apply"]
            return
        if fault == "container_only":
            assert not applied.envelope["ok"]
            receipt = applied.envelope["error"]["details"]["receipt"]
            assert receipt["outcome"]["change"] == "partial"
            assert receipt["outcome"]["persistence"] == "partial"
            assert calls == ["apply"]
            return
        assert applied.envelope["ok"], applied.envelope
        receipt = applied.envelope["data"]["receipt"]
        assert receipt["outcome"]["change"] == "applied"
        assert receipt["outcome"]["persistence"] == "saved"
        duplicate = cli.run([*args, "--confirm", token])
        assert not duplicate.envelope["ok"]
    assert calls == ["apply"]


def test_geometry_observation_never_instantiates_cell_directory(monkeypatch):
    class Doc:
        @property
        def Cells(self):
            pytest.fail("Hosted cell directory must not be initialized by observation")

    comp = SimpleNamespace(Pins=[], PlacementOutlines=[], InsertionOutlines=[])
    a = SimpleNamespace(
        _com_member=lambda *args: [comp],
        _component_record=lambda obj: {"refdes": "R1", "uid": "62"},
        _board_model=lambda doc: {},
        _board_stackup=lambda doc: [],
    )
    assert refresh.capture(Doc(), a)["components"][0]["partition"] is None


@pytest.mark.parametrize("fault", [None, "locked", "existing", "wrong_library", "close_failed"])
def test_library_services_only_closes_new_verified_owned_window(tmp_path, monkeypatch, fault):
    from xpedition_cli import native_identity, win_dialogs

    board, library = tmp_path / "board.pcb", tmp_path / "library.lmc"
    calls, windows = [], [42] if fault == "existing" else []
    plan = {
        "pcb": str(board),
        "before": {},
        "definitions": {"central_library": str(library), "sources": []},
    }
    monkeypatch.setattr(native_identity, "application_identity", lambda *args: {"pid": 123})
    monkeypatch.setattr(win_dialogs, "desktop_locked", lambda: fault == "locked")
    monkeypatch.setattr(win_dialogs, "find_dialogs", lambda **kwargs: [])
    monkeypatch.setattr(refresh, "_services_windows", lambda pid: list(windows))
    monkeypatch.setattr(
        refresh,
        "_services_paths",
        lambda hwnd: [str(tmp_path / "other.lmc" if fault == "wrong_library" else library)],
    )
    monkeypatch.setattr(
        refresh, "capture", lambda *args: {"drift": True} if fault == "input_drift" else {}
    )
    monkeypatch.setattr(
        native, "_licensed_document", lambda app: SimpleNamespace(FullName=str(board))
    )
    ticks = iter(range(100))
    monkeypatch.setattr(refresh.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(refresh.time, "sleep", lambda seconds: None)

    def command(name, sync):
        calls.append((name, sync))
        windows.append(42)
        return True

    def close(hwnd):
        calls.append(("WM_CLOSE", hwnd))
        if fault != "close_failed":
            windows.clear()

    monkeypatch.setattr(refresh, "_services_close", close)
    app = SimpleNamespace(Gui=SimpleNamespace(ProcessCommand=command))
    if fault:
        with pytest.raises(native.AdapterError) as error:
            refresh.prepare_library_services(app, plan, native)
        assert error.value.details["write_attempted"] is False
        if fault in {"locked", "existing"}:
            assert not calls
        if fault == "wrong_library":
            assert calls == [("SETUP_LIBRARYSERVICES", False)]
    else:
        result = refresh.prepare_library_services(app, plan, native)
        assert result["status"] == "normally_closed"
        assert result["forced_reservation_release"] is False
        assert calls == [("SETUP_LIBRARYSERVICES", False), ("WM_CLOSE", 42)]


@pytest.mark.parametrize("apply", [False, True])
def test_refresh_plan_timeout_is_not_an_unknown_engineering_write(adapter, monkeypatch, apply):
    import subprocess

    from xpedition_cli.backends import native_xpedition as backend
    from xpedition_cli.errors import CLIError
    from xpedition_cli.session import read_state

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("fixture", 1)

    monkeypatch.setattr(backend.subprocess, "run", timeout)
    with pytest.raises(CLIError) as error:
        backend.NativeBackend().invoke("refresh_cells", {"apply": apply}, timeout_seconds=1)
    assert error.value.details["native_query_only"] is (not apply)
    assert (read_state().get("state") == "stale") is apply


@pytest.mark.parametrize("drift", [False, True])
def test_after_services_reacquires_identity_once_without_reopening(tmp_path, monkeypatch, drift):
    calls = []
    fresh_app, fresh_doc = object(), object()

    def attach(*args):
        calls.append("attach/read-only")
        return None, None, fresh_app, fresh_doc

    monkeypatch.setattr(refresh, "active", attach)
    monkeypatch.setattr(refresh, "capture", lambda doc, a: {"uid": "new" if drift else "old"})
    lmc = tmp_path / "library.lmc"
    lmc.write_bytes(b"original container")
    plan = {
        "project": str(tmp_path / "test.prj"),
        "before": {"uid": "old"},
        "definitions": {"central_library": str(lmc), "sources": [refresh._file(lmc)]},
    }
    lmc.write_bytes(b"planned native container save")
    monkeypatch.setattr(native, "_central_library", lambda path: (lmc, tmp_path))
    if drift:
        with pytest.raises(native.AdapterError) as error:
            refresh.after_services({}, plan, None, native)
        assert error.value.details["write_attempted"] is False
    else:
        assert refresh.after_services({}, plan, None, native) == (fresh_app, fresh_doc)
    assert calls == ["attach/read-only"]
