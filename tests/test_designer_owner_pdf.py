"""PDF owner routing and child responsibility; no CAD or vendor producer runs."""

from __future__ import annotations

import copy
import os
import sys
import threading
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest
from test_designer_owner_client import CMD as CLIENT_CMD
from test_designer_owner_client import OP, auth, start
from test_designer_owner_client import api as _client_api
from test_designer_owner_dispatch import editing_scope as _scope_fixture
from test_designer_owner_dispatch import scene as _scene_fixture

from xpedition_cli import designer_driver_routing as R
from xpedition_cli import designer_frozen_binding as F
from xpedition_cli import designer_native_owner as O
from xpedition_cli import designer_owner_client as C
from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli import native_com_adapter as A
from xpedition_cli.errors import CLIError

CMD = "owner-command-" + "c" * 32
api = _client_api
scene, editing_scope = _scene_fixture, _scope_fixture


def _expectation(project, producer, output):
    return {
        "generation_id": "op-pdf",
        "project": {"path": str(project)},
        "producer": {"path": str(producer)},
        "output": str(output),
        "color": 1,
        "schematic": None,
    }


def test_confirmed_plan_is_only_pdf_binding_producer(tmp_path):
    grant = tmp_path / "scope.json"
    grant.write_text("fixture")
    scope = NS(granted=True, grant_file=grant, scope_id="fixture")
    expected = {"generation_id": "op-pdf", "fixture": True}
    assert "pdf_export" not in R._binding(scope, "schematic export", "op-pdf")
    bound = R._binding(
        scope, "schematic export", "op-pdf", {"extensions": {"schematic_pdf_artifact": expected}}
    )
    assert bound["pdf_export"] == expected
    bound["pdf_export"]["fixture"] = False
    assert expected["fixture"] is True


@pytest.mark.parametrize(
    "fault", [None, "missing", "payload_changed", "wrong_generation", "boolean_color"]
)
def test_pdf_dispatch_requires_confirmed_exact_plan_and_scope_before_producer(
    scene,
    editing_scope,
    monkeypatch,
    tmp_path,
    fault,  # noqa: F811
):
    project, producer, output = tmp_path / "own.prj", tmp_path / "sch2pdf.exe", tmp_path / "new.pdf"
    project.write_text("fixture")
    producer.write_bytes(b"fixture producer; never executable")
    scene.owner.project = str(project)
    scene.owner.project_open_verified = True
    scene.document = {"open": True, "path": str(project).lower()}
    expected = _expectation(project, producer, output)
    params = {
        "project": str(project),
        "output": str(output),
        "artifact_generation": "op-pdf",
        "_pdf_expectation": copy.deepcopy(expected),
    }
    binding = {
        "command": "schematic export",
        "scope_id": editing_scope["scope_id"],
        "scope_sha256": editing_scope["scope_sha256"],
        "source_operation_id": "op-pdf",
        "pdf_export": expected,
    }
    if fault == "missing":
        binding.pop("pdf_export")
    elif fault == "payload_changed":
        params["_pdf_expectation"]["output"] += ".other"
    elif fault == "wrong_generation":
        params["artifact_generation"] = "op-other"
    elif fault == "boolean_color":
        params["_pdf_expectation"]["color"] = True
    calls = []
    monkeypatch.setattr(
        A, "dispatch", lambda method, data: calls.append((method, data)) or {"exported": True}
    )
    if fault:
        with pytest.raises(D.DispatchError):
            D.dispatch(
                scene.grant,
                "export_pdf",
                params,
                editing_scope=editing_scope,
                sdk_operation_id="op-pdf",
                authorization_binding=binding,
            )
        assert calls == []
    else:
        assert D.dispatch(
            scene.grant,
            "export_pdf",
            params,
            editing_scope=editing_scope,
            sdk_operation_id="op-pdf",
            authorization_binding=binding,
        ) == {"exported": True}
        assert calls[0][0] == "export_pdf"


def _producer_owner(tmp_path, monkeypatch):
    from xpedition_cli.schematic_pdf_artifacts import file_pin

    root = tmp_path / "new-owner"
    directory = root / "commands" / CMD
    directory.mkdir(parents=True)
    home = tmp_path / "sdk"
    exe = home / "common/win64/bin/sch2pdf.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"synthetic-only")
    project = root / "own.prj"
    project.write_bytes(b"synthetic resource fixture, not a CAD database")
    expected = _expectation(project, exe, root / "new.pdf")
    expected["producer"] = file_pin(exe)
    O._command_write(
        directory / "request.json",
        {
            "command_id": CMD,
            "epoch": "a" * 32,
            "command": "adapter",
            "method": "export_pdf",
            "params": {
                "project": str(project),
                "output": expected["output"],
                "artifact_generation": "op-pdf",
                "_pdf_expectation": copy.deepcopy(expected),
            },
            "sdk_operation_id": "op-pdf",
            "authorization_binding": {
                "command": "schematic export",
                "source_operation_id": "op-pdf",
                "pdf_export": expected,
            },
        },
        b"k" * 32,
    )
    owner = O.NativeApplication.__new__(O.NativeApplication)
    owner._owner = lambda: None
    owner._dual_count_probe = owner._dual_document_call = None
    owner._pdf_producer = None
    owner.runtime_root, owner.boundary = root, NS(command_id=CMD)
    owner._origin_handoff, owner.lease = NS(_epoch="a" * 32), NS(home=home)
    monkeypatch.setattr(O, "_context", lambda ticket: (root, b"k" * 32, {}))
    monkeypatch.setattr(
        O.L,
        "_identity",
        lambda handle: {
            "pid": 123,
            "birth_filetime_100ns": "100",
            "executable": "fixture",
            "alive": True,
        },
    )
    monkeypatch.setattr(F, "external_child_environment", lambda env: dict(env))
    monkeypatch.setattr(O.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    return owner, exe, directory


@contextmanager
def _dll_scope():
    yield


@pytest.mark.skipif(os.name != "nt", reason="The real child check owns a Windows Popen HANDLE")
def test_actual_non_cad_child_stays_owned_while_waiting_and_exits_without_kill(
    tmp_path, monkeypatch
):
    """The fixture factory launches Python instead of the fixed fake vendor path.

    This is a real new helper process/resource test, not CAD/producer identity
    qualification. Its private fixture release is test teardown, not cancellation.
    """
    owner, exe, directory = _producer_owner(tmp_path, monkeypatch)
    release = tmp_path / "fixture-release"
    helper = tmp_path / "fixture-child.py"
    helper.write_text(
        "import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\n"
        "while not p.exists(): time.sleep(.01)\n"
        "print('Printed Board1 Sheet 1',flush=True)\n"
    )
    original = O.subprocess.Popen
    launched = threading.Event()
    children, returned = [], []

    def spawn(args, **kwargs):
        assert args == [str(exe)]
        process = original(
            [sys.executable, "-X", "utf8", "-B", str(helper), str(release)], **kwargs
        )
        children.append(process)
        launched.set()
        return process

    monkeypatch.setattr(F, "external_application_dll_search", _dll_scope)
    monkeypatch.setattr(O.subprocess, "Popen", spawn)
    thread = threading.Thread(
        target=lambda: returned.append(owner.run_pdf_producer([str(exe)], exe.parent))
    )
    thread.start()
    try:
        assert launched.wait(3)
        assert thread.is_alive() and children[0].poll() is None
        assert owner._pdf_producer["process"] is children[0]
        assert not owner._pdf_producer["cleanup_complete"]
        assert all(not stream.closed for stream in owner._pdf_producer["streams"])
    finally:
        release.write_text("fixture teardown only")
        thread.join(5)
    assert not thread.is_alive() and children[0].returncode == 0
    assert children[0]._handle is None and owner._pdf_producer is None
    assert returned[0]["stdout"] == b"Printed Board1 Sheet 1\r\n"
    assert returned[0]["owned_producer_terminal"] is True
    assert (directory / "pdf-producer-returned.json").is_file()
    assert len(children) == 1


def test_dll_restore_failure_after_spawn_still_waits_original_child_and_never_reports_success(
    tmp_path, monkeypatch
):
    owner, exe, directory = _producer_owner(tmp_path, monkeypatch)
    calls = []

    @contextmanager
    def broken():
        yield
        raise ValueError("fixture restore failure")

    class Handle:
        def __int__(self):
            return 123

        def Close(self):
            calls.append("handle_closed")

    class Process:
        _handle = Handle()

        def wait(self):
            calls.append("wait_returned")
            return 0

    monkeypatch.setattr(F, "external_application_dll_search", broken)
    monkeypatch.setattr(O.subprocess, "Popen", lambda *a, **k: Process())
    with pytest.raises(O.OwnerError, match="observation_incomplete"):
        owner.run_pdf_producer([str(exe)], exe.parent)
    assert calls == ["wait_returned", "handle_closed"]
    assert owner._pdf_producer is None
    assert not (directory / "pdf-producer-returned.json").exists()


def test_owned_pdf_adapter_path_does_not_use_subprocess_run_or_save(monkeypatch, tmp_path):
    project, home = tmp_path / "own.prj", tmp_path / "sdk"
    project.write_text("fixture")
    exe = home / "common/win64/bin/sch2pdf.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"fixture")
    monkeypatch.setattr(A, "_configure_environment", lambda: home)
    monkeypatch.setattr(A, "_owner_application", lambda: object())
    monkeypatch.setattr(A.subprocess, "run", lambda *a, **k: pytest.fail("No timeout-kill path"))
    monkeypatch.setattr(
        D,
        "run_pdf_producer",
        lambda args, cwd: {
            "returncode": 1,
            "stdout": b"",
            "stderr": b"",
            "owned_producer_terminal": True,
        },
    )
    result = A._export_pdf({"project": str(project), "output": str(tmp_path / "new.pdf")})
    assert result["exported"] is False
    assert result["producer_timing"]["child_budget_seconds"] is None
    assert (
        result["producer_timing"]["deadline_policy"]
        == "bounded_client_observation_no_producer_cancellation"
    )


@pytest.mark.parametrize("mismatch", [False, True])
def test_client_pdf_uses_typed_binding_and_timeout_never_republishes(api, mismatch):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    expected = {"generation_id": OP, "producer": {"path": "C:/fixture/sch2pdf.exe"}}
    binding = {**auth(api), "command": "schematic export", "pdf_export": expected}
    payload = copy.deepcopy(expected)
    if mismatch:
        payload["generation_id"] = "op-forged"
    api.pending = True
    before = len(api.submits)
    with C.bind_owner_routing(lambda *a: locator, OP, binding), pytest.raises(CLIError) as failed:
        C.call_owner(locator, "export_pdf", {"_pdf_expectation": payload}, 0)
    if mismatch:
        assert failed.value.code == "E_FORBIDDEN"
        assert len(api.submits) == before
    else:
        assert failed.value.code == "E_OUTCOME_UNKNOWN"
        assert failed.value.details["owner_command_id"] == CLIENT_CMD
        assert failed.value.details["request_replayed"] is False
        assert len(api.submits) == before + 1


def test_unknown_spawn_outcome_retains_streams_and_never_fakes_cleanup(tmp_path, monkeypatch):
    owner, exe, directory = _producer_owner(tmp_path, monkeypatch)
    calls = []

    class FixtureAbort(BaseException):
        pass

    def unknown(*args, **kwargs):
        calls.append("spawn_once")
        raise OSError("fixture Popen outcome unobservable")

    def retained():
        state = owner._pdf_producer
        assert state["attempted"] is True and state["process"] is None
        assert state["cleanup_complete"] is False
        assert all(not stream.closed for stream in state["streams"])
        calls.append("retained_unknown")
        raise FixtureAbort()

    monkeypatch.setattr(F, "external_application_dll_search", _dll_scope)
    monkeypatch.setattr(O.subprocess, "Popen", unknown)
    monkeypatch.setattr(owner, "_retain_pdf_uncertainty", retained)
    try:
        with pytest.raises(FixtureAbort):
            owner.run_pdf_producer([str(exe)], exe.parent)
        assert calls == ["spawn_once", "retained_unknown"]
        assert owner._pdf_producer is not None
        assert not (directory / "pdf-producer-returned.json").exists()
    finally:
        # Test-only synthetic teardown; no child existed and no product cleanup
        # receipt is minted. This is not cancellation/recovery qualification.
        for stream in owner._pdf_producer["streams"]:
            stream.close()
