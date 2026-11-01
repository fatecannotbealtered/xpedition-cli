"""New temporary MAC fixtures only; no CAD, COM or real process API is permitted."""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import designer_owner_recovery as R
from xpedition_cli import resources
from xpedition_cli._vendor.tool_driver_sdk.scope import ScopeResolver
from xpedition_cli.backends import NativeBackend
from xpedition_cli.errors import CLIError


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        raise AssertionError("No real native/COM/process API is allowed in this suite")

    for name in ("WinDLL", "OleDLL"):
        monkeypatch.setattr(ctypes, name, forbidden, raising=False)
    for name in ("invoke", "status"):
        monkeypatch.setattr(NativeBackend, name, forbidden)
    monkeypatch.setattr(R.L, "_plain_path", lambda value: Path(value))
    yield
    assert hits == []


@pytest.fixture
def scene(tmp_path, monkeypatch):
    config = tmp_path / "isolated-config"
    config.mkdir()
    operation = "op-" + "a" * 32
    owner_id, epoch = "b" * 32, "c" * 32
    root = config.parent / (config.name + "-designer-runtime") / ("owner-" + owner_id)
    root.mkdir(parents=True)
    grant = config / "scope.json"
    grant.write_text(
        json.dumps(
            {
                "scope_id": "fresh-recovery-fixture",
                "tool": "xpedition-cli",
                "effects": ["session"],
                "dangerous": False,
                "roots": [],
                "outputs": [],
                "destinations": [],
                "expires_at": None,
            }
        )
    )
    calls = []

    class Validator:
        def validate(self, kind, value):
            calls.append(kind)
            assert kind in {"scope", "plan", "receipt"} and isinstance(value, dict)

        def receipt_rule(self, receipt):
            return {"ok": False}

    validator = Validator()
    scope = ScopeResolver(
        "xpedition-cli",
        validator=validator,
        environ={
            "XPEDITION_CLI_SCOPE_FILE": str(grant),
        },
    ).resolve()
    source = {"old-owner.py": "d" * 64}  # Historical hash deliberately differs from today's code.
    reference = {
        "protocol": "designer-owned-session-v1",
        "owner_id": owner_id,
        "owner_epoch_sha256": hashlib.sha256(epoch.encode()).hexdigest(),
        "source_bundle_sha256": R._digest(source),
    }
    editing = {
        "scope_file": str(grant.resolve()),
        "scope_sha256": hashlib.sha256(grant.read_bytes()).hexdigest(),
        "scope_id": scope.scope_id,
    }
    plan = {
        "scheme": "designer-native-empty-launch-v1",
        "installation_root": "C:/fixture/SDD_HOME",
        "runtime_directory": str(root / "startup"),
    }
    pending = {
        "protocol": reference["protocol"],
        "sdk_operation_id": operation,
        "owner_id": owner_id,
        "root": str(root),
        "plan": plan,
        "source_binding": source,
        "editing_scope": editing,
        "authority_granted": False,
        "original_outcome": "unknown",
    }
    for path, value in (
        (R.pending_start_path(operation, config), pending),
        (
            R._locator_path(reference, config),
            {"root": str(root), "source_binding": source, "editing_scope": editing},
        ),
    ):
        path.parent.mkdir()
        path.write_text(json.dumps(value))
    key = b"k" * 32
    (root / "integrity-key.bin").write_bytes(key)
    intent = {
        "component": "designer-dedicated-native-owner-measurement-v1",
        "execution_id": "designer-owner-" + "e" * 32,
        "execution_epoch": epoch,
        "mode": "confirmed-public-owner-v1",
        "locator_sha256": R.L._locator(root),
        "source_sha256": source,
        "editing_scope": editing,
        "plan": plan,
    }
    owner = {
        "pid": 101,
        "birth_filetime_100ns": "10",
        "executable": "C:/fixture/python.exe",
        "alive": True,
    }
    launcher = {
        "pid": 102,
        "creation_time_100ns": "20",
        "executable": "C:/fixture/SDD_HOME/common/win64/bin/viewdraw.exe",
    }
    worker = {
        "pid": 103,
        "creation_time_100ns": "30",
        "executable": "C:/fixture/SDD_HOME/wv/win64/bin/viewdraw.exe",
    }
    seed = {
        "version": R.STARTUP_LINEAGE_VERSION,
        "domain": "schematic",
        "launcher_identity": launcher,
        "candidates": [
            {"process_chain": [launcher, worker], "parent_edges": [{"pid": 103, "parent_pid": 102}]}
        ],
    }
    launch = {
        "authorized_project": None,
        "startup_seed": seed,
        "startup_provenance": seed,
        "expected_native_generation": {
            "pid": 103,
            "birth_filetime_100ns": "30",
            "executable": worker["executable"],
        },
    }
    for name, value in (
        ("intent.json", intent),
        ("owner.json", {"epoch": epoch, "owner": owner}),
        ("native-launch-evidence.json", launch),
    ):
        (root / name).write_bytes(R.L._pack(value, key))
    declared = json.loads(
        resources.locate("contract/driver-v2/product_contract.json").read_text(encoding="utf-8")
    )
    capability = next(
        row["driver"]["capability_id"]
        for row in declared["reference"]["commands"]
        if row["path"] == "session start"
    )
    target_path = config / "launch-intents/schematic.json"
    target = {
        "mode": "file",
        "engine_id": "xpedition-cli-files",
        "instance_id": None,
        "object_id": str(target_path),
        "locator": target_path.as_uri(),
        "extensions": {
            "path": str(target_path),
            "command": "session start",
            "domain": "schematic",
            "input_paths": [],
        },
    }
    common = {
        "operation_id": operation,
        "plan_id": "plan-fixture",
        "capability_id": capability,
        "authorization_scope_id": scope.scope_id,
        "effects": ["session"],
        "target": target,
    }
    result = {
        "domain": "schematic",
        "owner_ref": reference,
        "requested_project": None,
        "startup_operation_id": operation,
        "attached": False,
    }
    record = {
        "operation_id": operation,
        "plan_id": "plan-fixture",
        "capability_id": capability,
        "scope_id": scope.scope_id,
        "effects": ["session"],
        "command": "session start",
        "kind": "single",
        "status": "unknown",
        "resolution": None,
        "plan": {**common, "can_apply": True, "base_snapshot": {"id": "before"}},
        "receipt": {
            **common,
            "snapshot_before": {"id": "before"},
            "extensions": {"result": result},
            "outcome": {"change": "unknown", "verification": {"status": "incomplete"}},
        },
    }
    state = {
        "domain": "schematic",
        "state": "startup_incomplete",
        "pid": None,
        "startup_operation_id": operation,
        "owner_ref": reference,
        "requested_project": None,
    }
    current = {
        row["pid"]: {
            **row,
            "birth_filetime_100ns": row.get("birth_filetime_100ns", row.get("creation_time_100ns")),
            "alive": False,
        }
        for row in (owner, launcher, worker)
    }
    opened, closed = [], []

    def open_process(access, inherit, pid):
        assert access == 0x00101000 and inherit is False
        opened.append(pid)
        return pid

    def process_times(handle, creation, *_rest):
        value = int(current[handle]["birth_filetime_100ns"])
        creation._obj.dwHighDateTime = value >> 32
        creation._obj.dwLowDateTime = value & 0xFFFFFFFF
        return True

    kernel = NS(
        OpenProcess=open_process,
        GetProcessId=lambda handle: handle,
        GetProcessTimes=process_times,
        WaitForSingleObject=lambda handle, timeout: 0,
        CloseHandle=lambda handle: closed.append(handle) or True,
    )
    monkeypatch.setattr(R.L, "_kernel", lambda: kernel)
    monkeypatch.setattr(R.L, "_identity", lambda handle: copy.deepcopy(current[handle]))
    case = NS(
        config=config,
        root=root,
        key=key,
        intent=intent,
        launch=launch,
        source=source,
        reference=reference,
        operation=operation,
        scope=scope,
        validator=validator,
        state=state,
        record=record,
        current=current,
        opened=opened,
        closed=closed,
        kernel=kernel,
    )
    case.observe = lambda **kwargs: R.observe_exited_failed_start(
        case.state,
        scope,
        lambda op: copy.deepcopy(case.record) if op == operation else None,
        validator,
        config,
        **kwargs,
    )
    return case


def test_closed_original_generation_preserves_unknown_and_accepts_historical_source(scene):
    before = copy.deepcopy(scene.record)
    proof = scene.observe()
    assert proof.matches(scene.state)
    assert proof.safe_summary["original_generations_absent"] is True
    assert scene.opened == scene.closed == [101, 102, 103]
    assert scene.record == before and scene.record["resolution"] is None
    serialized = json.dumps(proof.safe_summary)
    for private in (str(scene.root), "c" * 32, "k" * 32, "scope_file", "pid_reused"):
        assert private not in serialized
    changed = {**scene.state, "cause": "new field"}
    assert not proof.matches(changed)
    with pytest.raises(CLIError):
        R.RecoveryObservation(None, {}, scene.state, [])


@pytest.mark.parametrize("context", ["started", "closed", "closed_peer"])
def test_ended_qualified_start_requires_original_full_start_validation(scene, monkeypatch, context):
    from xpedition_cli import session_ownership

    scene.record["status"] = "succeeded"
    scene.record["receipt"]["outcome"]["verification"]["status"] = "passed"
    scene.state["state"] = "closed" if context == "closed_peer" else context
    if context == "closed_peer":
        scene.state = {"domain": "pcb", "state": "started", "retained_peer_context": scene.state}
    monkeypatch.setattr(scene.validator, "receipt_rule", lambda receipt: {"ok": True})
    checked = []

    def original(domain, actual, operation, getter, validator, scope):
        checked.append((domain, actual["pid"], operation, scope))
        return {}, {"process_identity": actual}

    monkeypatch.setattr(session_ownership, "original_start", original)
    before = copy.deepcopy(scene.record)
    result = scene.observe()
    assert result.safe_summary["protocol"] == R.ENDED_PROTOCOL
    assert result.safe_summary["original_status"] == "succeeded"
    assert result.safe_summary["normal_close_qualification"] == "unobserved"
    assert result.matches(scene.state)
    assert len(checked) == 1 and checked[0][0:2] == ("schematic", 103)
    assert scene.record == before


def test_success_label_without_passed_original_receipt_is_rejected(scene):
    scene.record["status"] = "succeeded"
    scene.state["state"] = "started"
    with pytest.raises(CLIError):
        scene.observe()
    assert scene.opened == []


def test_exited_handle_needs_birth_and_signal_not_unavailable_image(scene):
    def image_unavailable(*args):
        raise AssertionError("An exited original handle's image getter must not be called")

    scene.kernel.QueryFullProcessImageNameW = image_unavailable
    proof = scene.observe()
    assert proof.safe_summary["original_generations_absent"] is True
    assert scene.opened == scene.closed == [101, 102, 103]


def test_pid_reuse_is_readonly_and_does_not_drift_semantic_after_summary(scene):
    previous = scene.observe()
    scene.current[103]["birth_filetime_100ns"] = "99"
    scene.current[103]["executable"] = "C:/different/unrelated.exe"
    scene.current[103]["alive"] = True
    scene.kernel.WaitForSingleObject = lambda handle, timeout: 258 if handle == 103 else 0
    scene.state = {"domain": "schematic", "state": "started", "startup_operation_id": "op-new"}
    after = scene.observe(expected_summary=previous.safe_summary)
    assert after.safe_summary == previous.safe_summary
    assert after.diagnostics[-1] == {"role": "worker", "pid_reused": True}
    assert not after.matches(scene.state)
    assert not hasattr(scene.kernel, "TerminateProcess")


@pytest.mark.parametrize("failure", ["alive", "read_failed", "wait_failed", "close_failed"])
def test_live_or_incomplete_os_observation_refuses(scene, monkeypatch, failure):
    if failure == "alive":
        scene.kernel.WaitForSingleObject = lambda handle, timeout: 258
    elif failure == "read_failed":
        scene.kernel.GetProcessTimes = lambda *args: False
    elif failure == "wait_failed":
        scene.kernel.WaitForSingleObject = lambda handle, timeout: 0xFFFFFFFF
    else:
        scene.kernel.CloseHandle = lambda handle: False
    with pytest.raises(CLIError) as caught:
        scene.observe()
    assert caught.value.details["write_attempted"] is False
    assert "private error" not in str(caught.value.details)
    assert scene.record["status"] == "unknown" and scene.record["resolution"] is None


@pytest.mark.parametrize("error", [0, 5, 6, 50])
@pytest.mark.parametrize("role,pid", [("owner", 101), ("launcher", 102), ("worker", 103)])
def test_failed_process_open_retains_exact_role_and_error_without_grant(
    scene, monkeypatch, error, role, pid
):
    original = scene.kernel.OpenProcess
    scene.kernel.OpenProcess = lambda access, inherit, actual: (
        None if actual == pid else original(access, inherit, actual)
    )
    monkeypatch.setattr(R.ctypes, "get_last_error", lambda: error, raising=False)
    before = copy.deepcopy(scene.record)
    with pytest.raises(CLIError) as caught:
        scene.observe()
    assert caught.value.details["reason"] == "original_generation_open_unobservable"
    assert caught.value.details["process_observation"] == {
        "role": role,
        "access": 0x101000,
        "callpoint": "OpenProcess",
        "win32_error": error,
    }
    assert scene.record == before and scene.record["resolution"] is None
    assert caught.value.details["state_write_attempted"] is False
    assert caught.value.details["native_request_dispatched"] is False


def test_invalid_parameter_is_the_only_absent_open_result(scene, monkeypatch):
    scene.kernel.OpenProcess = lambda access, inherit, pid: None
    monkeypatch.setattr(R.ctypes, "get_last_error", lambda: 87, raising=False)
    result = scene.observe()
    assert result.safe_summary["original_generations_absent"] is True
    assert result.diagnostics == [
        {"role": role, "pid_reused": False} for role in ("owner", "launcher", "worker")
    ]
    assert scene.closed == [] and scene.record["status"] == "unknown"


def test_failed_native_getter_error_is_captured_before_handle_cleanup(scene, monkeypatch):
    last_error = [6]
    scene.kernel.GetProcessTimes = lambda *args: False

    def close(handle):
        last_error[0] = 1234
        scene.closed.append(handle)
        return True

    scene.kernel.CloseHandle = close
    monkeypatch.setattr(R.ctypes, "get_last_error", lambda: last_error[0], raising=False)
    with pytest.raises(CLIError) as caught:
        scene.observe()
    assert caught.value.details["process_observation"] == {
        "role": "owner",
        "access": 0x101000,
        "callpoint": "GetProcessTimes",
        "win32_error": 6,
    }
    assert scene.closed == [101] and last_error[0] == 1234


@pytest.mark.parametrize("tamper", ["receipt_ref", "scope", "mac", "project", "source", "active"])
def test_source_scope_mac_project_or_active_state_refuses_before_os(scene, tamper):
    if tamper == "receipt_ref":
        scene.record["receipt"]["extensions"]["result"]["owner_ref"] = {}
    elif tamper == "scope":
        scene.record["scope_id"] = "foreign-scope"
    elif tamper == "mac":
        (scene.root / "owner.json").write_bytes(b"invalid mac")
    elif tamper == "project":
        scene.launch["authorized_project"] = "C:/private/project.prj"
        (scene.root / "native-launch-evidence.json").write_bytes(R.L._pack(scene.launch, scene.key))
    elif tamper == "source":
        scene.intent["source_sha256"] = {"foreign.py": "f" * 64}
        (scene.root / "intent.json").write_bytes(R.L._pack(scene.intent, scene.key))
    else:
        scene.state["state"] = "started"
    with pytest.raises(CLIError):
        scene.observe()
    assert scene.opened == []


def test_unrelated_record_has_no_recovery_candidate(scene):
    scene.state = {"domain": "pcb", "state": "attached"}
    with pytest.raises(CLIError) as caught:
        scene.observe()
    assert caught.value.details["reason"] == "retained_start_without_explainable_session_context"
    assert scene.opened == []


@pytest.mark.parametrize("contents", [None, b"{broken json"])
def test_missing_or_damaged_state_with_retained_start_refuses_before_process_observation(
    scene, monkeypatch, contents
):
    from xpedition_cli import session

    path = scene.config / "session.json"
    if contents is not None:
        path.write_bytes(contents)
    monkeypatch.setattr(session, "_state_path", lambda: path)
    scene.state = session.read_state()
    assert scene.state == {}
    with pytest.raises(CLIError) as caught:
        scene.observe()
    assert caught.value.details["write_attempted"] is False
    assert caught.value.details["reason"] == "retained_start_without_explainable_session_context"
    assert scene.opened == []
    assert path.read_bytes() == contents if contents is not None else not path.exists()


@pytest.mark.parametrize("indexes", [False, True])
def test_genuinely_first_empty_configuration_still_has_no_prior_generation(
    scene, tmp_path, indexes
):
    root = tmp_path / "first-empty-config"
    root.mkdir()
    if indexes:
        (root / "pending-starts").mkdir()
        (root / "dedicated-owner-locators").mkdir()
    assert (
        R.observe_exited_failed_start(
            {}, scene.scope, lambda _op: pytest.fail("no source lookup"), scene.validator, root
        )
        is None
    )
    assert scene.opened == []


@pytest.mark.parametrize("count", [64, 65])
def test_orphan_pending_index_is_bounded_and_never_grants_from_presence(scene, tmp_path, count):
    root = tmp_path / "orphan-index-config"
    pending = root / "pending-starts"
    pending.mkdir(parents=True)
    for index in range(count):
        (pending / ("op-" + f"{index:032x}" + ".json")).write_bytes(b"unread, never authority")
    with pytest.raises(CLIError) as caught:
        R.observe_exited_failed_start(
            {}, scene.scope, lambda _op: pytest.fail("no history scan"), scene.validator, root
        )
    expected = (
        "retained_start_index_limit"
        if count == 65
        else "retained_start_without_explainable_session_context"
    )
    assert caught.value.details["reason"] == expected
    assert scene.opened == []


@pytest.mark.parametrize("kind", ["locator_only", "index_reparse", "index_io"])
def test_orphan_locator_reparse_or_io_cannot_look_like_first_use(
    scene, tmp_path, monkeypatch, kind
):
    root = tmp_path / "uncertain-index-config"
    index = root / "dedicated-owner-locators"
    index.mkdir(parents=True)
    if kind == "locator_only":
        (index / ("a" * 32 + ".json")).write_bytes(b"address only")
    elif kind == "index_reparse":
        original = Path.lstat

        def lstat(path):
            if path == index:
                return NS(st_mode=0o040000, st_file_attributes=0x400)
            return original(path)

        monkeypatch.setattr(Path, "lstat", lstat)
    else:
        original = R.os.scandir

        def scandir(path):
            if Path(path) == index:
                raise PermissionError("private path is never exported")
            return original(path)

        monkeypatch.setattr(R.os, "scandir", scandir)
    with pytest.raises(CLIError) as caught:
        R.observe_exited_failed_start(
            {}, scene.scope, lambda _op: pytest.fail("no history scan"), scene.validator, root
        )
    assert "private path" not in str(caught.value.details)
    assert scene.opened == []


@pytest.fixture
def closed_scene(scene, monkeypatch):
    from xpedition_cli import designer_native_owner as owner
    from xpedition_cli import session_ownership
    from xpedition_cli._vendor.tool_driver_sdk.store import target_key

    # Ordinary fresh fixture files have no Win32 sharing-contract requirement.
    monkeypatch.setattr(owner.L, "_snapshot_reader", lambda path: path.open("rb"))
    worker = {
        "pid": 103,
        "creation_time_100ns": "30",
        "executable": scene.launch["expected_native_generation"]["executable"],
    }
    close_id = "op-" + "f" * 32
    pointer = {
        "domain": "schematic",
        "process_identity": worker,
        "startup_operation_id": scene.operation,
        "source_sha256": "a" * 64,
    }
    registry = {"schema_version": 1, "entries": [pointer]}
    (scene.config / "native-ownership.json").write_text(json.dumps(registry))
    scene.record["status"] = "succeeded"
    scene.record["receipt"]["outcome"]["verification"]["status"] = "passed"
    scene.state.update(
        state="closing", pid=103, process_identity=worker, closing_operation_id=close_id
    )
    monkeypatch.setattr(
        scene.validator,
        "receipt_rule",
        lambda receipt: {"ok": receipt.get("operation_id") == scene.operation},
    )
    checked = []

    def original(domain, actual, operation, getter, validator, scope_id):
        assert domain == "schematic" and operation == scene.operation
        assert R.same_identity(actual, worker) and scope_id == scene.scope.scope_id
        assert getter(operation) == scene.record
        checked.append(operation)
        return {"startup_operation_id": operation}, copy.deepcopy(pointer)

    monkeypatch.setattr(session_ownership, "original_start", original)
    commands = json.loads(
        resources.locate("contract/driver-v2/product_contract.json").read_text(encoding="utf-8")
    )["reference"]["commands"]
    capability = next(
        row["driver"]["capability_id"] for row in commands if row["path"] == "session close"
    )
    target = {
        "mode": "session",
        "engine_id": "xpedition-schematic",
        "instance_id": "103",
        "object_id": "schematic",
        "locator": "xpedition://schematic/103",
        "extensions": {
            "command": "session close",
            "domain": "schematic",
            "process_identity": worker,
            "ownership_pointer": pointer,
            "owned_launcher_identities": [scene.launch["startup_seed"]["launcher_identity"]],
            "application": {
                "pid": 103,
                "document": {"open": False, "path": None, "status": "observed"},
            },
        },
    }
    common = {
        "operation_id": close_id,
        "plan_id": "plan-original-close",
        "capability_id": capability,
        "authorization_scope_id": scene.scope.scope_id,
        "effects": ["session"],
        "target": target,
    }
    scene.close_record = {
        "operation_id": close_id,
        "kind": "single",
        "command": "session close",
        "status": "unknown",
        "resolution": None,
        "scope_id": scene.scope.scope_id,
        "plan_id": common["plan_id"],
        "capability_id": capability,
        "effects": ["session"],
        "target_key": target_key(target),
        "plan": {**common, "can_apply": True, "base_snapshot": {"id": "close-before"}},
        "receipt": {
            **common,
            "snapshot_before": {"id": "close-before"},
            "outcome": {"change": "unknown", "verification": {"status": "incomplete"}},
        },
    }
    scene.close_id, scene.pointer, scene.worker, scene.checked = close_id, pointer, worker, checked
    scene.command_id = "owner-command-" + "1" * 32
    directory = scene.root / "commands" / scene.command_id
    directory.mkdir(parents=True)
    scene.request = {
        "command_id": scene.command_id,
        "epoch": scene.intent["execution_epoch"],
        "command": "adapter",
        "method": "close",
        "params": {
            "domain": "schematic",
            "pid": 103,
            "expected_process_identity": worker,
            "save_project": None,
            "exit_wait_seconds": 5,
        },
        "sdk_operation_id": close_id,
        "authorization_binding": {
            "command": "session close",
            "scope_id": scene.scope.scope_id,
            "scope_sha256": scene.intent["editing_scope"]["scope_sha256"],
            "source_operation_id": close_id,
        },
    }
    scene.close_result = {
        "domain": "schematic",
        "pid": 103,
        "process_identity": worker,
        **{
            name: True
            for name in (
                "closed",
                "process_absence_observed",
                "owned_launcher_exit_observed",
                "command_returned",
                "normal_exit_observed",
                "startup_resources_released",
                "process_exit_verified",
            )
        },
        "lifecycle": {
            "empty_document_observed": True,
            "quit_dispatched": True,
            "quit_reply_received": True,
            "save_count": 0,
            "close_count": 0,
            "quit_count": 1,
        },
    }
    (directory / "initial.json").write_bytes(
        owner.L._pack(
            {
                "command_id": scene.command_id,
                "epoch": scene.intent["execution_epoch"],
                "original_outcome": "unknown",
                "immutable": True,
            },
            scene.key,
        )
    )

    def publish():
        (directory / "request.json").write_bytes(owner.L._pack(scene.request, scene.key))
        for path in directory.glob("observation-*.json"):
            path.unlink()
        journal = owner._CommandJournal(directory, scene.key, scene.request)
        journal.append("command_received")
        journal.append("command_started")
        journal.append("command_returned", result=scene.close_result)

    publish()
    event_directory = scene.root / "events"
    event_directory.mkdir()
    exit_generation = {
        "pid": 103,
        "birth_filetime_100ns": "30",
        "executable_sha256": hashlib.sha256(worker["executable"].encode()).hexdigest(),
        "private_launch_evidence_sha256": hashlib.sha256(
            (scene.root / "native-launch-evidence.json").read_bytes()
        ).hexdigest(),
    }
    scene.global_rows = [
        ("public_dispatch_ready", {}),
        ("public_dispatch_stopping", {"reason": "original_empty_quit_returned"}),
        (
            "native_process_exit_observed",
            {"original_handle_signalled": True, "generation": exit_generation},
        ),
        (
            "owner_cleanup_complete",
            {
                "native_exit_verified": True,
                "reference_slots_cleared": True,
                "startup_resources_released": True,
            },
        ),
    ]

    def publish_global():
        for path in event_directory.iterdir():
            path.unlink()
        previous = None
        for sequence, (phase, details) in enumerate(scene.global_rows, 1):
            event = {
                "execution_id": scene.intent["execution_id"],
                "execution_epoch": scene.intent["execution_epoch"],
                "sequence": sequence,
                "previous_sha256": previous,
                "phase": phase,
                "details": details,
            }
            raw = owner.L._pack(event, scene.key)
            (event_directory / f"event-{sequence:04d}.json").write_bytes(raw)
            previous = hashlib.sha256(raw).hexdigest()
        (scene.root / "snapshot.json").write_bytes(
            owner.L._pack(
                {
                    "execution_id": scene.intent["execution_id"],
                    "execution_epoch": scene.intent["execution_epoch"],
                    "sequence": len(scene.global_rows),
                    "event_sha256": previous,
                },
                scene.key,
            )
        )

    publish_global()
    scene.publish, scene.publish_global, scene.command_directory = (
        publish,
        publish_global,
        directory,
    )
    scene.get_record = lambda op: copy.deepcopy(
        scene.record if op == scene.operation else scene.close_record if op == close_id else None
    )
    scene.observe = lambda **kwargs: R.observe_exited_failed_start(
        scene.state, scene.scope, scene.get_record, scene.validator, scene.config, **kwargs
    )
    return scene


def test_completed_same_id_close_permits_only_new_generation_preserves_unknown(closed_scene):
    scene = closed_scene
    before = {path: path.read_bytes() for path in scene.config.parent.rglob("*") if path.is_file()}
    record, state = copy.deepcopy(scene.close_record), copy.deepcopy(scene.state)
    proof = scene.observe()
    assert proof.matches(scene.state)
    assert proof.safe_summary["protocol"] == R.CLOSED_PROTOCOL
    assert proof.safe_summary["original_close_operation_id"] == scene.close_id
    assert proof.safe_summary["original_close_command_id"] == scene.command_id
    assert proof.safe_summary["original_close_status"] == "unknown"
    assert (
        proof.safe_summary["normal_close_qualification"] == "same_id_empty_quit_terminal_observed"
    )
    assert scene.opened == scene.closed == [101, 102, 103]
    assert scene.close_record == record and scene.state == state
    assert {path: path.read_bytes() for path in before} == before
    assert not hasattr(scene.kernel, "TerminateProcess")
    assert str(scene.root) not in json.dumps(proof.safe_summary)
    scene.state = {"domain": "schematic", "state": "started", "startup_operation_id": "op-new"}
    assert scene.observe(expected_summary=proof.safe_summary).safe_summary == proof.safe_summary
    assert not proof.matches(scene.state)


@pytest.mark.parametrize(
    "field",
    [
        "closed",
        "process_absence_observed",
        "owned_launcher_exit_observed",
        "command_returned",
        "normal_exit_observed",
        "startup_resources_released",
        "process_exit_verified",
        "empty_document_observed",
        "quit_dispatched",
        "quit_reply_received",
        "save_count",
        "close_count",
        "quit_count",
    ],
)
def test_close_terminal_requires_each_actual_fact(closed_scene, field):
    scene = closed_scene
    mapping = scene.close_result if field in scene.close_result else scene.close_result["lifecycle"]
    mapping[field] = False if type(mapping[field]) is bool else 2
    scene.publish()
    with pytest.raises(CLIError):
        scene.observe()
    assert scene.opened == [] and scene.close_record["status"] == "unknown"


@pytest.mark.parametrize(
    "bad",
    [
        "sdk_status",
        "sdk_scope",
        "sdk_cap",
        "sdk_target",
        "sdk_snapshot",
        "sdk_pointer",
        "registry",
        "sdk_id",
        "request_pid",
        "request_birth",
        "request_scope",
        "request_save",
        "request_mac",
        "chain",
        "initial",
        "global_cleanup",
        "global_birth",
        "duplicate",
    ],
)
def test_closing_source_mac_or_generation_conflicts_refuse_before_os(closed_scene, bad):
    scene = closed_scene
    if bad == "sdk_status":
        scene.close_record["status"] = "succeeded"
    elif bad == "sdk_scope":
        scene.close_record["scope_id"] = "foreign"
    elif bad == "sdk_cap":
        scene.close_record["plan"]["capability_id"] = "foreign"
    elif bad == "sdk_target":
        scene.close_record["target_key"] = "foreign"
    elif bad == "sdk_snapshot":
        scene.close_record["receipt"]["snapshot_before"] = {}
    elif bad == "sdk_pointer":
        scene.close_record["plan"]["target"]["extensions"]["ownership_pointer"] = {}
    elif bad == "registry":
        (scene.config / "native-ownership.json").write_text('{"schema_version":1,"entries":[]}')
    elif bad == "sdk_id":
        scene.request["sdk_operation_id"] = "op-foreign"
        scene.publish()
    elif bad.startswith("request_") and bad != "request_mac":
        if bad == "request_pid":
            scene.request["params"]["pid"] = 999
        elif bad == "request_birth":
            scene.request["params"]["expected_process_identity"] = {
                **scene.worker,
                "creation_time_100ns": "99",
            }
        elif bad == "request_scope":
            scene.request["authorization_binding"]["scope_id"] = "foreign"
        else:
            scene.request["params"]["save_project"] = "C:/not-empty.prj"
        scene.publish()
    elif bad in {"request_mac", "initial"}:
        (
            scene.command_directory / ("request.json" if bad == "request_mac" else "initial.json")
        ).write_bytes(b"bad mac")
    elif bad == "chain":
        (scene.command_directory / "observation-03.json").unlink()
    elif bad == "global_cleanup":
        scene.global_rows[-1][1]["startup_resources_released"] = False
        scene.publish_global()
    elif bad == "global_birth":
        scene.global_rows[-2][1]["generation"]["birth_filetime_100ns"] = "99"
        scene.publish_global()
    else:
        import shutil

        shutil.copytree(
            scene.command_directory, scene.command_directory.with_name("owner-command-" + "2" * 32)
        )
    with pytest.raises(CLIError):
        scene.observe()
    assert scene.opened == []
    assert scene.close_record.get("resolution") is None


def test_close_summary_cannot_hide_later_sdk_record_change(closed_scene):
    proof = closed_scene.observe()
    closed_scene.close_record["extra"] = {"later-observation": "changed"}
    with pytest.raises(CLIError):
        closed_scene.observe(expected_summary=proof.safe_summary)


def test_close_still_requires_all_three_original_generations_absent(closed_scene):
    closed_scene.kernel.WaitForSingleObject = lambda handle, timeout: 258 if handle == 103 else 0
    with pytest.raises(CLIError):
        closed_scene.observe()
    assert closed_scene.close_record["status"] == "unknown"
