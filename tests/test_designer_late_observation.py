"""Original MAC evidence only. No CAD, process APIs, state updates or replay."""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_designer_owner_recovery import no_native, scene  # noqa: F401

from xpedition_cli import designer_late_observation as E
from xpedition_cli import designer_native_owner as O
from xpedition_cli import session_ownership
from xpedition_cli._vendor.tool_driver_sdk.scope import ScopeResolver


@pytest.fixture
def case(scene, monkeypatch):  # noqa: F811
    grant = Path(scene.scope.grant_file)
    value = json.loads(grant.read_text())
    value.update(
        effects=["observe", "session", "change", "persist"], roots=[str(scene.config.parent)]
    )
    grant.write_text(json.dumps(value))
    scope = ScopeResolver(
        "xpedition-cli", validator=scene.validator, environ={"XPEDITION_CLI_SCOPE_FILE": str(grant)}
    ).resolve()
    editing = {
        "scope_file": str(grant.resolve()),
        "scope_sha256": hashlib.sha256(grant.read_bytes()).hexdigest(),
        "scope_id": scope.scope_id,
    }
    scene.intent["editing_scope"] = editing
    (scene.root / "intent.json").write_bytes(O.L._pack(scene.intent, scene.key))
    for p in (
        scene.config / "pending-starts" / f"{scene.operation}.json",
        scene.config / "dedicated-owner-locators" / f"{scene.reference['owner_id']}.json",
    ):
        item = json.loads(p.read_text())
        item["editing_scope"] = editing
        p.write_text(json.dumps(item))
    scene.record["status"] = "succeeded"
    monkeypatch.setattr(scene.validator, "receipt_rule", lambda r: {"ok": True})
    checked = []

    def original(domain, actual, operation, get, validator, scope_id):
        checked.append(operation)
        assert domain == "schematic" and actual["pid"] == 103 and operation == scene.operation
        assert get(operation) == scene.record and scope_id == scope.scope_id
        return {}, {}

    monkeypatch.setattr(session_ownership, "original_start", original)
    operation = "op-" + "f" * 32
    project = str(scene.config.parent / "new-project" / "owned.prj")
    template = str(scene.config.parent / "template" / "Default.prj")
    source = {
        "template": template,
        "library": str(scene.config.parent / "template" / "Library.lmc"),
        "closure_sha256": "1" * 64,
    }
    target = {
        "mode": "file",
        "engine_id": "xpedition-cli-files",
        "object_id": project,
        "instance_id": None,
        "locator": Path(project).as_uri(),
        "extensions": {"path": project, "command": "project create", "domain": "schematic"},
    }
    common = {
        "operation_id": operation,
        "plan_id": "plan-create",
        "capability_id": "project-create",
        "authorization_scope_id": scope.scope_id,
        "effects": ["session", "change", "persist"],
        "target": target,
    }
    before = {"id": "before", "extensions": {"facts": {"designer_setup_source": source}}}
    command_id = "owner-command-" + "1" * 32
    details = {
        "sdk_operation_id": operation,
        "owner_ref": scene.reference,
        "owner_command_id": command_id,
    }
    record = {
        "operation_id": operation,
        "plan_id": "plan-create",
        "capability_id": "project-create",
        "scope_id": scope.scope_id,
        "effects": common["effects"],
        "command": "project create",
        "kind": "single",
        "status": "unknown",
        "resolution": None,
        "plan": {
            **common,
            "base_snapshot": before,
            "can_apply": True,
            "extensions": {
                "legacy_preview": {
                    "changes": [{"action": "initialize_native_database", "design": "NonFixed"}]
                }
            },
        },
        "receipt": {
            **common,
            "snapshot_before": before,
            "extensions": {"handler_error": {"code": "E_OUTCOME_UNKNOWN", "details": details}},
            "outcome": {"execution": "failed", "persistence": "unknown"},
        },
    }
    request = {
        "command_id": command_id,
        "epoch": scene.intent["execution_epoch"],
        "command": "adapter",
        "method": "clone_project",
        "sdk_operation_id": operation,
        "params": {"project": project, "template": template, "design": "NonFixed"},
        "authorization_binding": {
            "command": "project create",
            "scope_id": scope.scope_id,
            "scope_sha256": editing["scope_sha256"],
            "source_operation_id": operation,
            "source_binding": source,
        },
    }
    directory = scene.root / "commands" / command_id
    directory.mkdir(parents=True)
    for name, payload in (
        ("request.json", request),
        (
            "initial.json",
            {
                "command_id": command_id,
                "epoch": request["epoch"],
                "original_outcome": "unknown",
                "immutable": True,
            },
        ),
    ):
        (directory / name).write_bytes(O.L._pack(payload, scene.key))
    journal = O._CommandJournal(directory, scene.key, request)
    journal.append("command_received")
    journal.append("command_started")
    journal.append(
        "command_returned",
        result={
            "created": True,
            "opened": True,
            "persistence_verified": True,
            "secret": "never-return-this",
        },
    )
    (scene.root / "events").mkdir()
    records = {scene.operation: scene.record, operation: record}
    result = NS(
        scene=scene,
        scope=scope,
        record=record,
        records=records,
        request=request,
        directory=directory,
        journal=journal,
        checked=checked,
    )
    result.observe = lambda: E.observe_record(
        record,
        scope=scope,
        validator=scene.validator,
        config_root=scene.config,
        get_record=lambda op: copy.deepcopy(records.get(op)),
    )
    return result


def hashes(root):
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }


def test_authenticated_late_return_is_evidence_only_with_no_record_or_file_changes(case):
    before = copy.deepcopy(case.records)
    files = hashes(case.scene.config.parent)
    result = case.observe()
    assert result["status"] == "observed", result
    assert result["terminal_phase"] == "command_returned"
    assert result["result_facts"] == {
        "created": True,
        "opened": True,
        "saved": None,
        "persistence_verified": True,
    }
    assert result["owner_cleanup"] == {"phase": None, "resources_released": None}
    assert result["native_writer_admitted"] is False and result["cad_requests_queued"] is False
    assert case.records == before and hashes(case.scene.config.parent) == files
    assert case.record["status"] == "unknown" and case.record["resolution"] is None
    serialized = json.dumps(result)
    for secret in (
        "never-return-this",
        "c" * 32,
        "k" * 32,
        str(case.scene.root),
        "scope_file",
        "authorization_binding",
    ):
        assert secret not in serialized
    assert case.checked == [case.scene.operation]


@pytest.mark.parametrize(
    "fault",
    [
        "scope",
        "operation",
        "plan",
        "target",
        "epoch",
        "mac",
        "source",
        "parameter",
        "binding",
        "start",
        "ambiguous",
        "extra",
    ],
)
def test_wrong_or_incomplete_original_associations_cannot_publish_evidence(case, fault):
    if fault == "scope":
        case.record["scope_id"] = "other"
    elif fault == "operation":
        case.record["receipt"]["extensions"]["handler_error"]["details"]["sdk_operation_id"] = (
            "op-other"
        )
    elif fault == "plan":
        case.record["receipt"]["plan_id"] = "plan-other"
    elif fault == "target":
        case.record["receipt"]["target"] = {}
    elif fault == "epoch":
        case.scene.reference["owner_epoch_sha256"] = "0" * 64
    elif fault == "mac":
        (case.directory / "observation-03.json").write_text("{}")
    elif fault == "source":
        p = (
            case.scene.config
            / "dedicated-owner-locators"
            / f"{case.scene.reference['owner_id']}.json"
        )
        d = json.loads(p.read_text())
        d["source_binding"] = {}
        p.write_text(json.dumps(d))
    elif fault in {"parameter", "binding"}:
        if fault == "parameter":
            case.request["params"]["design"] = "Other"
        else:
            case.request["authorization_binding"]["source_operation_id"] = "op-other"
        (case.directory / "request.json").write_bytes(O.L._pack(case.request, case.scene.key))
    elif fault == "start":
        case.records.pop(case.scene.operation)
    elif fault == "ambiguous":
        p = case.scene.config / "pending-starts" / f"{case.scene.operation}.json"
        d = json.loads(p.read_text())
        (p.parent / "op-another.json").write_text(json.dumps(d))
    else:
        (case.directory / "unexpected.json").write_text("{}")
    files = hashes(case.scene.config.parent)
    before = copy.deepcopy(case.records)
    result = case.observe()
    assert result["status"] == "unavailable", result
    assert result["native_writer_admitted"] is False and result["cad_requests_queued"] is False
    assert case.records == before and hashes(case.scene.config.parent) == files


def test_received_started_with_no_terminal_is_unobserved_not_false_or_success(case):
    (case.directory / "observation-03.json").unlink()
    result = case.observe()
    assert result["status"] == "observed", result
    assert result["command_received"] is True and result["command_started"] is True
    assert result["native_return_observed"] is None and result["terminal_phase"] is None
    assert result["result_facts"] is None and result["owner_cleanup"]["resources_released"] is None


@pytest.mark.parametrize(
    "details,released",
    [
        ({}, None),
        ({"reference_slots_cleared": True}, None),
        ({"reference_slots_cleared": True, "startup_resources_released": True}, True),
        ({"reference_slots_cleared": False}, False),
    ],
)
def test_cleanup_phase_alone_does_not_fabricate_unobserved_release(
    case, monkeypatch, details, released
):
    @contextmanager
    def reader(path):
        with path.open("rb") as stream:
            yield stream

    monkeypatch.setattr(O.L, "_snapshot_reader", reader)
    monkeypatch.setattr(O.L, "_replace_snapshot", lambda source, target: source.replace(target))
    writer = O.L.EventWriter(case.scene.root, case.scene.key, case.scene.intent)
    writer.append("owner_cleanup_complete", **details)
    result = case.observe()
    assert result["status"] == "observed", result
    assert result["owner_cleanup"]["phase"] == "owner_cleanup_complete"
    assert result["owner_cleanup"]["resources_released"] is released


def test_resolution_or_non_unknown_record_is_not_supplemented(case):
    case.record["resolution"] = {"outcome": "resolved"}
    assert case.observe()["status"] == "unavailable"
    case.record["resolution"] = None
    case.record["status"] = "succeeded"
    assert case.observe()["status"] == "unavailable"


def test_start_record_is_pinned_before_validation_and_rechecked_after(case, monkeypatch):
    validated = []

    def original(domain, actual, operation, get, validator, scope):
        pinned = get(operation)
        validated.append(copy.deepcopy(pinned))
        case.records[operation]["receipt"]["later_mutation"] = True
        assert get(operation) == pinned
        return {}, {}

    monkeypatch.setattr(session_ownership, "original_start", original)
    result = case.observe()
    assert len(validated) == 1
    assert result["status"] == "unavailable" and result["reason"] == "sdk_record_changed"


def test_hard_read_limit_is_enforced_even_if_stat_did_not_report_growth(tmp_path, monkeypatch):
    path = tmp_path / "bounded.json"
    path.write_bytes(b"x")
    original = Path.open
    sizes = []

    class BoundedStream:
        def __enter__(self):
            self.stream = original(path, "rb")
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, maximum):
            sizes.append(maximum)
            return b"x" * maximum

    monkeypatch.setattr(Path, "open", lambda selected, *args, **kwargs: BoundedStream())
    with pytest.raises(E.ObservationUnavailable, match="record_byte_limit"):
        E._Reader().read(path, 8)
    assert sizes == [9]


def test_inventory_reads_only_the_limit_plus_one():
    consumed = []

    def entries():
        for index in range(10000):
            consumed.append(index)
            yield Path(str(index))

    with pytest.raises(E.ObservationUnavailable, match="record_inventory_limit"):
        E._inventory(NS(iterdir=entries), 5)
    assert len(consumed) == 6


def test_public_show_adds_a_sibling_without_status_update_or_native_request(tmp_path, monkeypatch):
    from test_driver_v2 import _scope

    from xpedition_cli._vendor.tool_driver_sdk import DriverError
    from xpedition_cli.driver import Adapter, runtime

    _scope(tmp_path, monkeypatch)

    class LostReply(Adapter):
        def apply(self, call, plan):
            raise DriverError("E_TIMEOUT", "fixture lost reply", dispatch_state="dispatched")

    args = ["kb", "bind", "--name", "qa", "--url", "https://example.com"]
    with runtime(LostReply(), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dangerous", "--dry-run"])
        operation = preview.envelope["data"]["preview"]["operation_id"]
        failed = cli.run(
            [*args, "--dangerous", "--confirm", preview.envelope["data"]["confirm_token"]]
        )
        assert failed.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
        store = cli._store_open()
        before = copy.deepcopy(store.get(operation))
        calls = []

        def observed(record, **kwargs):
            calls.append((record["operation_id"], kwargs["config_root"]))
            assert record == before and kwargs["get_record"](operation) == before
            return {
                "status": "observed",
                "native_return_observed": True,
                "original_outcome_unchanged": True,
                "native_writer_admitted": False,
            }

        monkeypatch.setattr(E, "observe_record", observed)
        shown = cli.run(["operation", "show", "--id", operation])
        assert shown.envelope["ok"] is True, shown.envelope
        assert shown.envelope["data"]["operation_status"]["status"] == "unknown"
        assert shown.envelope["data"]["operation_status"]["receipt"] == before["receipt"]
        assert shown.envelope["data"]["native_observation"]["native_return_observed"] is True
        assert store.get(operation) == before
        assert calls == [(operation, tmp_path)]
        # Another query cannot borrow the preceding show request's ID.
        listing = cli.run(["operation", "list"])
        assert (
            listing.envelope["ok"] is True and "native_observation" not in listing.envelope["data"]
        )
        assert len(calls) == 1
