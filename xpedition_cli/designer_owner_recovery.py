"""Read-only proof that one failed empty-start generation can no longer own CAD.

This does not resolve an SDK unknown, adopt an application or issue an owner
ticket. Historical source bytes are bound to the original intent, not today's
code. Only the normal Driver may use the opaque observation for a new intent.
"""

from __future__ import annotations

import copy
import ctypes
import hashlib
import itertools
import json
import os
import re
import stat
from ctypes import wintypes as w
from pathlib import Path

from . import execution_lifecycle as L
from . import resources
from ._vendor.tool_driver_sdk.scope import Scope, ScopeResolver
from .designer_owner_client import _locator_path, pending_start_path, validate_owner_ref
from .errors import CLIError
from .native_process import STARTUP_LINEAGE_VERSION, owned_startup_launchers, same_identity

PROTOCOL = "designer-failed-start-closed-v1"
ENDED_PROTOCOL = "designer-owned-session-ended-v1"
CLOSED_PROTOCOL = "designer-owned-close-ended-v1"
_SEAL = object()
_ID = re.compile(r"op-[A-Za-z0-9_-]{1,100}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")


def _fail(reason, *, process_observation=None):
    details = {
        "reason": reason,
        "write_attempted": False,
        "state_write_attempted": False,
        "native_request_dispatched": False,
        "historical_receipt_changed": False,
    }
    if process_observation is not None:
        details["process_observation"] = process_observation
    raise CLIError(
        "E_CONFLICT",
        "The previous Designer launch has no complete closed-generation proof",
        details,
    )


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class RecoveryObservation:
    """Internal evidence, not a JSON flag or a native execution capability."""

    __slots__ = ("_seal", "_summary", "_state_binding", "_diagnostics")

    def __init__(self, seal, summary, recorded_state, diagnostics):
        if seal is not _SEAL:
            _fail("typed_recovery_observation_required")
        self._seal, self._summary = seal, copy.deepcopy(summary)
        self._state_binding = _digest(recorded_state) if recorded_state is not None else None
        self._diagnostics = copy.deepcopy(diagnostics)

    @property
    def safe_summary(self):
        return copy.deepcopy(self._summary)

    @property
    def diagnostics(self):
        return copy.deepcopy(self._diagnostics)

    def matches(self, recorded_state):
        source = schematic_context(recorded_state)
        return (
            self._seal is _SEAL
            and self._state_binding is not None
            and self._state_binding == _digest(recorded_state)
            and self._summary["original_startup_operation_id"] == source.get("startup_operation_id")
            and self._summary["original_owner_ref"] == source.get("owner_ref")
        )

    def __repr__(self):
        return "<ClosedDesignerStartGenerationObservation>"


class SameRequestStartWitness:
    """Keep a terminal *original generation* proof within its one confirmed start."""

    __slots__ = ("_seal", "_observation", "_adapter", "_request", "_operation", "_plan")

    def __init__(self, seal, observation, adapter, operation_id, plan, recorded_state):
        if (
            seal is not _SEAL
            or type(observation) is not RecoveryObservation
            or not observation.matches(recorded_state)
            or getattr(adapter, "_revision_request_token", None) is None
            or not _ID.fullmatch(str(operation_id))
            or type(plan) is not dict
            or plan.get("operation_id") != operation_id
            or not plan.get("plan_id")
            or ((plan.get("target") or {}).get("extensions") or {}).get("command")
            != "session start"
            or ((plan.get("target") or {}).get("extensions") or {}).get("domain") != "schematic"
            or ((plan.get("target") or {}).get("extensions") or {}).get("source_operation_id")
            != operation_id
            or ((plan.get("target") or {}).get("extensions") or {}).get(
                "previous_start_observation"
            )
            != observation.safe_summary
        ):
            _fail("same_request_terminal_witness_binding_invalid")
        self._seal, self._observation, self._adapter = seal, observation, adapter
        self._request = adapter._revision_request_token
        self._operation, self._plan = operation_id, copy.deepcopy(plan)

    def _require(self, state, scope, get_record, adapter, target):
        summary = self._observation.safe_summary
        association = (
            "previous_failed_start"
            if summary.get("protocol") == PROTOCOL
            else "previous_session_generation"
        )
        current = schematic_context(state)
        record = get_record(self._operation)
        if (
            self._seal is not _SEAL
            or adapter is not self._adapter
            or getattr(adapter, "_revision_request_token", None) is not self._request
            or target != self._plan.get("target")
            or current.get("state") != "started"
            or current.get("startup_operation_id") != self._operation
            or not current.get("owner_ref")
            or current.get("owner_ref") == summary.get("original_owner_ref")
            or current.get(association) != summary
            or not self._observation.matches(current.get(association + "_context") or {})
            or type(record) is not dict
            or record.get("operation_id") != self._operation
            or record.get("status") != "dispatched"
            or record.get("scope_id") != scope.scope_id
            or record.get("plan") != self._plan
        ):
            _fail("same_request_terminal_witness_context_changed")
        return summary

    def observe_after(self, adapter, target, scope, get_record, validator, config_root):
        from .session import read_state

        return observe_exited_failed_start(
            read_state(),
            scope,
            get_record,
            validator,
            config_root,
            expected_summary=self._observation.safe_summary,
            _same_request=self,
            _request_adapter=adapter,
            _request_target=target,
        )


def observe_confirmed_start(adapter, operation_id, plan, scope, get_record, validator, config_root):
    """Acquire the proof here; no earlier observation, JSON or cached proof is accepted."""
    from .session import read_state

    recorded = read_state()
    observation = observe_exited_failed_start(recorded, scope, get_record, validator, config_root)
    if observation is None:
        return None, None
    return observation, SameRequestStartWitness(
        _SEAL, observation, adapter, operation_id, plan, recorded
    )


def schematic_context(state):
    """One explicit closed peer may be retained while Layout owns the active slot."""
    if state.get("domain") == "schematic":
        return state
    peer = state.get("retained_peer_context")
    if (
        isinstance(peer, dict)
        and peer.get("domain") == "schematic"
        and peer.get("state") == "closed"
    ):
        return peer
    return {}


def _require_no_orphan_start_index(config_root):
    """Presence can refuse a missing context, never establish ownership authority."""
    root = Path(config_root)
    if not root.is_absolute() or ".." in root.parts:
        _fail("retained_start_index_root_invalid")
    try:
        for ancestor in reversed((root, *root.parents)):
            try:
                info = ancestor.lstat()
            except FileNotFoundError:
                return  # A genuinely new configuration cannot contain retained indexes.
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                _fail("retained_start_index_root_reparse_or_invalid")
        retained = False
        for name in ("pending-starts", "dedicated-owner-locators"):
            path = root / name
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                _fail("retained_start_index_reparse_or_invalid")
            with os.scandir(path) as entries:
                values = list(itertools.islice(entries, 65))
            if len(values) > 64:
                _fail("retained_start_index_limit")
            for entry in values:
                valid = (
                    _ID.fullmatch(entry.name[:-5])
                    if name == "pending-starts"
                    else re.fullmatch(r"[a-f0-9]{32}", entry.name[:-5])
                )
                info = entry.stat(follow_symlinks=False)
                if (
                    not entry.name.endswith(".json")
                    or not valid
                    or not stat.S_ISREG(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400
                ):
                    _fail("retained_start_index_entry_invalid")
                retained = True
        if retained:
            _fail("retained_start_without_explainable_session_context")
    except CLIError:
        raise
    except OSError:
        _fail("retained_start_index_unobservable")


def _read_bytes(path, limit):
    path = L._plain_path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        _fail("original_private_record_not_regular_or_bounded")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        _fail("original_private_record_byte_limit")
    return raw


def _json(path):
    from ._vendor.tool_driver_sdk.validation import parse_json

    value = parse_json(_read_bytes(path, 1024 * 1024))
    if type(value) is not dict:
        _fail("original_private_mapping_required")
    return value


def _record(operation_id, reference, scope, get_record, validator):
    record = get_record(operation_id)
    if not isinstance(record, dict):
        _fail("original_sdk_start_missing")
    plan, receipt = record.get("plan"), record.get("receipt")
    if not isinstance(plan, dict) or not isinstance(receipt, dict):
        _fail("original_sdk_plan_or_receipt_missing")
    validator.validate("plan", plan)
    validator.validate("receipt", receipt)
    declared = resources.locate("contract/driver-v2/product_contract.json")
    commands = _json(declared)["reference"]["commands"]
    starts = [row for row in commands if row.get("path") == "session start"]
    if len(starts) != 1:
        _fail("declared_start_capability_unavailable")
    capability = starts[0]["driver"]["capability_id"]
    target = plan.get("target") or {}
    extension = target.get("extensions") or {}
    result = (receipt.get("extensions") or {}).get("result") or {}
    outcome = receipt.get("outcome") or {}
    if (
        record.get("status") not in {"unknown", "succeeded"}
        or "resolution" not in record
        or record["resolution"] is not None
        or record.get("kind") != "single"
        or record.get("command") != "session start"
        or record.get("operation_id") != operation_id
        or plan.get("operation_id") != operation_id
        or receipt.get("operation_id") != operation_id
        or not record.get("plan_id")
        or record["plan_id"] != plan.get("plan_id")
        or record["plan_id"] != receipt.get("plan_id")
        or record.get("capability_id") != capability
        or plan.get("capability_id") != capability
        or receipt.get("capability_id") != capability
        or record.get("scope_id") != scope.scope_id
        or plan.get("authorization_scope_id") != scope.scope_id
        or receipt.get("authorization_scope_id") != scope.scope_id
        or record.get("effects") != ["session"]
        or plan.get("effects") != ["session"]
        or receipt.get("effects") != ["session"]
        or receipt.get("target") != target
        or receipt.get("snapshot_before") != plan.get("base_snapshot")
        or plan.get("can_apply") is not True
        or validator.receipt_rule(receipt).get("ok") is not (record.get("status") == "succeeded")
        or target.get("mode") != "file"
        or target.get("engine_id") != "xpedition-cli-files"
        or target.get("instance_id") is not None
        or extension.get("command") != "session start"
        or extension.get("domain") != "schematic"
        or extension.get("input_paths") != []
        or any(extension.get(key) for key in ("project", "project_directory", "library_directory"))
        or result.get("owner_ref") != reference
        or result.get("domain") != "schematic"
        or result.get("startup_operation_id") != operation_id
        or result.get("requested_project") is not None
        or (result.get("application_document") or {}).get("open") is True
        or (result.get("application_document") or {}).get("path")
        or outcome.get("change") not in {"unknown", "not_applied", "applied", "partial"}
        or ((outcome.get("verification") or {}).get("status") == "passed")
        != (record.get("status") == "succeeded")
    ):
        _fail("original_sdk_unknown_empty_start_binding_mismatch")
    scope.require(effects=["session"], target=target)
    return record, target


def _native_generations(launch):
    if launch.get("authorized_project") is not None:
        _fail("historical_launch_was_not_empty")
    expected = launch.get("expected_native_generation") or {}
    worker = {
        "pid": expected.get("pid"),
        "executable": expected.get("executable"),
        "creation_time_100ns": expected.get("birth_filetime_100ns"),
    }
    seed = launch.get("startup_seed") or {}
    candidates = seed.get("candidates")
    if (
        seed.get("version") != STARTUP_LINEAGE_VERSION
        or seed.get("domain") != "schematic"
        or not same_identity(worker, worker)
        or not isinstance(candidates, list)
        or not 1 <= len(candidates) <= 64
    ):
        _fail("historical_native_generation_seed_missing")
    matches = [
        row
        for row in candidates
        if isinstance(row, dict)
        and row.get("process_chain")
        and same_identity(row["process_chain"][-1], worker)
    ]
    if len(matches) != 1:
        _fail("historical_native_generation_chain_ambiguous")
    provenance = {
        **matches[0],
        "version": seed["version"],
        "domain": "schematic",
        "launcher_identity": seed.get("launcher_identity"),
        "ownership": "proven_this_start",
    }
    launchers = owned_startup_launchers(provenance, worker)
    if len(launchers) != 1:
        _fail("historical_distinct_launcher_generation_missing")
    return launchers[0], worker


def _observe_absent(expected, *, role):
    """Read one original generation through a query/synchronize-only HANDLE."""
    access = 0x00101000

    def fail(reason, callpoint, error=None):
        _fail(
            reason,
            process_observation={
                "role": role,
                "access": access,
                "callpoint": callpoint,
                "win32_error": error,
            },
        )

    api = L._kernel()
    handle = api.OpenProcess(access, False, expected["pid"])
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:
            return {"original_generation_absent": True, "pid_reused": False}
        fail("original_generation_open_unobservable", "OpenProcess", error)
    try:
        pid = int(api.GetProcessId(handle))
        error = ctypes.get_last_error() if pid == 0 else None
        if pid != expected["pid"]:
            fail("original_generation_handle_pid_mismatch", "GetProcessId", error)
        times = [w.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            error = ctypes.get_last_error()
            fail("original_generation_birth_unobservable", "GetProcessTimes", error)
        birth = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        if int(birth) <= 0:
            fail("original_generation_birth_unobservable", "GetProcessTimes")
        waited = int(api.WaitForSingleObject(handle, 0))
        error = ctypes.get_last_error() if waited == 0xFFFFFFFF else None
        if waited not in (0, 258):
            fail("original_generation_wait_unobservable", "WaitForSingleObject", error)
        if birth != expected["creation_time_100ns"]:
            return {"original_generation_absent": True, "pid_reused": True}
        if waited != 0:
            fail("original_generation_still_alive", "WaitForSingleObject")
        return {"original_generation_absent": True, "pid_reused": False}
    finally:
        if not api.CloseHandle(handle):
            error = ctypes.get_last_error()
            fail("original_generation_handle_close_unobserved", "CloseHandle", error)


def _close_record(operation_id, scope, get_record, validator, worker, launcher, pointer):
    """An original unknown is evidence to correlate, never a success to rewrite."""
    from ._vendor.tool_driver_sdk.store import target_key
    from .session_ownership import _pointer_equivalent

    record = get_record(operation_id)
    if type(record) is not dict:
        _fail("original_sdk_close_missing")
    plan, receipt = record.get("plan"), record.get("receipt")
    if type(plan) is not dict or type(receipt) is not dict:
        _fail("original_sdk_close_plan_or_receipt_missing")
    validator.validate("plan", plan)
    validator.validate("receipt", receipt)
    commands = _json(resources.locate("contract/driver-v2/product_contract.json"))["reference"][
        "commands"
    ]
    capabilities = [
        row["driver"]["capability_id"] for row in commands if row.get("path") == "session close"
    ]
    target = plan.get("target") or {}
    extension = target.get("extensions") or {}
    document = (extension.get("application") or {}).get("document") or {}
    if (
        len(capabilities) != 1
        or record.get("status") != "unknown"
        or "resolution" not in record
        or record["resolution"] is not None
        or record.get("kind") != "single"
        or record.get("command") != "session close"
        or not plan.get("plan_id")
        or any(value.get("operation_id") != operation_id for value in (record, plan, receipt))
        or any(value.get("plan_id") != plan["plan_id"] for value in (record, receipt))
        or any(value.get("capability_id") != capabilities[0] for value in (record, plan, receipt))
        or record.get("scope_id") != scope.scope_id
        or any(value.get("authorization_scope_id") != scope.scope_id for value in (plan, receipt))
        or any(value.get("effects") != ["session"] for value in (record, plan, receipt))
        or record.get("target_key") != target_key(target)
        or receipt.get("target") != target
        or receipt.get("snapshot_before") != plan.get("base_snapshot")
        or plan.get("can_apply") is not True
        or validator.receipt_rule(receipt).get("ok") is not False
        or ((receipt.get("outcome") or {}).get("verification") or {}).get("status") == "passed"
        or target.get("mode") != "session"
        or target.get("engine_id") != "xpedition-schematic"
        or target.get("instance_id") != str(worker["pid"])
        or extension.get("command") != "session close"
        or extension.get("domain") != "schematic"
        or not same_identity(extension.get("process_identity") or {}, worker)
        or not _pointer_equivalent(extension.get("ownership_pointer") or {}, pointer)
        or type(extension.get("owned_launcher_identities")) is not list
        or len(extension["owned_launcher_identities"]) != 1
        or not same_identity(extension["owned_launcher_identities"][0], launcher)
        or extension.get("save_project")
        or extension.get("project")
        or extension.get("input_paths")
        or (extension.get("application") or {}).get("pid") != worker["pid"]
        or document.get("open") is not False
        or document.get("status") != "observed"
        or document.get("path")
    ):
        _fail("original_sdk_unknown_close_binding_mismatch")
    scope.require(effects=["session"], target=target)
    return record


def _closed_command(root, key, intent, operation_id, worker):
    """Read the unique same-ID close MAC chain; no submit, lock or current-code binding."""
    from . import designer_native_owner as owner

    def inventory():
        directory = L._plain_path(root / "commands")
        with os.scandir(directory) as stream:
            entries = list(itertools.islice(stream, owner.MAX_PUBLIC_COMMANDS + 1))
        if len(entries) > owner.MAX_PUBLIC_COMMANDS:
            _fail("original_close_command_inventory_limit")
        paths = []
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            if (
                not re.fullmatch(r"owner-command-[a-f0-9]{32}", entry.name)
                or not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                _fail("original_close_command_inventory_incomplete")
            paths.append(L._plain_path(directory / entry.name))
        return sorted(paths)

    paths, matches, used = inventory(), [], 0
    allowed = {"initial.json", "request.json", *(f"observation-{i:02d}.json" for i in range(1, 4))}
    for directory in paths:
        with os.scandir(directory) as stream:
            files = list(itertools.islice(stream, 6))
        if len(files) > 5 or {entry.name for entry in files} - allowed:
            _fail("original_close_command_files_incomplete")
        for entry in files:
            info = entry.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                _fail("original_close_command_files_incomplete")
            used += info.st_size
        if used > owner.MAX_PUBLIC_BYTES:
            _fail("original_close_command_byte_limit")
        request, request_digest = owner._command_read(directory / "request.json", key)
        owner._validate_adapter_request(request, directory.name, intent["execution_epoch"])
        if request["method"] != "close":
            continue  # A different failed engineering operation retains its own unknown.
        if request["sdk_operation_id"] != operation_id:
            _fail("original_close_foreign_close_request")
        initial, _ = owner._command_read(directory / "initial.json", key)
        if initial != {
            "command_id": directory.name,
            "epoch": intent["execution_epoch"],
            "original_outcome": "unknown",
            "immutable": True,
        }:
            _fail("original_close_initial_unknown_binding_mismatch")
        params, editing = request["params"], intent["editing_scope"]
        if (
            request["authorization_binding"]
            != {
                "command": "session close",
                "scope_id": editing["scope_id"],
                "scope_sha256": editing["scope_sha256"],
                "source_operation_id": operation_id,
            }
            or params.get("domain") != "schematic"
            or params.get("pid") != worker["pid"]
            or not same_identity(params.get("expected_process_identity") or {}, worker)
            or params.get("save_project")
            or params.get("document_only")
            or set(params)
            - {"domain", "pid", "expected_process_identity", "save_project", "exit_wait_seconds"}
        ):
            _fail("original_close_request_binding_mismatch")
        events = owner._public_command_observations(directory, key, request)
        if len(events) != 3 or events[-1]["phase"] != "command_returned":
            _fail("original_close_terminal_chain_incomplete")
        result = events[-1]["details"].get("result") or {}
        lifecycle = result.get("lifecycle") or {}
        if (
            result.get("domain") != "schematic"
            or result.get("pid") != worker["pid"]
            or not same_identity(result.get("process_identity") or {}, worker)
            or any(
                result.get(name) is not True
                for name in (
                    "closed",
                    "process_absence_observed",
                    "owned_launcher_exit_observed",
                    "command_returned",
                    "normal_exit_observed",
                    "startup_resources_released",
                    "process_exit_verified",
                )
            )
            or any(
                lifecycle.get(name) is not True
                for name in ("empty_document_observed", "quit_dispatched", "quit_reply_received")
            )
            or any(
                type(lifecycle.get(name)) is not int or lifecycle[name] != count
                for name, count in (("save_count", 0), ("close_count", 0), ("quit_count", 1))
            )
        ):
            _fail("original_close_empty_quit_terminal_incomplete")
        matches.append((directory.name, request_digest, _digest(events)))
    if len(matches) != 1 or paths != inventory():
        _fail("original_close_unique_command_not_proven")
    events = L._events(root, key, intent)
    phases = [event["phase"] for event in events]
    required = (
        "public_dispatch_ready",
        "public_dispatch_stopping",
        "native_process_exit_observed",
        "owner_cleanup_complete",
    )
    if (
        not events
        or events[-1]["phase"] != "owner_cleanup_complete"
        or any(phases.count(phase) != 1 for phase in required)
        or [phases.index(phase) for phase in required]
        != sorted(phases.index(phase) for phase in required)
        or any(phase in phases for phase in ("owner_unresolved", "owner_cleanup_failed"))
        or events[phases.index("public_dispatch_stopping")]["details"].get("reason")
        != "original_empty_quit_returned"
        or events[phases.index("native_process_exit_observed")]["details"].get(
            "original_handle_signalled"
        )
        is not True
        or any(
            events[-1]["details"].get(name) is not True
            for name in (
                "native_exit_verified",
                "reference_slots_cleared",
                "startup_resources_released",
            )
        )
    ):
        _fail("original_close_owner_cleanup_chain_incomplete")
    exit_generation = (
        events[phases.index("native_process_exit_observed")]["details"].get("generation") or {}
    )
    if exit_generation != {
        "pid": worker["pid"],
        "birth_filetime_100ns": worker["creation_time_100ns"],
        "executable_sha256": hashlib.sha256(worker["executable"].encode()).hexdigest(),
        "private_launch_evidence_sha256": hashlib.sha256(
            _read_bytes(root / "native-launch-evidence.json", 1024 * 1024)
        ).hexdigest(),
    }:
        _fail("original_close_exit_generation_binding_mismatch")
    return {
        "original_close_operation_id": operation_id,
        "original_close_command_id": matches[0][0],
        "original_close_request_sha256": matches[0][1],
        "original_close_command_chain_sha256": matches[0][2],
        "original_close_owner_chain_sha256": _digest(events),
        "original_close_status": "unknown",
        "original_close_resolution_null": True,
    }


def observe_exited_failed_start(
    recorded_state,
    scope,
    get_record,
    validator,
    config_root,
    *,
    expected_summary=None,
    _same_request=None,
    _request_adapter=None,
    _request_target=None,
):
    """Recheck one original empty-start unknown; never scan history or write state."""
    recorded = copy.deepcopy(recorded_state)
    state = schematic_context(recorded)
    if _same_request is not None and (
        type(_same_request) is not SameRequestStartWitness or expected_summary is None
    ):
        _fail("typed_same_request_terminal_witness_required")
    if expected_summary is None and not (
        state.get("domain") == "schematic"
        and (state.get("owner_ref") is not None or state.get("state") == "startup_incomplete")
    ):
        _require_no_orphan_start_index(config_root)
        return None
    try:
        if expected_summary is None:
            if state.get("state") not in {
                "startup_incomplete",
                "started",
                "stale",
                "closed",
                "closing",
            } or state.get("requested_project"):
                _fail("previous_session_is_not_a_failed_empty_start")
            operation_id, reference = state.get("startup_operation_id"), state.get("owner_ref")
        else:
            if type(expected_summary) is not dict or expected_summary.get("protocol") not in {
                PROTOCOL,
                ENDED_PROTOCOL,
                CLOSED_PROTOCOL,
            }:
                _fail("previous_summary_binding_invalid")
            operation_id = expected_summary.get("original_startup_operation_id")
            reference = expected_summary.get("original_owner_ref")
        closing = (
            state.get("state") == "closing"
            if expected_summary is None
            else expected_summary.get("protocol") == CLOSED_PROTOCOL
        )
        close_id = (
            state.get("closing_operation_id")
            if expected_summary is None
            else expected_summary.get("original_close_operation_id")
        )
        reference = validate_owner_ref(reference)
        if not _ID.fullmatch(str(operation_id)) or not callable(get_record) or validator is None:
            _fail("original_sdk_reader_or_id_missing")
        if type(scope) is not Scope or not scope.granted:
            _fail("original_scope_unavailable")
        fresh = ScopeResolver(
            "xpedition-cli",
            validator=validator,
            environ={
                "XPEDITION_CLI_SCOPE_FILE": str(scope.grant_file),
            },
        ).resolve()
        if not fresh.granted or fresh.scope_id != scope.scope_id or fresh.record != scope.record:
            _fail("original_scope_changed_or_expired")
        record, target = _record(operation_id, reference, fresh, get_record, validator)
        if expected_summary is None and (
            (record["status"] == "unknown" and state.get("state") != "startup_incomplete")
            or (
                record["status"] == "succeeded"
                and state.get("state") not in {"started", "stale", "closed", "closing"}
            )
        ):
            _fail("original_sdk_status_does_not_match_session_state")
        config_root = L._plain_path(config_root)
        target_path = Path(target["object_id"])
        if (
            not target_path.is_relative_to(config_root / "launch-intents")
            or target_path.suffix != ".json"
            or target.get("locator") != target_path.as_uri()
            or (target.get("extensions") or {}).get("path") != str(target_path)
        ):
            _fail("original_sdk_target_was_not_a_launch_intent")
        pending = _json(pending_start_path(operation_id, config_root))
        locator = _json(_locator_path(reference, config_root))
        root = (
            config_root.parent
            / (config_root.name + "-designer-runtime")
            / ("owner-" + reference["owner_id"])
        )
        if (
            pending.get("protocol") != reference["protocol"]
            or pending.get("sdk_operation_id") != operation_id
            or pending.get("owner_id") != reference["owner_id"]
            or pending.get("authority_granted") is not False
            or pending.get("original_outcome") != "unknown"
            or pending.get("root") != str(root)
            or locator.get("root") != str(root)
        ):
            _fail("original_private_locator_or_pending_binding_mismatch")
        root = L._plain_path(root)
        key = _read_bytes(root / "integrity-key.bin", 32)
        if len(key) != 32:
            _fail("original_integrity_key_incomplete")
        intent, _ = L._read(root / "intent.json", key)
        registration, _ = L._read(root / "owner.json", key)
        launch, _ = L._read(root / "native-launch-evidence.json", key)
        source = intent.get("source_sha256")
        epoch = intent.get("execution_epoch")
        editing = intent.get("editing_scope")
        if (
            intent.get("component") != "designer-dedicated-native-owner-measurement-v1"
            or intent.get("mode") != "confirmed-public-owner-v1"
            or not re.fullmatch(r"designer-owner-[a-f0-9]{32}", str(intent.get("execution_id")))
            or not isinstance(epoch, str)
            or not re.fullmatch(r"[a-f0-9]{32}", epoch)
            or hashlib.sha256(epoch.encode()).hexdigest() != reference["owner_epoch_sha256"]
            or intent.get("locator_sha256") != L._locator(root)
            or not isinstance(source, dict)
            or not 1 <= len(source) <= 64
            or any(
                not isinstance(name, str) or not _SHA.fullmatch(str(value))
                for name, value in source.items()
            )
            or source != pending.get("source_binding")
            or source != locator.get("source_binding")
            or _digest(source) != reference["source_bundle_sha256"]
            or intent.get("plan") != pending.get("plan")
            or (intent.get("plan") or {}).get("scheme") != "designer-native-empty-launch-v1"
            or (intent.get("plan") or {}).get("runtime_directory") != str(root / "startup")
            or type(editing) is not dict
            or set(editing) != {"scope_file", "scope_sha256", "scope_id"}
            or editing != pending.get("editing_scope")
            or editing != locator.get("editing_scope")
            or editing["scope_file"] != str(Path(fresh.grant_file).resolve())
            or editing["scope_id"] != fresh.scope_id
            or editing["scope_sha256"]
            != hashlib.sha256(_read_bytes(fresh.grant_file, 65536)).hexdigest()
            or registration.get("epoch") != epoch
        ):
            _fail("original_mac_source_scope_or_plan_mismatch")
        owner = registration.get("owner") or {}
        owner = {
            "pid": owner.get("pid"),
            "executable": owner.get("executable"),
            "creation_time_100ns": owner.get("birth_filetime_100ns"),
        }
        launcher, worker = _native_generations(launch)
        if record["status"] == "succeeded":
            # A previously qualified session can end after a later operation
            # failed. Keep that later operation's own target/unknown untouched;
            # this proof permits only a new empty process generation.
            from .session_ownership import original_start

            _context, pointer = original_start(
                "schematic", worker, operation_id, get_record, validator, fresh.scope_id
            )
            if not same_identity(pointer.get("process_identity") or {}, worker):
                _fail("original_qualified_start_generation_mismatch")
        close_record, close_summary = None, None
        if closing:
            from .session_ownership import _key, _pointer_equivalent, _read

            if (
                record["status"] != "succeeded"
                or not _ID.fullmatch(str(close_id))
                or (
                    expected_summary is None
                    and (
                        state.get("pid") != worker["pid"]
                        or not same_identity(state.get("process_identity") or {}, worker)
                    )
                )
                or not _pointer_equivalent(
                    _read(path=config_root / "native-ownership.json").get(_key("schematic", worker))
                    or {},
                    pointer,
                )
            ):
                _fail("original_closing_start_or_registry_binding_mismatch")
            close_record = _close_record(
                close_id, fresh, get_record, validator, worker, launcher, pointer
            )
            close_summary = _closed_command(root, key, intent, close_id, worker)
            close_summary["original_close_record_sha256"] = _digest(close_record)
        if (
            not same_identity(owner, owner)
            or len({owner["pid"], launcher["pid"], worker["pid"]}) != 3
        ):
            _fail("original_owner_generation_incomplete")
        witnessed = (
            _same_request._require(recorded, fresh, get_record, _request_adapter, _request_target)
            if _same_request is not None
            else None
        )
        rows, diagnostics = [], []
        for role, generation in (("owner", owner), ("launcher", launcher), ("worker", worker)):
            row = {
                "role": role,
                "pid": generation["pid"],
                "creation_time_100ns": generation["creation_time_100ns"],
                "executable_sha256": hashlib.sha256(generation["executable"].encode()).hexdigest(),
                "original_generation_absent": True,
            }
            if witnessed is not None:
                matches = [value for value in witnessed.get("generations", []) if value == row]
                if len(matches) != 1:
                    _fail("same_request_terminal_generation_changed")
                diagnostics.append(
                    {
                        "role": role,
                        "pid_reused": None,
                        "source": "same_request_original_generation_terminal_witness",
                    }
                )
            else:
                observed = _observe_absent(generation, role=role)
                row["original_generation_absent"] = observed["original_generation_absent"]
                diagnostics.append({"role": role, "pid_reused": observed["pid_reused"]})
            rows.append(row)
        summary = {
            "protocol": CLOSED_PROTOCOL
            if closing
            else PROTOCOL
            if record["status"] == "unknown"
            else ENDED_PROTOCOL,
            "original_startup_operation_id": operation_id,
            "original_owner_ref": reference,
            "original_status": record["status"],
            "original_resolution_null": True,
            "engineering_write_replayed": False,
            "normal_close_qualification": "same_id_empty_quit_terminal_observed"
            if closing
            else "unobserved",
            "original_generations_absent": True,
            "generations": rows,
            **(close_summary or {}),
        }
        if expected_summary is not None and summary != expected_summary:
            _fail("previous_generation_observation_changed")
        if get_record(operation_id) != record:
            _fail("original_sdk_unknown_changed_during_observation")
        if closing and (
            get_record(close_id) != close_record
            or {
                **_closed_command(root, key, intent, close_id, worker),
                "original_close_record_sha256": _digest(close_record),
            }
            != close_summary
            or not _pointer_equivalent(
                _read(path=config_root / "native-ownership.json").get(_key("schematic", worker))
                or {},
                pointer,
            )
        ):
            _fail("original_close_evidence_changed_during_observation")
        return RecoveryObservation(
            _SEAL,
            summary,
            recorded if expected_summary is None else None,
            diagnostics,
        )
    except CLIError:
        raise
    except Exception:
        _fail("original_closed_generation_evidence_incomplete")
