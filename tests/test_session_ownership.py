"""Original-ID ownership recovery, with no CAD and no rewritten history."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_driver_v2 import _scope
from test_session_start_generation import startup  # noqa: F401 - shared fake native fixture

from xpedition_cli import driver, session
from xpedition_cli import session_ownership as O
from xpedition_cli.cli import sessions as S
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


@pytest.fixture
def started(startup, tmp_path, monkeypatch):  # noqa: F811 - pytest uses the imported fixture
    _scope(tmp_path, monkeypatch)
    with runtime(Adapter(startup.backend), state_dir=tmp_path / "sdk-state") as cli:
        args = ["session", "start", "--kind", "pcb", "--project", str(startup.board)]
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        applied = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert applied.envelope["ok"], applied.envelope
        receipt = applied.envelope["data"]["receipt"]
        operation_id = receipt["operation_id"]
        record = cli._store_open().get(operation_id)
        actual = copy.deepcopy(startup.identities[22220])
        yield SimpleNamespace(
            startup=startup,
            cli=cli,
            operation_id=operation_id,
            record=record,
            actual=actual,
            scope_id=record["scope_id"],
        )


def _validate(case, record=None, *, actual=None, scope_id=None):
    return O.original_start(
        "pcb",
        actual or case.actual,
        case.operation_id,
        lambda _: copy.deepcopy(case.record if record is None else record),
        case.cli.validator,
        case.scope_id if scope_id is None else scope_id,
    )


def _peer():
    return {
        "state": "attached",
        "domain": "schematic",
        "pid": 5252,
        "process_identity": {
            "pid": 5252,
            "executable": "C:/CAD/Viewdraw.exe",
            "creation_time_100ns": "133000000000999999",
        },
        "startup_provenance": None,
        "session_id": "synthetic-peer-context",
    }


def test_new_verified_start_registers_only_its_original_receipt_pointer(started):
    pointer = O.pointer_for("pcb", started.actual)
    assert pointer is not None
    assert pointer["startup_operation_id"] == started.operation_id
    assert set(pointer) == {"domain", "process_identity", "startup_operation_id", "source_sha256"}
    context, expected = _validate(started)
    assert pointer == expected
    assert (
        context["startup_provenance"]
        == started.record["receipt"]["extensions"]["result"]["startup_provenance"]
    )


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "failed",
        "missing_receipt",
        "missing_record",
        "foreign_scope",
        "plan_id",
        "operation_id",
        "different_command",
        "all_capabilities_changed",
        "can_apply_false",
        "batch_kind",
        "partial_verification",
        "missing_OS_snapshot",
        "different_birth",
        "different_executable",
        "different_PID",
        "missing_seed",
        "forged_parent_chain",
    ],
)
def test_original_start_recovery_rejects_foreign_incomplete_or_tampered_proof(started, change):
    record = copy.deepcopy(started.record)
    actual = copy.deepcopy(started.actual)
    if change in {"unknown", "failed"}:
        record["status"] = change
    elif change == "missing_receipt":
        record["receipt"] = None
    elif change == "missing_record":
        with pytest.raises(CLIError):
            O.original_start(
                "pcb",
                actual,
                started.operation_id,
                lambda _: None,
                started.cli.validator,
                started.scope_id,
            )
        return
    elif change == "foreign_scope":
        with pytest.raises(CLIError):
            _validate(started, scope_id="another-task-scope")
        return
    elif change == "plan_id":
        record["receipt"]["plan_id"] = "plan-different"
    elif change == "operation_id":
        record["receipt"]["operation_id"] = "op-different"
    elif change == "different_command":
        record["command"] = "session attach"
    elif change == "all_capabilities_changed":
        record["capability_id"] = "session-attach"
        record["plan"]["capability_id"] = "session-attach"
        record["receipt"]["capability_id"] = "session-attach"
    elif change == "can_apply_false":
        record["plan"]["can_apply"] = False
    elif change == "batch_kind":
        record["kind"] = "batch"
    elif change == "partial_verification":
        record["receipt"]["outcome"]["verification"]["coverage"] = "partial"
    elif change == "missing_OS_snapshot":
        del record["receipt"]["snapshot_after"]["extensions"]["facts"]["os_process_identity"]
    elif change == "different_birth":
        actual["creation_time_100ns"] = "133999999999999999"
    elif change == "different_executable":
        actual["executable"] = "C:/another-install/ExpeditionPCB.exe"
    elif change == "different_PID":
        actual["pid"] = 7777
    elif change == "missing_seed":
        del record["receipt"]["extensions"]["result"]["startup_seed"]
    else:
        result = record["receipt"]["extensions"]["result"]
        result["startup_provenance"]["parent_edges"][0]["parent_pid"] = 9999
        record["receipt"]["snapshot_after"]["extensions"]["facts"]["startup_provenance"] = (
            copy.deepcopy(result["startup_provenance"])
        )
    with pytest.raises(CLIError):
        _validate(started, record, actual=actual)


def test_registry_digest_detects_validly_shaped_source_receipt_change(started):
    changed = copy.deepcopy(started.record)
    changed["receipt"]["extensions"]["later_note"] = "different source bytes"
    with pytest.raises(CLIError) as caught:
        O.select(
            "pcb",
            started.actual,
            get_record=lambda _: changed,
            validator=started.cli.validator,
            scope_id=started.scope_id,
        )
    assert caught.value.details["lifecycle_diagnostic"]["reason"] == "original_start_changed"


def test_registry_has_a_bound_and_rejects_duplicate_generation(started):
    pointer = O.pointer_for("pcb", started.actual)
    path = O.registry_path()
    path.write_text(json.dumps({"schema_version": 1, "entries": [pointer, pointer]}))
    with pytest.raises(CLIError) as caught:
        O.pointer_for("pcb", started.actual)
    assert caught.value.details["lifecycle_diagnostic"]["reason"] == "registry_ambiguous"
    path.write_text(
        json.dumps({"schema_version": 1, "entries": [pointer] * (O.MAX_OWNED_GENERATIONS + 1)})
    )
    with pytest.raises(CLIError):
        O.pointer_for("pcb", started.actual)


def test_public_restore_original_ID_keeps_peer_state_bytes_and_never_calls_attach_open(started):
    O.registry_path().unlink()
    session._write_state(_peer())
    state_path = session._state_path()
    before = state_path.read_bytes()
    source_before = copy.deepcopy(started.cli._store_open().get(started.operation_id))
    calls_before = len(started.startup.calls)
    args = [
        "session",
        "attach",
        "--kind",
        "pcb",
        "--pid",
        str(started.actual["pid"]),
        "--startup-operation-id",
        started.operation_id,
    ]
    preview = started.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    assert not O.registry_path().exists()
    assert state_path.read_bytes() == before
    applied = started.cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert applied.envelope["ok"], applied.envelope
    receipt = applied.envelope["data"]["receipt"]
    result = receipt["extensions"]["result"]
    assert result["ownership_recovered"] is True
    assert result["native_attach_dispatched"] is False
    assert result["native_open_dispatched"] is False
    assert result["active_session_context_changed"] is False
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert state_path.read_bytes() == before
    assert started.cli._store_open().get(started.operation_id) == source_before
    calls = started.startup.calls[calls_before:]
    assert calls and {method for method, _params in calls} == {"identity"}
    assert O.pointer_for("pcb", started.actual)["startup_operation_id"] == started.operation_id


def test_restore_project_conflict_is_rejected_before_any_native_mutation(started):
    before = len(started.startup.calls)
    result = started.cli.run(
        [
            "session",
            "attach",
            "--kind",
            "pcb",
            "--pid",
            str(started.actual["pid"]),
            "--startup-operation-id",
            started.operation_id,
            "--project",
            str(started.startup.board),
            "--dry-run",
        ]
    )
    assert not result.envelope["ok"]
    assert result.envelope["error"]["code"] == "E_USAGE"
    assert not any(
        method in {"attach", "open", "start", "close"}
        for method, _params in started.startup.calls[before:]
    )


@pytest.mark.parametrize(
    "change",
    [
        "deleted_pointer",
        "forged_pointer",
        "native_attach_flag",
        "native_open_flag",
        "ownership_recovered_flag",
        "wrong_source_ID",
        "peer_context_changed",
        "source_changed_after_handler",
        "foreign_document_after_register",
    ],
)
def test_public_restore_independent_verification_rejects_missing_or_forged_effects(
    started, monkeypatch, change
):
    O.registry_path().unlink()
    session._write_state(_peer())
    original_record = copy.deepcopy(started.cli._store_open().get(started.operation_id))
    original_handler = driver._handler
    original_get = started.cli.adapter.ownership_record_get
    changed_source = [False]

    def get_record(operation_id):
        record = original_get(operation_id)
        if changed_source[0] and operation_id == started.operation_id:
            record = copy.deepcopy(record)
            record["receipt"]["extensions"]["changed_after_handler"] = True
        return record

    monkeypatch.setattr(started.cli.adapter, "ownership_record_get", get_record)

    def handler(command):
        actual = original_handler(command)
        if command.path != "session attach":
            return actual

        def altered(options):
            result = actual(options)
            if change == "deleted_pointer":
                O.registry_path().unlink()
            elif change == "forged_pointer":
                value = json.loads(O.registry_path().read_text())
                value["entries"][0]["source_sha256"] = "0" * 64
                O.registry_path().write_text(json.dumps(value))
            elif change == "native_attach_flag":
                result["native_attach_dispatched"] = True
            elif change == "native_open_flag":
                result["native_open_dispatched"] = True
            elif change == "ownership_recovered_flag":
                result["ownership_recovered"] = False
            elif change == "wrong_source_ID":
                result["startup_operation_id"] = "op-another-source"
            elif change == "peer_context_changed":
                value = session.read_state()
                value["requested_project"] = "C:/synthetic-private/changed.prj"
                session._write_state(value)
            elif change == "foreign_document_after_register":
                started.startup.mode = "wrong_project"
            else:
                changed_source[0] = True
            return result

        return altered

    monkeypatch.setattr(driver, "_handler", handler)
    args = [
        "session",
        "attach",
        "--kind",
        "pcb",
        "--pid",
        str(started.actual["pid"]),
        "--startup-operation-id",
        started.operation_id,
    ]
    preview = started.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    applied = started.cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not applied.envelope["ok"], applied.envelope
    receipt = applied.envelope["error"]["details"].get("receipt")
    assert not receipt or receipt["outcome"]["verification"]["status"] != "passed"
    assert started.cli._store_open().get(started.operation_id) == original_record


def test_cross_domain_selector_and_close_cleanup_preserve_peer_record(started, monkeypatch):
    peer = _peer()
    session._write_state(peer)
    context, pointer = started.cli.adapter._ownership("pcb", started.actual)
    assert context["domain"] == "pcb"
    assert pointer["startup_operation_id"] == started.operation_id
    before = copy.deepcopy(started.cli._store_open().get(started.operation_id))
    # Model the existing handler's successful normal Close/Quit and strict OS
    # result. No GUI, COM or termination API is used by this fixture.
    monkeypatch.setattr(S, "_stop_domain", lambda _: "pcb")
    monkeypatch.setattr(S, "check_gate", lambda *args: None)
    monkeypatch.setattr(S, "confirmed", lambda *args: None)
    monkeypatch.setattr(
        S, "_observe_owned_launchers", lambda *args, **kwargs: {"normal_exit_complete": True}
    )
    started.startup.opened = False
    original = started.startup.backend.invoke

    def invoke(method, params, **kwargs):
        if method == "close":
            return {"closed": True, "process_absence_observed": True}
        return original(method, params, **kwargs)

    monkeypatch.setattr(started.startup.backend, "invoke", invoke)
    result = S.stop(
        {
            "kind": "pcb",
            "pid": started.actual["pid"],
            "_operation_id": "op-normal-quit",
            "_ownership_resolver": lambda domain, actual: started.cli.adapter._ownership(
                domain, actual
            ),
        }
    )
    assert result["closed"] is True
    assert session.read_state() == peer
    assert O.pointer_for("pcb", started.actual) is None
    assert started.cli._store_open().get(started.operation_id) == before


def test_pending_close_blocks_restore_and_does_not_replace_original_ID(started):
    session.record_native_closing("pcb", started.actual, operation_id="op-original-quit")
    before = session._state_path().read_bytes()
    with pytest.raises(CLIError) as caught:
        O.select(
            "pcb",
            started.actual,
            source_id=started.operation_id,
            get_record=started.cli._store_open().get,
            validator=started.cli.validator,
            scope_id=started.scope_id,
        )
    assert caught.value.details["lifecycle_diagnostic"]["reason"] == "original_close_pending"
    assert session._state_path().read_bytes() == before


def test_public_close_verifies_Layout_OS_exit_after_restoring_Designer_peer(started, monkeypatch):
    import ctypes

    from test_native_exit_observation import Function

    from xpedition_cli import native_process

    # Exercise the real Windows observer against the injected kernel API, not
    # the host Linux platform gate. Do not alter the process-wide os module.
    monkeypatch.setattr(native_process, "os", SimpleNamespace(name="nt", path=os.path))

    peer = _peer()
    session._write_state(peer)
    started.startup.opened = False
    original = started.startup.backend.invoke
    close_count = []
    post_quit_COM = []

    def raw_open(access, inherit, pid):
        assert access == 0x00101000 and inherit is False
        assert pid in started.startup.identities
        return pid

    def raw_times(handle, creation, *unused):
        birth = int(started.startup.identities[handle]["creation_time_100ns"])
        creation._obj.dwHighDateTime, creation._obj.dwLowDateTime = birth >> 32, birth & 0xFFFFFFFF
        return True

    def raw_image(handle, flags, buffer, size):
        buffer.value = started.startup.identities[handle]["executable"]
        return True

    api = SimpleNamespace(
        OpenProcess=Function(raw_open),
        GetProcessId=Function(lambda handle: handle),
        GetProcessTimes=Function(raw_times),
        WaitForSingleObject=Function(
            lambda handle, timeout: 258 if started.startup.alive[handle] else 0
        ),
        QueryFullProcessImageNameW=Function(raw_image),
        CloseHandle=Function(lambda handle: True),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: api, raising=False)

    def invoke(method, params, **kwargs):
        if method == "close":
            close_count.append(1)
            assert params["pid"] == started.actual["pid"]
            started.startup.alive[22220] = False
            started.startup.alive[24552] = False
            return {
                "closed": True,
                "process_absence_observed": True,
                "process_identity": started.actual,
                "lifecycle": {
                    "empty_document_observed": True,
                    "quit_count": 1,
                    "quit_dispatched": True,
                    "quit_reply_received": True,
                    "save_count": 0,
                    "close_count": 0,
                },
            }
        if close_count and method == "identity":
            post_quit_COM.append(1)
            pytest.fail("Quit completion must use the original Layout OS generation")
        return original(method, params, **kwargs)

    monkeypatch.setattr(started.startup.backend, "invoke", invoke)
    args = ["session", "close", "--kind", "pcb", "--pid", str(started.actual["pid"])]
    preview = started.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    applied = started.cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert applied.envelope["ok"], applied.envelope
    receipt = applied.envelope["data"]["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert (
        receipt["snapshot_after"]["extensions"]["facts"]["application"]["process_identity"]
        == started.actual
    )
    assert session.read_state() == peer
    assert close_count == [1]
    assert post_quit_COM == []


def test_failed_new_start_pointer_registration_preserves_successful_original_receipt(
    startup,  # noqa: F811 - pytest uses the imported fixture
    tmp_path,
    monkeypatch,
):
    _scope(tmp_path, monkeypatch)

    def fail(_pointer):
        raise CLIError("E_IO", "synthetic registry storage failure")

    monkeypatch.setattr(O, "register", fail)
    with runtime(Adapter(startup.backend), state_dir=tmp_path / "sdk-state") as cli:
        args = ["session", "start", "--kind", "pcb", "--project", str(startup.board)]
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        applied = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert applied.envelope["ok"], applied.envelope
        registration = applied.envelope["data"]["extensions"]["ownership_registration"]
        assert registration["status"] == "failed"
        assert registration["reason"] == "ownership_pointer_not_retained"
        assert registration["recovery_required"] is True
        assert any("ownership pointer was not retained" in message for message in cli._warnings)
        record = cli._store_open().get(registration["startup_operation_id"])
        assert record["status"] == "succeeded"
        assert record["receipt"]["outcome"]["verification"]["status"] == "passed"
        assert record["receipt"]["outcome"]["execution"] == "completed"
        assert [method for method, _params in startup.calls].count("start") == 1
        assert not O.registry_path().exists()
        assert applied.envelope["data"]["receipt"] == record["receipt"]
        with pytest.raises(CLIError) as caught:
            cli.adapter._ownership("pcb", startup.identities[22220])
        assert (
            caught.value.details["lifecycle_diagnostic"]["reason"]
            == "ownership_pointer_missing_recovery_required"
        )


def test_full_registry_refuses_before_launch(startup, tmp_path, monkeypatch):  # noqa: F811
    _scope(tmp_path, monkeypatch)
    entries = []
    for index in range(O.MAX_OWNED_GENERATIONS):
        entries.append(
            {
                "domain": "pcb",
                "process_identity": {
                    **startup.identities[22220],
                    "pid": 1000 + index,
                },
                "startup_operation_id": "op-synthetic-" + str(index),
                "source_sha256": "0" * 64,
            }
        )
    O.registry_path().write_text(json.dumps({"schema_version": 1, "entries": entries}))
    with runtime(Adapter(startup.backend), state_dir=tmp_path / "sdk-state") as cli:
        result = cli.run(
            ["session", "start", "--kind", "pcb", "--project", str(startup.board), "--dry-run"]
        )
    assert not result.envelope["ok"]
    assert (
        result.envelope["error"]["details"]["lifecycle_diagnostic"]["reason"]
        == "registry_generation_limit"
    )
    assert startup.calls == []


def test_oversized_registry_is_rejected_before_opening_its_contents(monkeypatch):
    path = O.registry_path()
    path.write_bytes(b" " * (O.MAX_REGISTRY_BYTES + 1))
    original = Path.open

    def guarded_open(self, *args, **kwargs):
        if self == path:
            pytest.fail("An over-limit registry must not be opened or allocated")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(CLIError) as caught:
        O._read()
    assert caught.value.details["lifecycle_diagnostic"]["reason"] == "registry_size_limit"


def test_registry_OS_lock_refuses_peer_and_is_released_on_process_exit(tmp_path):
    marker, release = tmp_path / "locked", tmp_path / "release"
    script = """
import os, sys, time
from pathlib import Path
from xpedition_cli import session_ownership as O
with O._locked():
    Path(sys.argv[1]).write_text('locked')
    deadline = time.monotonic() + 5
    while not Path(sys.argv[2]).exists() and time.monotonic() < deadline:
        time.sleep(.01)
    os._exit(0)
"""
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(marker), str(release)],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    deadline = time.monotonic() + 4
    try:
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        with pytest.raises(CLIError) as caught, O._locked():
            pytest.fail("A concurrently held OS lock must not be acquired")
        assert caught.value.details["lifecycle_diagnostic"]["reason"] == "registry_busy"
    finally:
        release.write_text("release")
        child.wait(timeout=7)
    assert child.returncode == 0, child.stderr.read().decode()
    child.stderr.close()
    with O._locked():
        assert O.registry_path().with_suffix(".lock").exists()


def test_old_crash_temporary_files_are_preserved_without_blocking_registration(started):
    pointer = O.pointer_for("pcb", started.actual)
    old_fixed = O.registry_path().with_suffix(".tmp")
    old_unique = O.registry_path().with_name("native-ownership.old-crash.tmp")
    old_fixed.write_bytes(b"old fixed temporary evidence")
    old_unique.write_bytes(b"old unique temporary evidence")
    before = [path.read_bytes() for path in (old_fixed, old_unique)]
    O.register(pointer)
    assert O.pointer_for("pcb", started.actual) == pointer
    assert [path.read_bytes() for path in (old_fixed, old_unique)] == before


@pytest.mark.parametrize("phase", ["before_temp", "after_replace"])
def test_registry_IO_failure_distinguishes_state_write_from_native_write(
    started, monkeypatch, phase
):
    pointer = O.pointer_for("pcb", started.actual)
    # Existing equivalent registrations are now read-only. Exercise a genuine
    # new pointer publication so these IO stages remain meaningful.
    O.remove("pcb", started.actual)
    original_open = Path.open
    original_read = O._bounded_read
    reads = []

    def fail_open(self, *args, **kwargs):
        if args and args[0] == "xb" and self.suffix == ".tmp":
            raise OSError("synthetic-private-path")
        return original_open(self, *args, **kwargs)

    def fail_read(path):
        reads.append(path)
        if len(reads) > 1:
            raise OSError("synthetic-private-path")
        return original_read(path)

    if phase == "before_temp":
        monkeypatch.setattr(Path, "open", fail_open)
    else:
        monkeypatch.setattr(O, "_bounded_read", fail_read)
    with pytest.raises(CLIError) as caught:
        O.register(pointer)
    details = caught.value.details
    assert caught.value.code == "E_IO"
    assert details["native_write_attempted"] is False
    assert details["state_write_attempted"] is (phase == "after_replace")
    assert details["write_attempted"] is (phase == "after_replace")
    assert "synthetic-private-path" not in json.dumps(details)


def test_public_restore_state_write_failure_retains_dispatch_and_original_start(
    started, monkeypatch
):
    O.registry_path().unlink()
    session._write_state(_peer())
    source_before = copy.deepcopy(started.cli._store_open().get(started.operation_id))
    original = O.register

    def failed_ack(pointer):
        original(pointer)
        raise CLIError(
            "E_IO",
            "Metadata write acknowledgement unavailable",
            {
                "write_attempted": True,
                "state_write_attempted": True,
                "native_write_attempted": False,
            },
        )

    monkeypatch.setattr(O, "register", failed_ack)
    args = [
        "session",
        "attach",
        "--kind",
        "pcb",
        "--pid",
        str(started.actual["pid"]),
        "--startup-operation-id",
        started.operation_id,
    ]
    preview = started.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    applied = started.cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not applied.envelope["ok"], applied.envelope
    details = applied.envelope["error"]["details"]
    assert details["dispatch_state"] == "dispatched"
    assert details["receipt"]["operation_id"] != started.operation_id
    cause = details["receipt"]["extensions"]["handler_error"]["details"]
    assert cause["state_write_attempted"] is True
    assert cause["native_write_attempted"] is False
    assert started.cli._store_open().get(started.operation_id) == source_before
