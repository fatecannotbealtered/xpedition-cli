"""Offline launcher/actual-COM generation and owned-chain closure regressions."""

from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest
from test_driver_v2 import _scope, _worker_fixture

from xpedition_cli import native_com_adapter as N
from xpedition_cli import native_process as P
from xpedition_cli import session
from xpedition_cli.cli import sessions as S
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


@pytest.fixture
def startup(monkeypatch, tmp_path):
    home = tmp_path / "installed/SDD_HOME"
    launcher = home / "common/win64/bin/ExpeditionPCB.exe"
    worker = home / "wg/win64/bin/ExpeditionPCB.exe"
    board = tmp_path / "owned/unfixed-board.pcb"
    board.parent.mkdir(parents=True)
    board.write_bytes(b"offline controlled native boundary")
    identities = {
        24552: {
            "pid": 24552,
            "executable": str(launcher),
            "creation_time_100ns": "133000000000000000",
        },
        22220: {
            "pid": 22220,
            "executable": str(worker),
            "creation_time_100ns": "133000000000720000",
        },
    }
    case = NS(
        home=home,
        board=board,
        identities=identities,
        alive={24552: True, 22220: True},
        rows=[
            {"pid": 24552, "parent_pid": 44660, "executable": str(launcher)},
            {"pid": 22220, "parent_pid": 24552, "executable": str(worker)},
        ],
        calls=[],
        launched=False,
        opened=False,
        mode=None,
        identity_count=0,
    )

    def process_identity(domain, pid):
        if domain != "pcb" or int(pid) not in case.identities or not case.alive.get(int(pid)):
            raise OSError(87, "synthetic generation absent")
        return copy.deepcopy(case.identities[int(pid)])

    class Handle:
        def __init__(self, domain, pid, expected_identity=None, **kwargs):
            self.identity = process_identity(domain, pid)
            if expected_identity is not None and not P.same_identity(
                self.identity, expected_identity
            ):
                raise ValueError("synthetic PID reuse")

        def exited(self):
            return not case.alive[self.identity["pid"]]

        def require_spawned(self, process):
            assert process.pid == self.identity["pid"] == 24552
            case.spawn_handle_verified = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(P, "ProcessHandle", Handle)
    monkeypatch.setattr(P, "process_identity", process_identity)
    monkeypatch.setattr(
        P, "processes", lambda domain: copy.deepcopy(case.rows) if case.launched else []
    )
    monkeypatch.setattr(S, "ProcessHandle", Handle)
    monkeypatch.setattr(S, "process_identity", process_identity)
    monkeypatch.setattr(
        S, "processes", lambda domain: copy.deepcopy(case.rows) if case.launched else []
    )

    class Backend:
        def invoke(self, method, params, **kwargs):
            case.calls.append((method, copy.deepcopy(params)))
            if method == "health":
                return {"application_running": case.launched, "designer_application_running": False}
            if method == "start":
                case.launched = True
                with Handle("pcb", 24552) as pinned:
                    seed = P.capture_startup_seed("pcb", pinned)
                case.seed = seed
                return {
                    "started": True,
                    "domain": "pcb",
                    "pid": 24552,
                    "launcher_pid": 24552,
                    "executable": str(launcher),
                    "launcher_identity": copy.deepcopy(identities[24552]),
                    "startup_seed": seed,
                    "automation_ready": True,
                }
            if method == "identity":
                case.identity_count += 1
                if case.mode == "reuse_actual" and case.identity_count == 1:
                    case.identities[22220]["creation_time_100ns"] = "133000000099999999"
                if case.mode == "replace_after_record" and case.identity_count == 4:
                    case.identities[22220]["creation_time_100ns"] = "133000000099999999"
                document = {
                    "open": case.opened,
                    "path": str(board) if case.opened else None,
                    "status": "observed",
                }
                if case.mode == "wrong_project" and case.opened:
                    document["path"] = str(board.with_name("foreign.pcb"))
                return {
                    "application": {
                        "pid": 22220,
                        "domain": "pcb",
                        "public_fields": {"FullName": str(worker)},
                        "document": document,
                    },
                    "adapter": _worker_fixture(),
                }
            if method == "open":
                assert params["pid"] == 22220 and params["start"] is False
                case.opened = True
                return {"opened": True, "project": str(board), "prompts": []}
            if method == "attach":
                return {"attached": True, "domain": "pcb"}
            if method == "snapshot":
                assert params.get("observe_only") is True
                assert params.get("start") is False
                assert params.get("pid") == 22220
                return {
                    "project": str(board),
                    "components": [],
                    "nets": [],
                    "pcb": {"components": [], "nets": [], "tracks": [], "vias": []},
                }
            raise AssertionError(method)

    case.backend = Backend()
    monkeypatch.setattr(S, "native", lambda: case.backend)
    return case


def test_start_returns_and_records_actual_com_worker_not_live_launcher(startup):
    result = S.start(
        {"kind": "pcb", "project": str(startup.board), "_operation_id": "op-new-launch"}
    )
    assert result["pid"] == 22220 and result["launcher_pid"] == 24552
    assert result["process_identity"] == startup.identities[22220]
    assert result["startup_provenance"]["process_chain"][-1]["pid"] == 22220
    recorded = session.read_state()
    assert recorded["state"] == "started" and recorded["pid"] == 22220
    assert recorded["startup_provenance"]["ownership"] == "proven_this_start"
    assert [m for m, _ in startup.calls].count("start") == 1
    assert [m for m, _ in startup.calls].count("open") == 1
    assert "44660" not in str(result["startup_provenance"])


@pytest.mark.parametrize("mode", ["reuse_actual", "replace_after_record", "wrong_project"])
def test_launched_but_unverified_is_retained_with_original_id_without_relaunch(startup, mode):
    startup.mode = mode
    with pytest.raises(CLIError) as caught:
        S.start({"kind": "pcb", "project": str(startup.board), "_operation_id": "op-original"})
    assert caught.value.details["write_attempted"] is True
    recorded = session.read_state()
    assert recorded["state"] == "startup_incomplete"
    assert recorded["startup_operation_id"] == "op-original"
    assert [m for m, _ in startup.calls].count("start") == 1
    if mode == "reuse_actual":
        assert not any(m == "open" for m, _ in startup.calls)


@pytest.mark.parametrize(
    "change",
    [
        "foreign_parent",
        "wrong_installation",
        "parent_reused",
        "duplicate_pid",
        "third_native_writer",
    ],
)
def test_parent_name_or_similar_start_time_cannot_substitute_for_proven_relation(startup, change):
    startup.launched = True
    if change == "foreign_parent":
        startup.rows[1]["parent_pid"] = 9000
    elif change == "wrong_installation":
        startup.identities[22220]["executable"] = str(
            startup.home / "other/win64/bin/ExpeditionPCB.exe"
        )
        startup.rows[1]["executable"] = startup.identities[22220]["executable"]
    elif change == "parent_reused":
        startup.identities[24552]["creation_time_100ns"] = "133000000000999999"
    elif change == "duplicate_pid":
        startup.rows.append(copy.deepcopy(startup.rows[0]))
    else:
        startup.identities[9000] = {**startup.identities[22220], "pid": 9000}
        startup.alive[9000] = True
        startup.rows.append(
            {"pid": 9000, "parent_pid": 8000, "executable": startup.identities[9000]["executable"]}
        )
    with P.ProcessHandle("pcb", 24552) as launcher:
        if change == "duplicate_pid":
            with pytest.raises(N.AdapterError):
                P.capture_startup_seed("pcb", launcher)
            return
        seed = P.capture_startup_seed("pcb", launcher)
    application = startup.backend.invoke("identity", {"domain": "pcb"})["application"]
    with pytest.raises((ValueError, OSError)):
        P.resolve_startup_process("pcb", seed, application)


def test_missing_original_seed_never_adopts_existing_com_process(startup):
    startup.launched = True
    application = startup.backend.invoke("identity", {"domain": "pcb"})["application"]
    with pytest.raises(ValueError):
        P.resolve_startup_process("pcb", {}, application)


def test_observed_child_remains_bound_if_original_launcher_exits_later(startup):
    startup.launched = True
    with P.ProcessHandle("pcb", 24552) as launcher:
        seed = P.capture_startup_seed("pcb", launcher)
    startup.alive[24552] = False
    startup.rows.pop(0)
    application = startup.backend.invoke("identity", {"domain": "pcb"})["application"]
    result = P.resolve_startup_process("pcb", seed, application)
    assert result["process_identity"]["pid"] == 22220
    assert result["startup_provenance"]["launcher_identity"]["pid"] == 24552


def test_unobserved_child_after_launcher_disappears_is_not_adopted(startup):
    startup.launched = True
    original_rows = copy.deepcopy(startup.rows)
    startup.rows = startup.rows[:1]
    with P.ProcessHandle("pcb", 24552) as launcher:
        seed = P.capture_startup_seed("pcb", launcher)
    startup.alive[24552] = False
    startup.rows = original_rows[1:]
    application = startup.backend.invoke("identity", {"domain": "pcb"})["application"]
    with pytest.raises((ValueError, OSError)):
        P.resolve_startup_process("pcb", seed, application)


def test_explicit_attach_does_not_invent_ownership_of_existing_launcher(startup):
    startup.launched = True
    result = S.attach({"kind": "pcb", "pid": 22220, "project": str(startup.board)})
    assert result["pid"] == 22220
    assert result["launcher_ownership"] == "unknown_external_launcher"
    assert session.read_state()["startup_provenance"] is None
    assert P.owned_startup_launchers(None, startup.identities[22220]) == []


def test_reattach_same_owned_generation_preserves_proven_original_launcher(startup):
    S.start({"kind": "pcb", "project": str(startup.board)})
    before = session.read_state()["startup_provenance"]
    result = S.attach({"kind": "pcb", "pid": 22220, "project": str(startup.board)})
    assert result["launcher_ownership"] == "proven_this_start"
    assert session.read_state()["startup_provenance"] == before


def test_attach_signed_generation_mismatch_refuses_before_attachment(startup):
    startup.launched = True
    with pytest.raises(CLIError) as caught:
        S.attach(
            {
                "kind": "pcb",
                "pid": 22220,
                "_expected_process_identity": {
                    **startup.identities[22220],
                    "creation_time_100ns": "999",
                },
            }
        )
    assert caught.value.details["write_attempted"] is False
    assert not any(method == "attach" for method, _ in startup.calls)


def test_recorded_status_rejects_reused_pid_instead_of_reporting_running(startup):
    S.start({"kind": "pcb", "project": str(startup.board)})
    startup.identities[22220]["creation_time_100ns"] = "133000000099999999"
    assert session.status()["state"] == "identity_changed"
    assert session.status()["xpedition_process"] is False


def test_native_start_pins_original_launcher_before_com_wait(startup, monkeypatch):
    events = []
    monkeypatch.setattr(
        N, "_import_com", lambda: (NS(CoInitialize=lambda: None, CoUninitialize=lambda: None), NS())
    )
    monkeypatch.setattr(N, "_heal_gen_py_cache", lambda: None)
    monkeypatch.setattr(N, "processes", lambda domain: [])
    monkeypatch.setattr(
        N,
        "_native_executable",
        lambda domain: (startup.home / "common/win64/bin/ExpeditionPCB.exe", startup.home),
    )

    def popen(*args, **kwargs):
        events.append("Popen")
        startup.launched = True
        return NS(pid=24552)

    monkeypatch.setattr(N.subprocess, "Popen", popen)
    monkeypatch.setattr(N, "_wait_for_native_ready", lambda *args: events.append("wait") or True)
    real_capture = P.capture_startup_seed

    def capture(domain, handle):
        events.append("seed")
        return real_capture(domain, handle)

    monkeypatch.setattr(P, "capture_startup_seed", capture)
    result = N.dispatch("start", {"domain": "pcb"})
    assert events == ["Popen", "seed", "wait", "seed"]
    assert result["launcher_identity"] == startup.identities[24552]
    assert startup.spawn_handle_verified is True
    assert result["startup_seed"]["candidates"][0]["process_chain"][-1]["pid"] == 22220


def test_public_sdk_start_independently_reads_actual_generation_and_original_launch_proof(
    startup, tmp_path, monkeypatch
):
    _scope(tmp_path, monkeypatch)
    args = ["session", "start", "--kind", "pcb", "--project", str(startup.board)]
    with runtime(Adapter(startup.backend), state_dir=tmp_path / "sdk-state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        assert not any(method == "start" for method, _ in startup.calls)
        result = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert result.envelope["ok"], result.envelope
    receipt = result.envelope["data"]["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    facts = receipt["snapshot_after"]["extensions"]["facts"]
    assert facts["os_process_identity"]["pid"] == facts["application"]["pid"] == 22220
    assert facts["startup_provenance"]["launcher_identity"]["pid"] == 24552
    assert [m for m, _ in startup.calls].count("start") == 1


def test_public_sdk_rejects_old_launcher_pid_echo_despite_actual_com_existing(
    startup, tmp_path, monkeypatch
):
    from xpedition_cli import driver

    _scope(tmp_path, monkeypatch)
    original = driver._handler

    def handler(command):
        actual = original(command)
        if command.path != "session start":
            return actual

        def legacy(options):
            result = actual(options)
            result["pid"] = result["launcher_pid"]
            return result

        return legacy

    monkeypatch.setattr(driver, "_handler", handler)
    args = ["session", "start", "--kind", "pcb", "--project", str(startup.board)]
    with runtime(Adapter(startup.backend), state_dir=tmp_path / "sdk-state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        result = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"], result.envelope
    receipt = result.envelope["error"]["details"]["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert [m for m, _ in startup.calls].count("start") == 1


@pytest.mark.parametrize("launcher_ended", [False, True])
def test_close_requires_actual_and_proven_launcher_exit_without_repeating_quit(
    startup, monkeypatch, launcher_ended
):
    S.start({"kind": "pcb", "project": str(startup.board)})
    original = startup.backend.invoke

    def invoke(method, params, **kwargs):
        if method != "close":
            return original(method, params, **kwargs)
        startup.calls.append((method, copy.deepcopy(params)))
        startup.alive[22220] = False
        startup.alive[24552] = not launcher_ended
        return {
            "closed": True,
            "process_absence_observed": True,
            "exit_observation": {"elapsed_seconds": 0.1},
        }

    monkeypatch.setattr(startup.backend, "invoke", invoke)
    monkeypatch.setattr(S, "_stop_domain", lambda options: "pcb")
    monkeypatch.setattr(S, "check_gate", lambda *args: None)
    monkeypatch.setattr(S, "confirmed", lambda *args: None)
    observations = []

    def observe(domain, pid, **kwargs):
        observations.append((domain, int(pid), kwargs["expected_identity"]))
        assert int(pid) == 24552
        return {
            "pid": None if launcher_ended else 24552,
            "process_absence_observed": launcher_ended,
        }

    monkeypatch.setattr(S, "observe_exit", observe)
    options = {
        "kind": "pcb",
        "pid": 22220,
        "save_project": str(startup.board),
        "_operation_id": "op-one-quit",
    }
    if launcher_ended:
        result = S.stop(options)
        assert result["closed"] and result["owned_launch_chain"]["normal_exit_complete"]
        assert session.read_state() == {}
    else:
        with pytest.raises(CLIError) as caught:
            S.stop(options)
        assert caught.value.code == "E_HUMAN_REQUIRED"
        assert caught.value.details["native_result"]["application_process_absence_observed"]
        assert caught.value.details["native_result"]["closed"] is False
        assert session.read_state()["state"] == "closing"
        assert session.read_state()["closing_operation_id"] == "op-one-quit"
        with pytest.raises(CLIError):
            S.stop(options)
    assert len([method for method, _ in startup.calls if method == "close"]) == 1
    assert observations == [("pcb", 24552, startup.identities[24552])]
