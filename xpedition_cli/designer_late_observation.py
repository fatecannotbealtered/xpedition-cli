"""Read authenticated original owner evidence; never resolve or dispatch an operation.

The SDK record is the outcome authority. A late native return is supplementary
evidence only and cannot grant another write, change a receipt or unblock a
target. Historical source maps are compared to their original address records,
not silently promoted to the current executable's source map.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import ntpath
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

from . import designer_native_owner as O
from . import designer_owner_client as C
from . import execution_lifecycle as L
from . import resources
from ._vendor.tool_driver_sdk.scope import Scope, ScopeResolver

_ID = re.compile(r"op-[A-Za-z0-9_-]{1,100}\Z")
_COMMAND = re.compile(r"owner-command-[a-f0-9]{32}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_MAX_FILES = 64
_MAX_BYTES = 4 * 1024 * 1024


class ObservationUnavailable(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise ObservationUnavailable(reason)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _path(value):
    _require(type(value) is str and value and "\0" not in value, "path_binding_invalid")
    return ntpath.normcase(ntpath.normpath(value))


class _Reader:
    def __init__(self):
        self.before = {}
        self.used = 0

    def read(self, path, maximum=_MAX_BYTES):
        path = L._plain_path(Path(path))
        info = path.lstat()
        _require(
            stat.S_ISREG(info.st_mode)
            and not path.is_symlink()
            and not getattr(info, "st_file_attributes", 0) & 0x400
            and info.st_nlink == 1
            and 0 <= info.st_size <= maximum,
            "record_not_regular_or_bounded",
        )

        def fingerprint(value):
            return (
                value.st_mode,
                value.st_dev,
                value.st_ino,
                value.st_nlink,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
                getattr(value, "st_birthtime_ns", None),
            )

        with path.open("rb") as stream:
            descriptor_before = os.fstat(stream.fileno())
            raw = stream.read(maximum + 1)
            descriptor_after = os.fstat(stream.fileno())
        after = path.lstat()
        _require(
            fingerprint(info) == fingerprint(after)
            and fingerprint(descriptor_before) == fingerprint(descriptor_after)
            and (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            == (
                descriptor_before.st_dev,
                descriptor_before.st_ino,
                descriptor_before.st_size,
                descriptor_before.st_mtime_ns,
            ),
            "record_changed_during_read",
        )
        _require(len(raw) <= maximum, "record_byte_limit")
        self.used += len(raw)
        _require(self.used <= 16 * _MAX_BYTES, "observation_total_byte_limit")
        observed = (hashlib.sha256(raw).hexdigest(), fingerprint(info), maximum)
        _require(path not in self.before or self.before[path] == observed, "record_changed")
        self.before[path] = observed
        return raw

    def json(self, path):
        value = json.loads(self.read(path))
        _require(type(value) is dict, "record_object_required")
        return value

    def mac(self, path, key, *, command=False):
        self.read(path, O.MAX_COMMAND_RECORD_BYTES if command else L.MAX_RECORD_BYTES)
        payload, digest = (O._command_read if command else L._read)(path, key)
        _require(self.before[Path(path)][0] == digest, "record_changed_during_mac_read")
        return payload

    def stable(self):
        for path, (_digest, _fingerprint, maximum) in list(self.before.items()):
            self.read(path, maximum)


def _inventory(directory, maximum):
    entries = list(itertools.islice(directory.iterdir(), maximum + 1))
    _require(len(entries) <= maximum, "record_inventory_limit")
    return sorted(entries)


def _record(record, scope, validator):
    _require(type(record) is dict and type(scope) is Scope and scope.granted, "scope_unavailable")
    plan, receipt = record.get("plan"), record.get("receipt")
    _require(type(plan) is dict and type(receipt) is dict, "sdk_plan_or_receipt_missing")
    validator.validate("plan", plan)
    validator.validate("receipt", receipt)
    operation = record.get("operation_id")
    _require(
        type(operation) is str
        and _ID.fullmatch(operation)
        and record.get("kind") == "single"
        and record.get("status") == "unknown"
        and record.get("resolution") is None
        and record.get("plan_id")
        and record["plan_id"] == plan.get("plan_id") == receipt.get("plan_id")
        and plan.get("operation_id") == receipt.get("operation_id") == operation
        and record.get("capability_id") == plan.get("capability_id") == receipt.get("capability_id")
        and record.get("scope_id") == scope.scope_id
        and plan.get("authorization_scope_id")
        == receipt.get("authorization_scope_id")
        == scope.scope_id
        and record.get("effects") == plan.get("effects") == receipt.get("effects")
        and receipt.get("target") == plan.get("target")
        and receipt.get("snapshot_before") == plan.get("base_snapshot")
        and plan.get("can_apply") is True,
        "sdk_record_binding_mismatch",
    )
    declared = json.loads(resources.locate("contract/driver-v2/product_contract.json").read_text())
    matches = [
        x for x in declared["reference"]["commands"] if x.get("path") == record.get("command")
    ]
    _require(
        len(matches) == 1 and matches[0]["driver"]["capability_id"] == record["capability_id"],
        "sdk_capability_binding_mismatch",
    )
    extension = plan["target"].get("extensions") or {}
    _require(extension.get("command") == record.get("command"), "sdk_target_command_mismatch")
    evidence = ((receipt.get("extensions") or {}).get("handler_error") or {}).get("details")
    _require(
        type(evidence) is dict and evidence.get("sdk_operation_id") == operation,
        "owner_reference_missing",
    )
    reference = C.validate_owner_ref(evidence.get("owner_ref"))
    command = evidence.get("owner_command_id")
    _require(type(command) is str and _COMMAND.fullmatch(command), "original_command_id_missing")
    scope.require(effects=["observe"], target=plan["target"])
    return plan, receipt, reference, command


def _original_start(reader, config, reference, get_record, validator, scope, worker):
    directory = L._plain_path(config / "pending-starts")
    entries = _inventory(directory, _MAX_FILES)
    matches = []
    names = sorted(x.name for x in entries)
    for path in sorted(entries):
        _require(_ID.fullmatch(path.stem) and path.suffix == ".json", "start_address_index_invalid")
        value = reader.json(path)
        if value.get("owner_id") == reference["owner_id"]:
            matches.append(value)
    _require(len(matches) == 1, "original_start_address_ambiguous")
    pending = matches[0]
    start_id = pending.get("sdk_operation_id")
    _require(type(start_id) is str and _ID.fullmatch(start_id), "original_start_id_missing")
    from .session_ownership import original_start

    record = copy.deepcopy(get_record(start_id))
    _context, _pointer = original_start(
        "schematic",
        worker,
        start_id,
        lambda operation: copy.deepcopy(record) if operation == start_id else None,
        validator,
        scope.scope_id,
    )
    result = (((record or {}).get("receipt") or {}).get("extensions") or {}).get("result") or {}
    _require(result.get("owner_ref") == reference, "original_start_owner_mismatch")
    _require(
        names == [x.name for x in _inventory(directory, _MAX_FILES)], "start_address_index_changed"
    )
    return pending, start_id, _digest(record)


def _read(record, *, scope, validator, config_root, get_record):
    plan, _receipt, reference, command_id = _record(record, scope, validator)
    reader = _Reader()
    config = L._plain_path(Path(config_root))
    locator = reader.json(C._locator_path(reference, config))
    root = config.parent / (config.name + "-designer-runtime") / ("owner-" + reference["owner_id"])
    scope.require(effects=["observe"], paths=[(str(root), "observe")])
    _require(locator.get("root") == str(root), "owner_locator_root_mismatch")
    root = L._plain_path(root)
    key = reader.read(root / "integrity-key.bin", 32)
    _require(len(key) == 32, "owner_key_incomplete")
    intent = reader.mac(root / "intent.json", key)
    registration = reader.mac(root / "owner.json", key)
    launch = reader.mac(root / "native-launch-evidence.json", key)
    epoch, source, editing = (
        intent.get("execution_epoch"),
        intent.get("source_sha256"),
        intent.get("editing_scope"),
    )
    _require(
        intent.get("mode") == O.PUBLIC_MODE
        and intent.get("component") == O.COMPONENT
        and type(epoch) is str
        and re.fullmatch(r"[a-f0-9]{32}", epoch)
        and hashlib.sha256(epoch.encode()).hexdigest() == reference["owner_epoch_sha256"]
        and intent.get("locator_sha256") == L._locator(root)
        and type(source) is dict
        and 1 <= len(source) <= 64
        and all(type(k) is str and type(v) is str and _SHA.fullmatch(v) for k, v in source.items())
        and source == locator.get("source_binding")
        and _digest(source) == reference["source_bundle_sha256"]
        and registration.get("epoch") == epoch
        and type(editing) is dict
        and set(editing) == {"scope_file", "scope_sha256", "scope_id"}
        and editing == locator.get("editing_scope")
        and editing["scope_id"] == scope.scope_id
        and _path(editing["scope_file"]) == _path(str(Path(scope.grant_file)))
        and editing["scope_sha256"]
        == hashlib.sha256(reader.read(Path(scope.grant_file), 65536)).hexdigest(),
        "original_source_scope_or_epoch_mismatch",
    )
    fresh = ScopeResolver(
        "xpedition-cli",
        validator=validator,
        environ={"XPEDITION_CLI_SCOPE_FILE": editing["scope_file"]},
    ).resolve()
    _require(
        fresh.granted and fresh.scope_id == scope.scope_id and fresh.record == scope.record,
        "scope_changed",
    )
    fresh.require(effects=["observe"], target=plan["target"], paths=[(str(root), "observe")])
    from .designer_owner_recovery import _native_generations

    _launcher, worker = _native_generations(launch)
    pending, start_id, start_digest = _original_start(
        reader, config, reference, get_record, validator, fresh, worker
    )
    _require(
        pending.get("protocol") == reference["protocol"]
        and pending.get("root") == str(root)
        and pending.get("authority_granted") is False
        and pending.get("original_outcome") == "unknown"
        and pending.get("source_binding") == source
        and pending.get("editing_scope") == editing
        and pending.get("plan") == intent.get("plan")
        and (intent.get("plan") or {}).get("scheme") == "designer-native-empty-launch-v1"
        and (intent.get("plan") or {}).get("runtime_directory") == str(root / "startup"),
        "original_start_source_binding_mismatch",
    )
    directory = L._plain_path(root / "commands" / command_id)
    files = [x.name for x in _inventory(directory, 5)]
    _require(
        set(files)
        <= {"request.json", "initial.json", *(f"observation-{n:02d}.json" for n in range(1, 4))},
        "command_inventory_invalid",
    )
    request = reader.mac(directory / "request.json", key, command=True)
    O._validate_adapter_request(request, command_id, epoch)
    initial = reader.mac(directory / "initial.json", key, command=True)
    _require(
        initial
        == {
            "command_id": command_id,
            "epoch": epoch,
            "original_outcome": "unknown",
            "immutable": True,
        },
        "original_command_initial_mismatch",
    )
    binding = request["authorization_binding"]
    _require(
        request["sdk_operation_id"] == record["operation_id"]
        and binding.get("source_operation_id") == record["operation_id"]
        and binding.get("command") == record["command"]
        and binding.get("scope_id") == fresh.scope_id
        and binding.get("scope_sha256") == editing["scope_sha256"],
        "original_command_authorization_mismatch",
    )
    # Only reviewed parameter projections are correlated. Other methods remain
    # unavailable rather than borrowing authority from a same-PID or path match.
    extension, params = plan["target"]["extensions"], request["params"]
    _require(
        record["command"] == "project create" and request["method"] == "clone_project",
        "method_projection_not_reviewed",
    )
    expected_source = ((plan["base_snapshot"].get("extensions") or {}).get("facts") or {}).get(
        "designer_setup_source"
    )
    changes = (plan.get("extensions") or {}).get("legacy_preview", {}).get("changes") or []
    designs = [
        x.get("design")
        for x in changes
        if type(x) is dict and x.get("action") == "initialize_native_database"
    ]
    _require(
        set(params) == {"project", "template", "design"}
        and _path(params.get("project")) == _path(extension.get("path"))
        and type(expected_source) is dict
        and binding
        == {
            "command": record["command"],
            "scope_id": fresh.scope_id,
            "scope_sha256": editing["scope_sha256"],
            "source_operation_id": record["operation_id"],
            "source_binding": expected_source,
        }
        and _path(params.get("template")) == _path(expected_source.get("template"))
        and len(designs) == 1
        and params.get("design") == designs[0],
        "original_command_target_or_parameters_mismatch",
    )
    events = O._public_command_observations(directory, key, request)
    for index in range(1, len(events) + 1):
        reader.read(directory / f"observation-{index:02d}.json", O.MAX_COMMAND_RECORD_BYTES)
    _require(
        files == [x.name for x in _inventory(directory, 5)], "command_changed_during_observation"
    )
    terminal = events[-1] if len(events) == 3 else None
    result = (
        (terminal.get("details") or {}).get("result")
        if terminal and terminal["phase"] == "command_returned"
        else None
    )
    _require(result is None or type(result) is dict, "original_terminal_result_invalid")
    global_events = L._events(root, key, intent)
    event_directory = L._plain_path(root / "events")
    event_files = _inventory(event_directory, L.MAX_EVENTS)
    _require(len(event_files) <= L.MAX_EVENTS, "owner_event_limit")
    for path in event_files:
        reader.read(path, L.MAX_RECORD_BYTES)
    if event_files:
        reader.read(root / "snapshot.json", L.MAX_RECORD_BYTES)
    _require(
        _digest(L._events(root, key, intent)) == _digest(global_events), "owner_events_changed"
    )
    phases = [event["phase"] for event in global_events]
    cleanup = next(
        (
            e
            for e in reversed(global_events)
            if e["phase"] in {"owner_cleanup_complete", "owner_cleanup_failed"}
        ),
        None,
    )
    cleanup_details = (cleanup.get("details") or {}) if cleanup else {}
    released = (
        True
        if cleanup
        and cleanup["phase"] == "owner_cleanup_complete"
        and all(
            cleanup_details.get(name) is True
            for name in ("reference_slots_cleared", "startup_resources_released")
        )
        else False
        if any(
            cleanup_details.get(name) is False
            for name in ("reference_slots_cleared", "startup_resources_released")
        )
        else None
    )
    reader.stable()
    _require(
        [x.name for x in event_files]
        == [x.name for x in _inventory(event_directory, L.MAX_EVENTS)],
        "owner_events_changed",
    )
    _require(
        _digest(get_record(record["operation_id"])) == _digest(record)
        and _digest(get_record(start_id)) == start_digest,
        "sdk_record_changed",
    )
    return {
        "scheme": "designer-original-command-observation-v1",
        "status": "observed",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "sdk_operation_id": record["operation_id"],
        "owner_command_id": command_id,
        "original_sdk_status": record["status"],
        "original_outcome_unchanged": True,
        "native_writer_admitted": False,
        "automatic_replay": False,
        "cad_requests_queued": False,
        "command_received": any(e["phase"] == "command_received" for e in events) or None,
        "command_started": any(e["phase"] == "command_started" for e in events) or None,
        "terminal_phase": terminal["phase"] if terminal else None,
        "native_return_observed": terminal["phase"] == "command_returned" if terminal else None,
        "result_facts": {
            name: result.get(name) if type(result.get(name)) is bool else None
            for name in ("created", "opened", "saved", "persistence_verified")
        }
        if result is not None
        else None,
        "owner_cleanup": {
            "phase": cleanup["phase"] if cleanup else None,
            "resources_released": released,
        },
        "owner_unresolved_observed": True if "owner_unresolved" in phases else None,
        "qualification": (
            "Authenticated original native evidence only; not independent target verification, "
            "SDK resolution, normal exit or permission for another write."
        ),
    }


def observe_record(record, *, scope, validator, config_root, get_record):
    """Bounded file-only supplement; errors never replace the original SDK result."""
    try:
        return _read(
            copy.deepcopy(record),
            scope=scope,
            validator=validator,
            config_root=config_root,
            get_record=get_record,
        )
    except Exception as error:
        return {
            "scheme": "designer-original-command-observation-v1",
            "status": "unavailable",
            "reason": str(error)
            if type(error) is ObservationUnavailable
            else "original_authenticated_evidence_unavailable",
            "native_writer_admitted": False,
            "automatic_replay": False,
            "cad_requests_queued": False,
            "original_outcome_unchanged": True,
        }
