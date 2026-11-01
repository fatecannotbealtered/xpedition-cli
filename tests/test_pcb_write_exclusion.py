"""The product-level PCB gate needs no CAD and never changes the SDK ledger."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import pcb_document_guard as guard
from xpedition_cli._vendor.tool_driver_sdk.store import StateStore
from xpedition_cli.errors import CLIError


def _candidate(digest="a" * 64, *, module_path="C:/temp/unpack-a/adapter.py"):
    return {
        "version": "test",
        "build_sha256": digest,
        "frozen": True,
        "module_path": module_path,
        "python_executable": "C:/candidate.exe",
        "diagnostic_content": {
            "scheme": "close-diagnostic-content-v1",
            "code": {"scheme": "onefile-executable", "executable_sha256": digest},
            "runtime": {
                "executable_sha256": digest,
                "implementation": "cpython",
                "version": "test-runtime",
            },
            "vendor_lock_sha256": "c" * 64,
            "vendor_files_sha256": "d" * 64,
            "product_contract_sha256": "e" * 64,
        },
    }


@pytest.fixture(autouse=True)
def isolated_ownership_root(tmp_path, monkeypatch):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "ownership"))


def test_pcb_exclusion_blocks_other_process_and_preserves_original_exception(tmp_path):
    store = StateStore(tmp_path)
    script = """
import json, sys
from xpedition_cli import pcb_document_guard as guard
from xpedition_cli._vendor.tool_driver_sdk.store import StateStore
from xpedition_cli.errors import CLIError
try:
    with guard.exclusive_pcb_write(StateStore(sys.argv[1])):
        print(json.dumps({'admitted': True}))
except CLIError as e:
    print(json.dumps({'admitted': False, 'code': e.code,
                      'reason': e.details['pcb_document_guard']['reason']}))
"""
    with guard.exclusive_pcb_write(store):
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert json.loads(result.stdout) == {
            "admitted": False,
            "code": "E_CONFLICT",
            "reason": "pcb_write_lock_held",
        }
        with pytest.raises(CLIError) as busy:
            with guard.exclusive_pcb_write(store):
                pytest.fail("reentrant writer passed")
        assert busy.value.details["write_attempted"] is False
    with pytest.raises(OSError, match="after dispatch"):
        with guard.exclusive_pcb_write(store):
            raise OSError("after dispatch")
    with guard.exclusive_pcb_write(store):
        pass
    assert (tmp_path / "ownership" / "pcb-engineering.lock").is_file()


def _project(path: Path, board: Path):
    path.write_text(
        'SECTION iCDB\nLIST Designs\nVALUE "Main"\nENDLIST\nENDSECTION\n'
        'SECTION Main\nKEY ConfigType "PCB"\nKEY RootBlock "Root"\n'
        f'KEY PCBDesignPath "{board}"\nENDSECTION\n',
        encoding="utf-8",
    )


@pytest.mark.parametrize("incoming", ["pcb diagnostics close", "pcb placement move"])
def test_before_native_check_correlates_prj_and_pcb_without_cad(tmp_path, monkeypatch, incoming):
    board = tmp_path / "Board.pcb"
    board.write_bytes(b"unchanged fake board")
    project = tmp_path / "Board.prj"
    _project(project, board)
    other = tmp_path / "Other.pcb"
    other.write_bytes(b"other fake board")
    previous_command = (
        "pcb placement move" if incoming.endswith("close") else "pcb diagnostics close"
    )
    record = {
        "operation_id": "op-original",
        "status": "unknown",
        "resolution": None,
        "command": previous_command,
        "effects": ["change", "persist"],
        "plan": {
            "target": {
                "engine_id": "xpedition-pcb",
                "extensions": {"domain": "pcb", "project": str(project)},
            }
        },
    }
    monkeypatch.setattr(guard, "read_active_records", lambda store, **kwargs: [record])
    store = StateStore(tmp_path / "journal")
    with pytest.raises(CLIError) as conflict:
        guard.before_native_write(store, incoming, {"project": str(board)})
    assert conflict.value.details["related_operation_id"] == "op-original"
    assert conflict.value.details["pcb_document_guard"]["reason"] == "same_physical_pcb_unresolved"
    guard.before_native_write(store, incoming, {"project": str(other)})
    assert record["status"] == "unknown" and record["resolution"] is None
    store.close()


def test_filtered_active_ledger_read_is_bounded_and_keeps_other_unknowns(tmp_path):
    store = StateStore(tmp_path)
    connection = store.connection()
    for index in range(guard.MAX_ACTIVE_RECORDS + 1):
        connection.execute(
            "INSERT INTO operations (operation_id, capability_id, command, status, effects, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"op-other-{index}", "other", "pcb placement move", "unknown", "[]", 1.0, 1.0),
        )
    connection.execute(
        "INSERT INTO operations (operation_id, capability_id, command, status, effects, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("op-diag", "diag", "pcb diagnostics close", "unknown", "[]", 1.0, 1.0),
    )
    selected = guard.read_active_records(store, command="pcb diagnostics close")
    assert [record["operation_id"] for record in selected] == ["op-diag"]
    assert (
        guard.read_active_records(
            store, command="pcb diagnostics close", exclude_operation_id="op-diag"
        )
        == []
    )
    with pytest.raises(CLIError) as limited:
        guard.read_active_records(store)
    assert limited.value.details["pcb_document_guard"]["reason"] == "active_record_count_limit"
    assert connection.execute("SELECT COUNT(*) FROM operations WHERE status='unknown'").fetchone()[
        0
    ] == (guard.MAX_ACTIVE_RECORDS + 2)
    store.close()


@pytest.mark.parametrize(
    ("path", "effects", "params", "expected"),
    [
        ("pcb diagnostics close", ["session", "persist"], {}, True),
        ("pcb placement move", ["change", "persist"], {}, True),
        ("manufacturing export", ["session", "persist"], {}, True),
        ("project backup", ["session", "persist"], {}, True),
        ("project restore", ["session", "persist"], {}, True),
        ("project create", ["session", "persist"], {"closed-copy": True}, True),
        ("project create", ["session", "persist"], {"closed-copy": False}, False),
        ("session close", ["session"], {"kind": "pcb"}, True),
        ("session start", ["session"], {"kind": "schematic"}, False),
        ("schematic edit", ["change", "persist"], {}, False),
    ],
)
def test_shared_pcb_exclusion_covers_release_and_replacement(path, effects, params, expected):
    assert (
        guard.requires_pcb_exclusion({"path": path, "driver": {"effects": effects}}, params)
        is expected
    )


@pytest.mark.parametrize("old_command", ["project backup", "project restore"])
def test_diagnostic_refuses_active_project_release_before_cad(tmp_path, monkeypatch, old_command):
    board = tmp_path / "Board.pcb"
    board.write_bytes(b"unchanged fake board")
    project = tmp_path / "Board.prj"
    _project(project, board)
    record = {
        "operation_id": "op-release",
        "status": "unknown",
        "resolution": None,
        "command": old_command,
        "effects": ["session", "persist"],
        "plan": {"target": {"mode": "file", "object_id": str(project), "extensions": {}}},
    }
    monkeypatch.setattr(guard, "read_active_records", lambda store, **kwargs: [record])
    store = StateStore(tmp_path / "journal")
    with pytest.raises(CLIError) as conflict:
        guard.before_native_write(store, "pcb diagnostics close", {"project": str(project)})
    assert conflict.value.details["related_operation_id"] == "op-release"
    store.close()


def test_same_ownership_root_rejects_other_journal_and_candidate(tmp_path):
    first = StateStore(tmp_path / "journal-a")
    second = StateStore(tmp_path / "journal-b")
    candidate = _candidate()
    with guard.exclusive_pcb_write(first):
        original = guard.require_shared_journal(first, candidate)
    binding = tmp_path / "ownership" / "pcb-journal-binding-v1.json"
    saved = binding.read_bytes()
    with guard.exclusive_pcb_write(first):
        assert guard.require_shared_journal(first, candidate) == original
    same_journal = StateStore(tmp_path / "journal-a")
    with guard.exclusive_pcb_write(same_journal):
        assert guard.require_shared_journal(same_journal, candidate) == original
    same_journal.close()
    with guard.exclusive_pcb_write(second), pytest.raises(CLIError) as wrong_journal:
        guard.require_shared_journal(second, candidate)
    assert wrong_journal.value.details["pcb_document_guard"]["reason"] == (
        "journal_or_candidate_mismatch"
    )
    with guard.exclusive_pcb_write(first), pytest.raises(CLIError) as wrong_candidate:
        guard.require_shared_journal(first, _candidate("b" * 64))
    assert wrong_candidate.value.details["pcb_document_guard"]["reason"] == (
        "journal_or_candidate_mismatch"
    )
    assert binding.read_bytes() == saved
    first.close()
    second.close()


def test_onefile_extraction_paths_do_not_change_content_binding(tmp_path):
    executable = tmp_path / "synthetic-candidate.exe"
    executable.write_bytes(b"candidate code and embedded resources v1")
    first_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    first = _candidate(first_hash, module_path="C:/Temp/_MEI-one/adapter.py")
    second = _candidate(first_hash, module_path="C:/Temp/_MEI-two/adapter.py")
    second["python_executable"] = "D:/relocated/candidate.exe"
    store = StateStore(tmp_path / "journal")
    with guard.exclusive_pcb_write(store):
        original = guard.require_shared_journal(store, first)
        assert guard.require_shared_journal(store, second) == original
    executable.write_bytes(b"candidate code and embedded resources v2")
    changed_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    with guard.exclusive_pcb_write(store), pytest.raises(CLIError) as changed:
        guard.require_shared_journal(store, _candidate(changed_hash))
    assert changed.value.details["pcb_document_guard"]["reason"] == "journal_or_candidate_mismatch"
    resource_changed = _candidate(first_hash)
    resource_changed["diagnostic_content"]["product_contract_sha256"] = "f" * 64
    with guard.exclusive_pcb_write(store), pytest.raises(CLIError) as resources:
        guard.require_shared_journal(store, resource_changed)
    assert (
        resources.value.details["pcb_document_guard"]["reason"] == "journal_or_candidate_mismatch"
    )
    store.close()


def test_diagnostic_revision_uses_candidate_bytes_and_retains_location_evidence():
    from xpedition_cli.close_diagnostic import _snapshot

    first = _candidate(module_path="C:/Temp/_MEI-one/adapter.py")
    second = _candidate(module_path="C:/Temp/_MEI-two/adapter.py")
    second["python_executable"] = "D:/relocated/candidate.exe"
    before = _snapshot({"identity": {"adapter": first}})
    after = _snapshot({"identity": {"adapter": second}})
    assert before["revision"] == after["revision"]
    assert (
        after["extensions"]["facts"]["identity"]["adapter"]["module_path"] == second["module_path"]
    )
    changed = _snapshot({"identity": {"adapter": _candidate("b" * 64)}})
    assert changed["revision"] != before["revision"]


def test_request_content_cache_is_fresh_at_boundaries_and_cannot_be_mutated(tmp_path, monkeypatch):
    from xpedition_cli import native_identity, resources

    package = tmp_path / "package"
    vendor = package / "_vendor"
    vendor.mkdir(parents=True)
    source = package / "code.py"
    source.write_bytes(b"source-a")
    vendor_file = vendor / "sdk.py"
    vendor_file.write_bytes(b"synthetic-sdk")
    (vendor / "vendor-lock.json").write_text(
        json.dumps({"files": {"sdk.py": hashlib.sha256(vendor_file.read_bytes()).hexdigest()}}),
        encoding="utf-8",
    )
    contract = tmp_path / "contract.json"
    contract.write_bytes(b"contract-a")
    executable = tmp_path / "python.exe"
    executable.write_bytes(b"synthetic-runtime")
    monkeypatch.setattr(native_identity, "_DIAGNOSTIC_SOURCE_FILES", ("code.py",))
    monkeypatch.setattr(resources, "locate", lambda unused: contract)

    def capture():
        return native_identity._diagnostic_content_binding(package, executable, False, "a" * 64)

    with native_identity.diagnostic_content_request():
        first = capture()
        first_hash = first["code"]["files"]["code.py"]
        first["code"]["files"]["code.py"] = "poisoned"
        assert capture()["code"]["files"]["code.py"] == first_hash
        original = source.stat()
        source.write_bytes(b"source-b")
        os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
        assert capture()["code"]["files"]["code.py"] == first_hash
        native_identity.invalidate_diagnostic_content()
        changed = capture()
        assert changed["code"]["files"]["code.py"] != first_hash
        contract.write_bytes(b"contract-b")
        native_identity.invalidate_diagnostic_content()
        assert capture()["product_contract_sha256"] != changed["product_contract_sha256"]
    contract.write_bytes(b"contract-c")
    with native_identity.diagnostic_content_request():
        assert capture()["product_contract_sha256"] == hashlib.sha256(b"contract-c").hexdigest()


def test_prewrite_rechecks_controller_content_before_store_or_native(monkeypatch):
    from xpedition_cli import close_diagnostic

    checkpoints = []
    monkeypatch.setattr(
        close_diagnostic, "invalidate_diagnostic_content", lambda: checkpoints.append(1)
    )
    monkeypatch.setattr(close_diagnostic, "adapter_identity", lambda: _candidate("b" * 64))
    adapter = SimpleNamespace(diagnostic_store=lambda: pytest.fail("store must not be read"))
    target = {"extensions": {"close_diagnostic_binding": {"controller": _candidate()}}}
    with pytest.raises(CLIError, match="controller candidate changed"):
        close_diagnostic.before_apply(adapter, target, "op-diagnostic")
    assert checkpoints == [1]


def test_other_state_directory_contends_on_same_ownership_lock(tmp_path):
    first = StateStore(tmp_path / "journal-a")
    other = tmp_path / "journal-b"
    script = """
import json, sys
from xpedition_cli import pcb_document_guard as guard
from xpedition_cli._vendor.tool_driver_sdk.store import StateStore
from xpedition_cli.errors import CLIError
try:
    with guard.exclusive_pcb_write(StateStore(sys.argv[1])):
        print(json.dumps({'admitted': True}))
except CLIError as e:
    print(json.dumps({'admitted': False, 'reason': e.details['pcb_document_guard']['reason']}))
"""
    with guard.exclusive_pcb_write(first):
        result = subprocess.run(
            [sys.executable, "-c", script, str(other)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert json.loads(result.stdout) == {"admitted": False, "reason": "pcb_write_lock_held"}


def test_existing_unbound_pcb_state_is_not_silently_adopted(tmp_path):
    root = tmp_path / "ownership"
    root.mkdir()
    state = root / "session.json"
    original = b'{"domain":"pcb","state":"stale"}'
    state.write_bytes(original)
    store = StateStore(tmp_path / "journal")
    with guard.exclusive_pcb_write(store), pytest.raises(CLIError) as refused:
        guard.require_shared_journal(store, _candidate())
    assert refused.value.details["pcb_document_guard"]["reason"] == (
        "prior_pcb_ownership_without_journal_binding"
    )
    assert not (root / "pcb-journal-binding-v1.json").exists()
    assert state.read_bytes() == original
    store.close()


def test_unreadable_prior_session_cannot_establish_new_binding(tmp_path):
    root = tmp_path / "ownership"
    root.mkdir()
    state = root / "session.json"
    state.write_bytes(b"not-json")
    store = StateStore(tmp_path / "journal")
    with guard.exclusive_pcb_write(store), pytest.raises(CLIError) as refused:
        guard.require_shared_journal(store, _candidate())
    assert refused.value.code == "E_IO"
    assert not (root / "pcb-journal-binding-v1.json").exists()
    assert state.read_bytes() == b"not-json"
    store.close()


def test_existing_unbound_pcb_registry_is_not_adopted(tmp_path):
    root = tmp_path / "ownership"
    root.mkdir()
    pointer = {
        "domain": "pcb",
        "process_identity": {
            "pid": 12001,
            "executable": str(tmp_path / "synthetic-layout.exe"),
            "creation_time_100ns": "133000000000000001",
        },
        "startup_operation_id": "op-abc123456789",
        "source_sha256": "a" * 64,
    }
    registry = root / "native-ownership.json"
    original = json.dumps({"schema_version": 1, "entries": [pointer]}).encode()
    registry.write_bytes(original)
    store = StateStore(tmp_path / "journal")
    with guard.exclusive_pcb_write(store), pytest.raises(CLIError) as refused:
        guard.require_shared_journal(store, _candidate())
    assert refused.value.details["pcb_document_guard"]["reason"] == (
        "prior_pcb_ownership_without_journal_binding"
    )
    assert not (root / "pcb-journal-binding-v1.json").exists()
    assert registry.read_bytes() == original
    store.close()


def test_schematic_only_previous_state_allows_first_pcb_binding(tmp_path):
    root = tmp_path / "ownership"
    root.mkdir()
    state = root / "session.json"
    original = b'{"domain":"schematic","state":"started"}'
    state.write_bytes(original)
    store = StateStore(tmp_path / "journal")
    with guard.exclusive_pcb_write(store):
        guard.require_shared_journal(store, _candidate())
    assert (root / "pcb-journal-binding-v1.json").is_file()
    assert state.read_bytes() == original
    store.close()


def test_qualification_store_requires_new_binding_for_first_initialization(tmp_path):
    store = StateStore(tmp_path / "journal")
    with pytest.raises(CLIError) as missing:
        guard.ensure_qualification_store(store, can_initialize=False)
    assert missing.value.details["pcb_document_guard"]["reason"] == (
        "qualification_store_missing_or_changed"
    )
    guard.ensure_qualification_store(store, can_initialize=True)
    guard.verify_qualification_store(store)
    guard.ensure_qualification_store(store, can_initialize=False)
    # An empty table left by an incomplete setup contains no eligible copy.
    guard.ensure_qualification_store(store, can_initialize=True)
    store.connection().execute(
        "INSERT INTO pcb_close_qualification_v1 "
        "(copy_operation_id,board_path,board_device,board_file_id) VALUES (?,?,?,?)",
        ("op-old", "C:/synthetic.pcb", "1", "2"),
    )
    with pytest.raises(CLIError) as elsewhere:
        guard.ensure_qualification_store(store, can_initialize=True)
    assert (
        elsewhere.value.details["pcb_document_guard"]["reason"] == "journal_already_bound_elsewhere"
    )
    store.close()


def test_onefile_binding_observes_actual_bytes_across_unpack_locations(tmp_path, monkeypatch):
    from xpedition_cli import native_identity, resources

    executable = tmp_path / "candidate.exe"
    executable.write_bytes(b"synthetic executable v1")
    unpacked = [tmp_path / "extract-a", tmp_path / "extract-b"]
    for root in unpacked:
        vendor = root / "package" / "_vendor"
        vendor.mkdir(parents=True)
        payload = vendor / "synthetic-sdk.bin"
        payload.write_bytes(b"same synthetic sdk")
        (vendor / "vendor-lock.json").write_text(
            json.dumps(
                {"files": {"synthetic-sdk.bin": hashlib.sha256(payload.read_bytes()).hexdigest()}}
            ),
            encoding="utf-8",
        )
        (root / "contract.json").write_bytes(b"same contract")
    monkeypatch.setattr(resources, "locate", lambda unused: Path(sys._MEIPASS) / "contract.json")

    def capture(root):
        monkeypatch.setattr(sys, "_MEIPASS", str(root), raising=False)
        code_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
        candidate = _candidate(code_hash, module_path=str(root / "package" / "adapter.py"))
        candidate["diagnostic_content"] = native_identity._diagnostic_content_binding(
            root / "package", executable, True, code_hash
        )
        return candidate

    first, second = (capture(root) for root in unpacked)
    store = StateStore(tmp_path / "journal")
    with guard.exclusive_pcb_write(store):
        original = guard.require_shared_journal(store, first)
        assert guard.require_shared_journal(store, second) == original
    executable.write_bytes(b"synthetic executable v2")
    with guard.exclusive_pcb_write(store), pytest.raises(CLIError) as code_changed:
        guard.require_shared_journal(store, capture(unpacked[1]))
    assert (
        code_changed.value.details["pcb_document_guard"]["reason"]
        == "journal_or_candidate_mismatch"
    )
    executable.write_bytes(b"synthetic executable v1")
    (unpacked[1] / "contract.json").write_bytes(b"changed contract")
    with guard.exclusive_pcb_write(store), pytest.raises(CLIError) as resource_changed:
        guard.require_shared_journal(store, capture(unpacked[1]))
    assert (
        resource_changed.value.details["pcb_document_guard"]["reason"]
        == "journal_or_candidate_mismatch"
    )
    store.close()


def test_worker_refreshes_content_after_state_before_save(tmp_path, monkeypatch):
    from xpedition_cli import native_close_diagnostic as worker
    from xpedition_cli import native_identity, resources

    package = tmp_path / "package"
    vendor = package / "_vendor"
    vendor.mkdir(parents=True)
    payload = vendor / "synthetic-sdk.bin"
    payload.write_bytes(b"synthetic sdk")
    (vendor / "vendor-lock.json").write_text(
        json.dumps(
            {"files": {"synthetic-sdk.bin": hashlib.sha256(payload.read_bytes()).hexdigest()}}
        ),
        encoding="utf-8",
    )
    executable = tmp_path / "candidate.exe"
    executable.write_bytes(b"synthetic executable")
    contract = tmp_path / "contract.json"
    contract.write_bytes(b"contract-before")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "unpack"), raising=False)
    monkeypatch.setattr(resources, "locate", lambda unused: contract)

    def candidate():
        value = _candidate("a" * 64)
        value["diagnostic_content"] = native_identity._diagnostic_content_binding(
            package, executable, True, "a" * 64
        )
        return value

    original = candidate()
    bound = {
        "contract": "drc-closed-failure-observation-v1",
        "diagnostic_contract": worker.D.CONTRACT,
        "reopen_authorized": False,
        "generation": {"pid": 42},
        "progid": "MGCPCB.Layout.151",
        "board": str(tmp_path / "board.pcb"),
        "side_effects": [],
        "controller": original,
        "worker": original,
    }

    def read_state(*args):
        assert candidate() == original  # Populate this worker request's cache.
        contract.write_bytes(b"contract-changed")
        return {"adapter": original, "process": {"pid": 42}, "application": {}}, object(), object()

    seen = {}
    require_identity = worker.F.require_identity

    def capture_identity(bound, identity, controller, process, **kwargs):
        seen["contract_sha256"] = controller["diagnostic_content"]["product_contract_sha256"]
        return require_identity(bound, identity, controller, process, **kwargs)

    monkeypatch.setattr(worker, "read_state", read_state)
    monkeypatch.setattr(worker, "adapter_identity", candidate)
    monkeypatch.setattr(worker.F, "require_identity", capture_identity)
    monkeypatch.setattr(worker.F, "_path", lambda value: value)
    monkeypatch.setattr(worker.F.drc, "planned_side_effects", lambda unused: [])
    with pytest.raises(CLIError, match="pre-Save binding failed: ValueError"):
        worker.execute(object(), {"binding": bound})
    assert seen["contract_sha256"] == hashlib.sha256(b"contract-changed").hexdigest()
