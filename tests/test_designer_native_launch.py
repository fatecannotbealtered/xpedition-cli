"""Launch preparation/failure tests; no CAD, COM or native filesystem calls."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import designer_native_launch as N
from xpedition_cli import native_com_adapter as A
from xpedition_cli.backends import NativeBackend

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native-launch boundary")
_READ_BYTES, _IS_FILE = Path.read_bytes, Path.is_file
ENDPOINT = "objref:" + "A" * 64 + ":"
EPOCH = "a" * 32
PLAN = {
    "scheme": N.SCHEME,
    "installation_root": "D:/licensed/XENTP2604/SDD_HOME",
    "runtime_directory": "D:/new-owner/runtime/startup",
}


@pytest.fixture(autouse=True)
def no_native_or_files(monkeypatch):
    hits = []
    # This suite fakes all external configuration; dedicated license tests use
    # the real helper with a synthetic read-only registry.
    monkeypatch.setattr(A, "_inherit_license_environment", lambda: None)

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("A pure launch test attempted external IO or CAD")

    for owner, name in (
        (N.P, "processes"),
        (N.P, "ProcessHandle"),
        (N.subprocess, "Popen"),
        (Path, "read_bytes"),
        (Path, "is_file"),
        (N, "_private_mkdir"),
        (NativeBackend, "invoke"),
        (NativeBackend, "status"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    yield
    assert hits == []


@pytest.mark.parametrize(
    "endpoint", ["Viewdraw.Application", "objref:x:", 'objref:"evil":', "x\nrun"]
)
def test_fixed_script_rejects_untrusted_endpoint_text(endpoint):
    with pytest.raises(N.LaunchError, match="private_objref_moniker_required"):
        N.render_script(endpoint, EPOCH)


def test_fixed_script_only_hands_host_application_to_private_owner():
    body = N.render_script(ENDPOINT, EPOCH).decode("ascii")
    assert "Set hostApplication = Application" in body
    assert ENDPOINT in body and EPOCH in body
    assert "__PRIVATE_OWNER_OBJREF__" not in body
    assert not any(
        value in body for value in ("ExecuteCommand", "GetActiveObject", "Save", "OpenProject")
    )


def test_child_environment_does_not_mutate_inherited_or_change_license_value():
    inherited = {
        "wdir": "old",
        "MGC_WD": "old",
        "Path": "system",
        "MGLS_LICENSE_FILE": "private-fixture-value",
    }
    original = dict(inherited)
    work, inputs = Path("D:/new/work"), Path("D:/new/inputs")
    result = N.child_environment(Path(PLAN["installation_root"]), work, inherited, inputs=inputs)
    assert inherited == original
    assert result["MGLS_LICENSE_FILE"] == original["MGLS_LICENSE_FILE"]
    assert result["MGC_WD"] == str(work)
    assert result["MGC_TMPDIR"] == str(work)
    assert result["XPEDITION_MGC_WD"] == str(work)
    assert result["WDIR"].split(os.pathsep) == [
        str(inputs),
        str(work),
        str(Path(PLAN["installation_root"]) / "standard"),
    ]
    assert "old" not in result["WDIR"]
    assert sum(key.casefold() == "wdir" for key in result) == 1
    assert "MGC_HOME" not in result  # No fabricated installation directory.


@pytest.mark.parametrize("inputs", ["D:/new/work", "D:/unrelated/inputs"])
def test_work_and_inputs_cannot_share_directory_or_escape_the_new_parent(inputs):
    with pytest.raises(N.LaunchError, match="distinct_siblings"):
        N.child_environment(
            Path(PLAN["installation_root"]), Path("D:/new/work"), {}, inputs=Path(inputs)
        )


@pytest.mark.parametrize("bad", ["not_directory", "reparse", "missing_fileid"])
def test_writable_work_identity_rejects_unsafe_or_unobserved_metadata(bad):
    fields = {"st_mode": stat.S_IFDIR, "st_file_attributes": 0, "st_dev": 81, "st_ino": 91}
    fields.update(
        {
            "not_directory": {"st_mode": stat.S_IFREG},
            "reparse": {"st_file_attributes": 0x410},
            "missing_fileid": {"st_ino": 0},
        }[bad]
    )
    path = NS(lstat=lambda: NS(**fields))
    with pytest.raises(N.LaunchError, match="identity_incomplete"):
        N._work_directory_identity(path)


@pytest.mark.parametrize(
    "extra", [{"project": "working.prj"}, {"args": ["-guessed"]}, {"allow": True}]
)
def test_launch_plan_cannot_open_a_project_or_inject_flags(extra):
    with pytest.raises(N.LaunchError, match="fixed_empty_launch_plan_required"):
        N.LaunchLease({**PLAN, **extra}, ENDPOINT, EPOCH)


def test_runtime_is_bound_to_the_fresh_owner_child_without_io():
    owner = Path("D:/new-owner/runtime")
    assert N.constrain_plan_to_owner(PLAN, owner)["runtime_directory"] == str(owner / "startup")
    with pytest.raises(N.LaunchError, match="startup_runtime_must_be_new_owner_child"):
        N.constrain_plan_to_owner(PLAN, Path("D:/old-runtime"))


@pytest.mark.parametrize("stage", ["before-dispatch", "unknown-spawn", "after-spawn"])
def test_failure_preserves_private_lease_and_honest_dispatch_state(monkeypatch, stage):
    marker = object()

    def failing(lease):
        lease.launch_attempted = stage != "before-dispatch"
        lease.process = marker if stage == "after-spawn" else None
        raise OSError("private path not exported")

    monkeypatch.setattr(N.LaunchLease, "launch", failing)
    with pytest.raises(N.LaunchError) as failure:
        N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    assert (
        failure.value.dispatched
        is {"before-dispatch": False, "unknown-spawn": None, "after-spawn": True}[stage]
    )
    assert isinstance(failure.value.lease, N.LaunchLease)
    assert "private path" not in str(failure.value) and ENDPOINT not in str(failure.value)


def test_pending_native_generation_refuses_resource_release():
    lease = N.LaunchLease(PLAN, ENDPOINT, EPOCH)
    lease.process = object()
    lease.worker_pin = NS(exited=lambda: False)
    with pytest.raises(N.LaunchError, match="pending_native_generation_retains"):
        lease.close()
    assert lease._released is False


def test_scene_with_any_designer_refuses_before_launch(monkeypatch):
    monkeypatch.setattr(N.P, "processes", lambda domain: [{"pid": 999}])
    with pytest.raises(N.LaunchError, match="existing_designer_blocks"):
        N._empty_scene()


def test_exit_observation_uses_held_generation_not_a_reused_pid_lookup():
    lease = N.LaunchLease(PLAN, ENDPOINT, EPOCH)
    lease.expected_generation = {"pid": 1, "birth_filetime_100ns": "123", "executable": "fixed"}
    lease.worker_pin = NS(exited=lambda: True)
    lease.launcher_pin = NS(exited=lambda: False)
    result = lease.snapshot()
    assert result["alive"] is False and result["launcher_alive"] is True


def test_complete_lease_release_reaps_original_popen_before_closing_handle():
    lease = N.LaunchLease(PLAN, ENDPOINT, EPOCH)
    observations = []
    lease.process = NS(
        poll=lambda: observations.append("poll") or 0,
        _handle=NS(Close=lambda: observations.append("original_handle_closed")),
    )
    lease.worker_pin = NS(
        exited=lambda: True, close=lambda: observations.append("worker_pin_closed")
    )
    lease.launcher_pin = NS(
        exited=lambda: True, close=lambda: observations.append("launcher_pin_closed")
    )
    lease.close()
    assert observations == [
        "poll",
        "worker_pin_closed",
        "launcher_pin_closed",
        "original_handle_closed",
    ]
    assert lease._released is True


def _launch_inputs_fixture(monkeypatch, *, failure=None):
    """Every OS/native boundary is fake; only the input lease protocol runs."""
    events, retained = [], []
    raw = b"audited standard fixture"
    monkeypatch.setattr(N, "STANDARD_SCRIPTS_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(Path, "read_bytes", lambda self: raw)
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    monkeypatch.setattr(Path, "resolve", lambda self, **kwargs: self)
    monkeypatch.setattr(N, "_plain_path", lambda value: Path(value))
    monkeypatch.setattr(N, "_work_directory_identity", lambda path: [71, 83])
    monkeypatch.setattr(N, "_work_directory_empty", lambda path: True)
    monkeypatch.setattr(
        N,
        "_private_mkdir",
        lambda value: events.append(
            "workspace_created" if Path(value).name == "startup" else "writable_work_created"
        ),
    )
    monkeypatch.setattr(N.P, "processes", lambda domain: [])

    def failed():
        error = N.SealError("private fixture text must not become a reason")
        error.category = "startup_input_security_mismatch"
        error.winerror = 5
        raise error

    class Inputs:
        def __init__(self, workspace):
            assert Path(workspace) == Path(PLAN["runtime_directory"]) / "inputs"
            self.directory = Path(workspace)
            self.resources = object()
            self.released = False
            retained.append(self)
            events.append("input_owner_constructed")

        def prepare(self, payloads):
            events.append("prepare")
            assert set(payloads) == {"identity.vbs", "scripts.ini"}
            assert payloads["scripts.ini"] == b"[ViewDraw]\nScript#0=identity.vbs\n"
            assert ENDPOINT.encode() in payloads["identity.vbs"]
            if failure == "prepare":
                failed()
            return self

        def verify_prelaunch(self):
            events.append("verify")
            if failure == "verify":
                failed()
            return {"input_contract_verified": True}

        def terminal_release(self):
            events.append("input_terminal_release")
            if failure == "release":
                failed()
            self.released = True
            return {
                "resources_released": True,
                "files_removed": False,
                "immutable_files_retained": True,
            }

    def spawn(arguments, **options):
        events.append("spawn")
        work = Path(PLAN["runtime_directory"]) / "work"
        assert options["cwd"] == str(work)
        assert options["cwd"] != str(retained[-1].directory)
        assert options["env"]["MGC_WD"] == options["env"]["MGC_TMPDIR"] == str(work)
        assert options["env"]["WDIR"].split(os.pathsep) == [
            str(retained[-1].directory),
            str(work),
            str(Path(PLAN["installation_root"]) / "standard"),
        ]
        if failure == "spawn":
            raise OSError("unknown spawn outcome, private path")
        return NS(pid=123, poll=lambda: 0, _handle=NS(Close=lambda: events.append("popen_closed")))

    class Pin:
        def __init__(self, domain, pid):
            self.identity = {
                "executable": str(Path(PLAN["installation_root"]) / "common/win64/bin/viewdraw.exe")
            }

        def require_spawned(self, process):
            events.append("original_popen_bound")

        def exited(self):
            return True

        def close(self):
            events.append("pin_closed")

    monkeypatch.setattr(N, "InputsLease", Inputs)
    monkeypatch.setattr(N.subprocess, "Popen", spawn)
    monkeypatch.setattr(N.P, "ProcessHandle", Pin)
    return events, retained


def test_future_launch_uses_sealed_directory_and_verifies_before_dispatch(monkeypatch):
    events, retained = _launch_inputs_fixture(monkeypatch)
    lease = N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    assert lease.inputs is retained[0]
    assert events == [
        "workspace_created",
        "writable_work_created",
        "input_owner_constructed",
        "prepare",
        "verify",
        "spawn",
        "original_popen_bound",
    ]
    assert retained[0].released is False
    assert lease.private_work_binding() == {
        "directory": str(Path(PLAN["runtime_directory"]) / "work"),
        "parent": PLAN["runtime_directory"].replace("/", "\\"),
        "file_id": [71, 83],
        "initially_empty": True,
        "directory_data_pin_held": False,
    }


@pytest.mark.parametrize("failure", ["old_existing", "not_empty", "changed_file_id"])
def test_writable_work_problem_refuses_before_any_cad_spawn(monkeypatch, failure):
    events, retained = _launch_inputs_fixture(monkeypatch)
    if failure == "old_existing":
        original = N._private_mkdir

        def create(path):
            if Path(path).name == "work":
                raise N.LaunchError("new_private_runtime_directory_required")
            return original(path)

        monkeypatch.setattr(N, "_private_mkdir", create)
    elif failure == "not_empty":
        monkeypatch.setattr(N, "_work_directory_empty", lambda path: False)
    else:
        calls = []

        def identity(path):
            calls.append(path)
            return [71, 83] if len(calls) == 1 else [71, 84]

        monkeypatch.setattr(N, "_work_directory_identity", identity)
    with pytest.raises(N.LaunchError) as caught:
        N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    assert caught.value.dispatched is False
    assert "spawn" not in events
    assert caught.value.lease.work_directory.name == "work"
    assert all(value.released is False for value in retained)


@pytest.mark.parametrize("facts", [None, False, {}, {"input_contract_verified": 1}])
def test_unverified_input_result_refuses_before_spawn(monkeypatch, facts):
    events, _ = _launch_inputs_fixture(monkeypatch)
    original = N.InputsLease

    class Inputs(original):
        def verify_prelaunch(self):
            return facts

    monkeypatch.setattr(N, "InputsLease", Inputs)
    with pytest.raises(N.LaunchError) as caught:
        N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    assert caught.value.dispatched is False and "spawn" not in events
    assert caught.value.component_stage == "startup.verify_inputs"
    assert caught.value.component_reason == "startup_input_contract_incomplete"


@pytest.mark.parametrize("stage", ["prepare", "verify"])
def test_partial_input_failure_retains_lease_before_any_dispatch(monkeypatch, stage):
    events, retained = _launch_inputs_fixture(monkeypatch, failure=stage)
    with pytest.raises(N.LaunchError) as caught:
        N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    error = caught.value
    assert error.lease.inputs is retained[0] and retained[0].resources is not None
    assert error.dispatched is False and "spawn" not in events
    assert (
        error.component_stage
        == {"prepare": "startup.create_inputs", "verify": "startup.verify_inputs"}[stage]
    )
    assert error.component_reason == "startup_input_security_mismatch"
    assert error.winerror == 5
    assert retained[0].released is False
    assert "private" not in str(error)


@pytest.mark.parametrize("category", ["a" * 32, {"private": "path"}, None])
def test_bad_input_failure_category_cannot_drop_the_partial_lease(monkeypatch, category):
    def failure(lease):
        error = N.SealError("private fixture text")
        error.category = category
        raise error

    monkeypatch.setattr(N.LaunchLease, "launch", failure)
    with pytest.raises(N.LaunchError) as caught:
        N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    assert isinstance(caught.value.lease, N.LaunchLease)
    assert caught.value.dispatched is False
    assert caught.value.component_reason == "startup_input_contract_incomplete"


def test_unknown_spawn_cannot_release_immutable_input_resources(monkeypatch):
    events, retained = _launch_inputs_fixture(monkeypatch, failure="spawn")
    with pytest.raises(N.LaunchError) as caught:
        N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    assert caught.value.dispatched is None
    assert caught.value.component_stage == "startup.spawn"
    with pytest.raises(N.LaunchError, match="unknown_launch_outcome_retains"):
        caught.value.lease.close()
    assert "input_terminal_release" not in events and retained[0].released is False


def test_native_terminal_is_required_before_input_release(monkeypatch):
    events, retained = _launch_inputs_fixture(monkeypatch)
    lease = N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    with pytest.raises(N.LaunchError, match="pending_native_generation_retains"):
        lease.close()
    assert "input_terminal_release" not in events
    lease.worker_pin = NS(exited=lambda: True, close=lambda: events.append("worker_closed"))
    lease.close()
    assert events.index("input_terminal_release") < events.index("worker_closed")
    assert retained[0].released is True and lease._released is True


def test_terminal_input_release_failure_keeps_process_pins_and_owner(monkeypatch):
    events, retained = _launch_inputs_fixture(monkeypatch, failure="release")
    lease = N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    lease.worker_pin = NS(exited=lambda: True, close=lambda: events.append("worker_closed"))
    with pytest.raises(N.SealError):
        lease.close()
    assert lease._released is False and retained[0].released is False
    assert not {"worker_closed", "pin_closed", "popen_closed"}.intersection(events)


@pytest.mark.parametrize(
    "facts",
    [
        {"resources_released": True},
        {"resources_released": True, "files_removed": True, "immutable_files_retained": True},
        {"resources_released": True, "files_removed": False, "immutable_files_retained": False},
    ],
)
def test_terminal_release_must_keep_immutable_input_files(monkeypatch, facts):
    events, _ = _launch_inputs_fixture(monkeypatch)
    lease = N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    lease.worker_pin = NS(exited=lambda: True, close=lambda: events.append("worker_closed"))
    lease.inputs.terminal_release = lambda: facts
    with pytest.raises(N.LaunchError, match="startup_input_handle_release_incomplete"):
        lease.close()
    assert lease._released is False
    assert not {"worker_closed", "pin_closed", "popen_closed"}.intersection(events)


@pytest.mark.parametrize("scheme", [N.SCHEME, N.PROJECT_SCHEME])
def test_both_launch_profiles_retain_diagnostics_without_granting_identity(scheme):
    lease = N.LaunchLease({**PLAN, "scheme": scheme}, ENDPOINT, EPOCH)
    assert lease.project_path is None  # No initialization or filesystem claim before launch.
    assert lease.project_initialization_observed is None
    body = lease.payload.decode("ascii")
    assert "bootstrap-observation.log" in body
    assert "script_tail_reached" in body
    assert 'CreateObject("Scripting.FileSystemObject")' in body
    assert "GetActiveObject" not in body and "ExecuteCommand" not in body
    assert body.index('Mark("entered"') < body.index('GetObject("objref:')
    assert body.index('GetObject("objref:') < body.index("Set hostApplication = Application")
    assert "__MARKER_PATH__" not in body
    assert len(N.SETUP_FILES) == 4 and all(len(value) == 64 for value in N.SETUP_FILES.values())


def test_diagnostic_marker_cannot_escape_or_inject_script_text():
    for path in ("D:/new/../old/log", 'D:/new/"injected.vbs', "relative.log"):
        with pytest.raises(N.LaunchError, match="private_plain_marker_path_required"):
            N.render_script(ENDPOINT, EPOCH, marker_path=path)


def test_static_library_junction_is_refused_before_descending():
    class Junction:
        def lstat(self):
            return NS(st_file_attributes=0x400, st_mode=0, st_size=0)

        def iterdir(self):
            pytest.fail("The source walk followed a junction")

    source = NS(iterdir=lambda: [Junction()])
    with pytest.raises(N.LaunchError, match="static_library_tree_bound_or_reparse_refused"):
        N._static_library_entries(source)


def test_setup_materialization_uses_only_new_project_and_private_library(tmp_path, monkeypatch):
    # Ordinary temporary files only; Win32/COM/process construction remain forbidden.
    monkeypatch.setattr(Path, "read_bytes", _READ_BYTES)
    monkeypatch.setattr(Path, "is_file", _IS_FILE)
    monkeypatch.setattr(N, "_plain_path", lambda value: Path(value))
    monkeypatch.setattr(N, "_private_mkdir", lambda value: Path(value).mkdir())
    monkeypatch.setattr(N.P, "processes", lambda domain: [])
    home = tmp_path / "sdk/XENTP2604/SDD_HOME"
    source = home / "standard/templates/dxdesigner/systemdesign"
    source.mkdir(parents=True)
    originals = {
        "SystemDesign.prj": (
            b'KEY CentralLibrary "${SDD_HOME}/standard/examples/SystemDesign/SystemDesign.lmc"\n'
        ),
        "SystemDesign/borders.ini": b"fixture-border",
        "SystemDesign/busconts.ini": b"",
        "SystemDesign/speccomp.ini": b"fixture-special-components",
    }
    for relative, raw in originals.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    monkeypatch.setattr(
        N,
        "SETUP_FILES",
        {relative: hashlib.sha256(raw).hexdigest() for relative, raw in originals.items()},
    )
    library = home / "standard/examples/SystemDesign"
    (library / "PartsDBLibs").mkdir(parents=True)
    (library / "SystemDesign.lmc").write_bytes(b"fixture-library-catalog")
    (library / "PartsDBLibs/Resistors.pdb").write_bytes(b"fixture-part-records")
    owner = tmp_path / "new-owner"
    owner.mkdir()
    lease = N.LaunchLease(
        {
            "scheme": N.PROJECT_SCHEME,
            "installation_root": str(home),
            "runtime_directory": str(owner / "startup"),
        },
        ENDPOINT,
        EPOCH,
    )
    target = lease._prepare_new_setup_project()
    assert target == owner / "project/SystemDesign.prj"
    assert str(owner / "project/Library/SystemDesign.lmc").encode() in target.read_bytes()
    assert (
        owner / "project/Library/PartsDBLibs/Resistors.pdb"
    ).read_bytes() == b"fixture-part-records"
    assert all((source / relative).read_bytes() == raw for relative, raw in originals.items())
    manifest = json.loads((target.parent / "setup-materialization.json").read_text())
    assert manifest["source_library_unchanged_after_copy"] is True
    assert manifest["native_database_initialization_observed"] is None
    assert manifest["installed_library_writes_authorized"] is False
