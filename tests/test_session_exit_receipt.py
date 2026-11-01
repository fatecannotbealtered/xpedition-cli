"""Public close receipts: COM unregistering and process exit are distinct facts."""

import ctypes
from types import SimpleNamespace

import pytest
from test_driver_v2 import _scope, _worker_fixture
from test_native_exit_observation import Function

from xpedition_cli import driver, native_process, session
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


@pytest.mark.parametrize("exit_state", ["delayed", "other_instance", "still_running", "unreadable"])
def test_public_close_observes_bounded_process_exit_without_repeating_quit(
    tmp_path, monkeypatch, exit_state
):
    _scope(tmp_path, monkeypatch)
    dispatched = []
    identity_after_dispatch = []
    reads = []
    clock = [0.0]
    # Model elapsed time without a wall-clock wait or any real CAD process.
    monkeypatch.setattr(
        native_process,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ),
        raising=False,
    )

    def processes(domain):
        assert domain == "pcb"
        if not dispatched:
            return [{"pid": 4242}]
        reads.append(clock[0])
        if exit_state == "unreadable":
            raise OSError("Synthetic process enumeration failure")
        if exit_state == "still_running" or len(reads) < 3:
            return [{"pid": 4242}]
        return [{"pid": 9000}] if exit_state == "other_instance" else []

    monkeypatch.setattr(native_process, "processes", processes)
    exact_process = {
        "pid": 4242,
        "executable": "D:/licensed/expeditionpcb.exe",
        "creation_time_100ns": "131234567890000000",
    }
    pinned_pids = []

    class ProcessHandle:
        def __init__(self, domain, pid, expected_identity=None, *, check_name=True):
            assert domain == "pcb"
            pinned_pids.append(pid)
            assert pid == 4242, "Another user's CAD instance must not be inspected for exit"
            self.identity = dict(exact_process)
            if expected_identity is not None:
                assert native_process.same_identity(self.identity, expected_identity)

        def exited(self):
            if not dispatched:
                return False
            reads.append(clock[0])
            if exit_state == "unreadable":
                raise OSError("Synthetic exact-process wait failure")
            return exit_state != "still_running" and len(reads) >= 3

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(native_process, "ProcessHandle", ProcessHandle)

    def raw_open(access, inherit, pid):
        assert access == 0x00101000 and inherit is False and pid == 4242
        pinned_pids.append(pid)
        return pid

    def raw_times(handle, creation, *unused):
        assert handle == 4242
        birth = int(exact_process["creation_time_100ns"])
        creation._obj.dwHighDateTime, creation._obj.dwLowDateTime = birth >> 32, birth & 0xFFFFFFFF
        return True

    def raw_wait(handle, milliseconds):
        assert handle == 4242 and milliseconds == 0
        if not dispatched:
            return 258
        reads.append(clock[0])
        if exit_state == "unreadable":
            return 0xFFFFFFFF
        return 0 if exit_state != "still_running" and len(reads) >= 3 else 258

    def raw_image(handle, flags, buffer, size):
        assert handle == 4242
        buffer.value = exact_process["executable"]
        return True

    api = SimpleNamespace(
        OpenProcess=Function(raw_open),
        GetProcessId=Function(lambda handle: handle),
        GetProcessTimes=Function(raw_times),
        WaitForSingleObject=Function(raw_wait),
        QueryFullProcessImageNameW=Function(raw_image),
        CloseHandle=Function(lambda handle: True),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: api, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    session.record_native_attach({"domain": "pcb", "pid": 4242, "process_identity": exact_process})

    class Backend:
        def invoke(self, method, params, **kwargs):
            assert method == "identity"
            if dispatched:
                identity_after_dispatch.append(True)
                raise CLIError("E_NOT_FOUND", "COM already unregistered")
            return {"application": {"pid": 4242}, "adapter": _worker_fixture()}

    def close(options):
        if options.get("dry_run"):
            return {"confirm_token": "synthetic-close", "changes": []}
        assert options["_expected_process_identity"] == exact_process
        assert options["_operation_id"].startswith("op-")
        dispatched.append(True)
        return {
            "closed": True,
            "domain": "pcb",
            "process_identity": exact_process,
            "lifecycle": {
                "save_count": 0,
                "close_count": 0,
                "quit_count": 1,
                "empty_document_observed": True,
                "quit_dispatched": True,
                "quit_reply_received": True,
            },
        }

    monkeypatch.setattr(driver, "_handler", lambda command: close)
    args = ["session", "close", "--kind", "pcb", "--pid", "4242", "--dangerous"]
    with runtime(Adapter(Backend()), state_dir=tmp_path / "state") as cli:
        plan = cli.run([*args, "--dry-run"])
        assert plan.envelope["ok"], plan.envelope
        result = cli.run([*args, "--confirm", plan.envelope["data"]["confirm_token"]])
        if exit_state in {"delayed", "other_instance"}:
            assert result.envelope["ok"], result.envelope
            receipt = result.envelope["data"]["receipt"]
            assert receipt["outcome"]["verification"]["status"] == "passed"
            facts = receipt["snapshot_after"]["extensions"]["facts"]
            assert facts["application"]["process_absence_observed"]
            assert facts["application"]["exit_observation"]["observations"] == 3
        else:
            assert not result.envelope["ok"], result.envelope
            receipt = result.envelope["error"]["details"]["receipt"]
            assert receipt["outcome"]["verification"]["status"] != "passed"
        assert dispatched == [True]
        assert reads and clock[0] <= native_process.EXIT_OBSERVATION_SECONDS
        assert set(pinned_pids) == {4242}
        assert not identity_after_dispatch, "Do not reattach to a quitting COM server"


def test_close_same_pid_with_another_creation_time_refuses_before_quit(tmp_path, monkeypatch):
    _scope(tmp_path, monkeypatch)
    generation = ["131234567890000000"]
    dispatched = []
    monkeypatch.setattr(
        native_process,
        "process_identity",
        lambda domain, pid: {
            "pid": int(pid),
            "executable": "D:/licensed/expeditionpcb.exe",
            "creation_time_100ns": generation[0],
        },
    )
    session.record_native_attach(
        {
            "domain": "pcb",
            "pid": 4242,
            "process_identity": native_process.process_identity("pcb", 4242),
        }
    )

    class Backend:
        def invoke(self, method, params, **kwargs):
            assert method == "identity"
            return {"application": {"pid": 4242}, "adapter": _worker_fixture()}

    def close(options):
        if options.get("dry_run"):
            return {"confirm_token": "synthetic-close", "changes": []}
        dispatched.append(True)
        pytest.fail("A reused PID must not receive Quit")

    monkeypatch.setattr(driver, "_handler", lambda command: close)
    args = ["session", "close", "--kind", "pcb", "--pid", "4242", "--dangerous"]
    with runtime(Adapter(Backend()), state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dry-run", "--quiet"])
        assert planned.envelope["ok"], planned.envelope
        generation[0] = "131234567899999999"
        refused = cli.run(
            [*args, "--confirm", planned.envelope["data"]["confirm_token"], "--quiet"]
        )
    assert refused.envelope["error"]["code"] == "E_CONFLICT", refused.envelope
    assert refused.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert dispatched == []
