"""Bounded pointers to this task's verified original native-start receipts.

Pointers grant nothing by themselves. Every use rereads the same SDK ledger by
the original operation ID and verifies its native generation and startup proof.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from pathlib import Path

from . import resources
from .audit import config_dir
from .errors import CLIError
from .native_process import owned_startup_launchers, same_identity

MAX_OWNED_GENERATIONS = 64
MAX_REGISTRY_BYTES = 262144
_DOMAINS = {"pcb", "schematic", "library"}
_ID = re.compile(r"op-[a-zA-Z0-9_-]{1,80}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_POINTER_FIELDS = {"domain", "process_identity", "startup_operation_id", "source_sha256"}
_IDENTITY_FIELDS = {"pid", "executable", "creation_time_100ns"}
_CURRENT_CLOSE = ContextVar("verified_sdk_close_operation", default=None)


def _fail(reason, *, code="E_CONFLICT", state_write_attempted=False):
    raise CLIError(
        code,
        "Native startup ownership could not be verified from its original record",
        {
            "write_attempted": state_write_attempted,
            "state_write_attempted": state_write_attempted,
            "native_write_attempted": False,
            "lifecycle_diagnostic": {
                "stage": "ownership",
                "callsite": "session_ownership.original_start",
                "reason": reason,
            },
        },
    )


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _key(domain, identity):
    if (
        domain not in _DOMAINS
        or not isinstance(identity, dict)
        or set(identity) != {"pid", "executable", "creation_time_100ns"}
        or type(identity.get("pid")) is not int
        or identity["pid"] <= 0
        or not isinstance(identity.get("executable"), str)
        or not Path(identity["executable"]).is_absolute()
        or not isinstance(identity.get("creation_time_100ns"), str)
        or not same_identity(identity, identity)
    ):
        _fail("invalid_generation")
    return _digest(
        [
            domain,
            identity["pid"],
            os.path.normcase(os.path.abspath(identity["executable"])),
            identity["creation_time_100ns"],
        ]
    )


@contextmanager
def current_close_operation(operation_id, domain, process_identity):
    """Driver-only scope for revalidation after this operation prepares metadata.

    This never permits a second handler entry or another native request. The
    handler's pending-close guard and the owner's no-replay queue guard remain.
    """
    if not _ID.fullmatch(str(operation_id)):
        _fail("current_close_operation_id_missing")
    token = _CURRENT_CLOSE.set((operation_id, domain, _key(domain, process_identity)))
    try:
        yield
    finally:
        _CURRENT_CLOSE.reset(token)


def registry_path():
    return config_dir() / "native-ownership.json"


def _regular(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        _fail("registry_not_regular", code="E_IO")
    return True


def _read(*, path=None):
    path = registry_path() if path is None else Path(path)
    if not _regular(path):
        return {}
    try:
        raw = _bounded_read(path)
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "entries"}
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or not isinstance(value["entries"], list)
            or len(value["entries"]) > MAX_OWNED_GENERATIONS
        ):
            _fail("registry_invalid", code="E_IO")
        entries = {}
        for entry in value["entries"]:
            if (
                not isinstance(entry, dict)
                or set(entry)
                != {"domain", "process_identity", "startup_operation_id", "source_sha256"}
                or not _ID.fullmatch(str(entry["startup_operation_id"]))
                or not _SHA.fullmatch(str(entry["source_sha256"]))
            ):
                _fail("registry_invalid", code="E_IO")
            key = _key(entry["domain"], entry["process_identity"])
            if key in entries:
                _fail("registry_ambiguous", code="E_IO")
            entries[key] = entry
        return entries
    except (OSError, ValueError, TypeError, KeyError):
        _fail("registry_unreadable", code="E_IO")


def pointer_for(domain, identity):
    return copy.deepcopy(_read().get(_key(domain, identity)))


def _pointer_equivalent(left, right):
    """Only the existing native generation rule may normalize pointer fields."""
    if (
        type(left) is not dict
        or type(right) is not dict
        or set(left) != _POINTER_FIELDS
        or set(right) != _POINTER_FIELDS
        or type(left.get("process_identity")) is not dict
        or type(right.get("process_identity")) is not dict
        or set(left["process_identity"]) != _IDENTITY_FIELDS
        or set(right["process_identity"]) != _IDENTITY_FIELDS
    ):
        return False
    return all(
        left[field] == right[field] for field in ("domain", "startup_operation_id", "source_sha256")
    ) and same_identity(left["process_identity"], right["process_identity"])


def require_registration_capacity():
    if len(_read()) >= MAX_OWNED_GENERATIONS:
        _fail("registry_generation_limit")


def _bounded_read(path):
    if path.stat().st_size > MAX_REGISTRY_BYTES:
        _fail("registry_size_limit", code="E_IO")
    with path.open("rb") as stream:
        raw = stream.read(MAX_REGISTRY_BYTES + 1)
    if len(raw) > MAX_REGISTRY_BYTES:
        _fail("registry_size_limit", code="E_IO")
    return raw


@contextmanager
def _locked():
    path = registry_path().with_suffix(".lock")
    descriptor = None
    unlock = None
    try:
        _regular(path)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        if not _regular(path) or not os.path.samestat(path.stat(), os.fstat(descriptor)):
            _fail("registry_lock_identity_changed", code="E_IO")
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                unlock = partial(msvcrt.locking, descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                unlock = partial(fcntl.flock, descriptor, fcntl.LOCK_UN)
        except OSError:
            _fail("registry_busy")
        yield
    except OSError:
        _fail("registry_write_unavailable", code="E_IO")
    finally:
        if descriptor is not None:
            try:
                if unlock is not None:
                    unlock()
            finally:
                # The OS releases the lock even after process interruption.
                # Keep the lock file; never remove another process's lock path.
                os.close(descriptor)


def _write(entries):
    path = registry_path()
    temporary = path.with_name(path.stem + "." + uuid.uuid4().hex + ".tmp")
    created = False
    if len(entries) > MAX_OWNED_GENERATIONS:
        _fail("registry_generation_limit")
    value = {"schema_version": 1, "entries": [entries[key] for key in sorted(entries)]}
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    if len(raw) > MAX_REGISTRY_BYTES:
        _fail("registry_size_limit", code="E_IO")
    try:
        with temporary.open("xb") as stream:
            created = True
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
        if _bounded_read(path) != raw:
            _fail("registry_write_not_verified", code="E_IO")
    except CLIError as error:
        if created:
            details = dict(error.details or {})
            details.update(
                write_attempted=True, state_write_attempted=True, native_write_attempted=False
            )
            raise CLIError(error.code, error.message, details) from error
        raise
    except OSError:
        _fail("registry_write_not_verified", code="E_IO", state_write_attempted=created)
    finally:
        if created:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                _fail("registry_temporary_cleanup_failed", code="E_IO", state_write_attempted=True)


def register(pointer):
    if (
        type(pointer) is not dict
        or set(pointer) != _POINTER_FIELDS
        or not _ID.fullmatch(str(pointer.get("startup_operation_id")))
        or not _SHA.fullmatch(str(pointer.get("source_sha256")))
    ):
        _fail("registry_invalid_pointer")
    key = _key(pointer["domain"], pointer["process_identity"])
    with _locked():
        entries = _read()
        if key in entries:
            if not _pointer_equivalent(entries[key], pointer):
                _fail("registry_source_conflict")
            return  # Retain the originally registered pointer and its exact bytes.
        entries[key] = copy.deepcopy(pointer)
        _write(entries)


def remove(domain, identity):
    key = _key(domain, identity)
    if not registry_path().exists():
        return
    with _locked():
        entries = _read()
        if key in entries:
            del entries[key]
            _write(entries)


def original_start(domain, actual, operation_id, get_record, validator, scope_id):
    """Validate one explicitly named source; never scan, replay or resolve history."""
    if (
        not _ID.fullmatch(str(operation_id))
        or not callable(get_record)
        or validator is None
        or not isinstance(scope_id, str)
        or not scope_id
    ):
        _fail("original_start_id_or_reader_missing")
    try:
        contract_path = resources.locate("contract/driver-v2/product_contract.json")
        if contract_path is None:
            _fail("declared_start_capability_unavailable", code="E_CONFIG")
        declared = json.loads(contract_path.read_text(encoding="utf-8"))["reference"]["commands"]
        starts = [item for item in declared if item.get("path") == "session start"]
        if len(starts) != 1:
            _fail("declared_start_capability_unavailable", code="E_CONFIG")
        capability_id = starts[0]["driver"]["capability_id"]
        record = get_record(operation_id)
        if not isinstance(record, dict):
            _fail("original_start_missing")
        plan, receipt = record.get("plan"), record.get("receipt")
        if not isinstance(plan, dict) or not isinstance(receipt, dict):
            _fail("original_start_incomplete")
        validator.validate("plan", plan)
        validator.validate("receipt", receipt)
        if (
            record.get("status") != "succeeded"
            or record.get("command") != "session start"
            or record.get("kind") != "single"
            or record.get("resolution") is not None
            or record.get("operation_id") != operation_id
            or plan.get("operation_id") != operation_id
            or receipt.get("operation_id") != operation_id
            or not record.get("plan_id")
            or record["plan_id"] != plan.get("plan_id")
            or record["plan_id"] != receipt.get("plan_id")
            or record.get("capability_id") != capability_id
            or record["capability_id"] != plan.get("capability_id")
            or record["capability_id"] != receipt.get("capability_id")
            or record.get("scope_id") != scope_id
            or plan.get("authorization_scope_id") != scope_id
            or receipt.get("authorization_scope_id") != scope_id
            or record.get("effects") != plan.get("effects")
            or record.get("effects") != receipt.get("effects")
            or receipt.get("target") != plan.get("target")
            or receipt.get("snapshot_before") != plan.get("base_snapshot")
            or plan.get("can_apply") is not True
            or (plan.get("target", {}).get("extensions") or {}).get("command") != "session start"
            or (plan.get("target", {}).get("extensions") or {}).get("domain") != domain
            or validator.receipt_rule(receipt).get("ok") is not True
        ):
            _fail("original_start_binding_mismatch")
        outcome = receipt.get("outcome") or {}
        verification = outcome.get("verification") or {}
        after = receipt.get("snapshot_after") or {}
        facts = (after.get("extensions") or {}).get("facts") or {}
        extension = receipt.get("extensions") or {}
        result = extension.get("result") or {}
        provenance = result.get("startup_provenance") or {}
        seed = result.get("startup_seed") or {}
        if (
            outcome.get("execution") != "completed"
            or verification.get("status") != "passed"
            or verification.get("coverage") != "complete"
            or verification.get("snapshot_id") != after.get("id")
            or not any(
                item.get("id") == "session-context-readback"
                and item.get("status") == "passed"
                and item.get("required") is True
                and item.get("evidence_ref") == after.get("id")
                for item in verification.get("checks", [])
            )
            or extension.get("handler_error") is not None
            or result.get("domain") != domain
            or result.get("started") is not True
            or result.get("attached") is not True
            or result.get("pid") != actual.get("pid")
            or result.get("startup_operation_id") not in (None, operation_id)
            or not same_identity(result.get("process_identity") or {}, actual)
            or not same_identity(facts.get("os_process_identity") or {}, actual)
            or not same_identity(facts.get("recorded_process_identity") or {}, actual)
            or (facts.get("application") or {}).get("pid") != actual.get("pid")
            or provenance != facts.get("startup_provenance")
            or provenance.get("domain") != domain
            or seed.get("domain") != domain
            or seed.get("version") != provenance.get("version")
            or result.get("launcher_pid") != (seed.get("launcher_identity") or {}).get("pid")
            or not same_identity(
                provenance.get("launcher_identity") or {}, seed.get("launcher_identity") or {}
            )
        ):
            _fail("original_start_native_proof_mismatch")
        image = ((facts.get("application") or {}).get("public_fields") or {}).get("FullName")
        if not image or os.path.normcase(os.path.abspath(image)) != os.path.normcase(
            os.path.abspath(actual["executable"])
        ):
            _fail("original_start_executable_mismatch")
        owned_startup_launchers(provenance, actual)
        digest = _digest(
            {
                key: record.get(key)
                for key in (
                    "operation_id",
                    "plan_id",
                    "capability_id",
                    "command",
                    "scope_id",
                    "status",
                    "effects",
                    "plan",
                    "receipt",
                    "resolution",
                )
            }
        )
        pointer = {
            "domain": domain,
            "process_identity": copy.deepcopy(actual),
            "startup_operation_id": operation_id,
            "source_sha256": digest,
        }
        context = {
            "domain": domain,
            "pid": actual["pid"],
            "process_identity": copy.deepcopy(actual),
            "startup_provenance": copy.deepcopy(provenance),
            "startup_operation_id": operation_id,
            "requested_project": result.get("requested_project"),
            "application_document": copy.deepcopy(result.get("application_document")),
            "launcher_ownership": "proven_this_start",
        }
        return context, pointer
    except CLIError:
        raise
    except Exception:
        # Neither arbitrary contract errors nor OS/private source values escape.
        _fail("original_start_validation_failed")


def select(domain, actual, *, get_record=None, validator=None, scope_id=None, source_id=None):
    from .session import read_state

    active = read_state()
    if active.get("state") == "closing":
        expected = _CURRENT_CLOSE.get()
        if (
            expected is None
            or expected != (active.get("closing_operation_id"), domain, _key(domain, actual))
            or active.get("domain") != domain
            or not same_identity(active.get("process_identity") or {}, actual)
        ):
            _fail("original_close_pending")
    pointer = pointer_for(domain, actual)
    if source_id is not None:
        context, recovered = original_start(
            domain, actual, source_id, get_record, validator, scope_id
        )
        if pointer is not None:
            if not _pointer_equivalent(pointer, recovered):
                _fail("registry_source_conflict")
            return context, pointer
        return context, recovered
    if pointer is not None:
        context, expected = original_start(
            domain, actual, pointer["startup_operation_id"], get_record, validator, scope_id
        )
        if not _pointer_equivalent(pointer, expected):
            _fail("original_start_changed")
        return context, pointer
    if active.get("domain") == domain and same_identity(
        active.get("process_identity") or {}, actual
    ):
        if active.get("startup_operation_id"):
            _fail("ownership_pointer_missing_recovery_required")
        owned_startup_launchers(active.get("startup_provenance"), actual)
        return copy.deepcopy(active), None
    if active.get("startup_provenance") is not None:
        # Preserve the C7 diagnostic for the exact legacy cross-domain case.
        owned_startup_launchers(active["startup_provenance"], actual)
    _fail("original_start_recovery_required")


_PREPARED_CLOSE_SEAL = object()
PREPARED_CLOSE_PROTOCOL = "designer-close-preparation-not-dispatched-v1"


class PreparedCloseObservation:
    """Opaque evidence for a metadata transition, never a second Quit permission."""

    __slots__ = ("_seal", "_state", "_summary")

    def __init__(self, seal, state, summary):
        if seal is not _PREPARED_CLOSE_SEAL:
            _fail("typed_close_preparation_observation_required")
        self._seal, self._state, self._summary = seal, copy.deepcopy(state), copy.deepcopy(summary)

    @property
    def original_operation_id(self):
        return self._summary["original_close_operation_id"]

    @property
    def process_identity(self):
        return copy.deepcopy(self._state["process_identity"])

    @property
    def safe_summary(self):
        return copy.deepcopy(self._summary)

    def matches(self, state):
        return self._seal is _PREPARED_CLOSE_SEAL and self._state == state

    def __repr__(self):
        return "<PreparedCloseNotDispatchedObservation>"


def _prepared_command_census(owner, ticket, root, key, intent, operation_id, editing):
    """Complete bounded immutable request/initial/terminal MAC census, no lock writes."""

    def directories():
        with os.scandir(root / "commands") as stream:
            values = list(itertools.islice(stream, owner.MAX_PUBLIC_COMMANDS + 1))
        if len(values) > owner.MAX_PUBLIC_COMMANDS:
            _fail("close_preparation_request_limit")
        result = []
        for entry in values:
            if not re.fullmatch(r"owner-command-[a-f0-9]{32}", entry.name):
                _fail("close_preparation_request_inventory_incomplete")
            path = owner.L._plain_path(root / "commands" / entry.name)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                _fail("close_preparation_request_inventory_incomplete")
            result.append(path)
        return sorted(result)

    paths, used, identity_count = directories(), 0, 0
    allowed = {"initial.json", "request.json", *(f"observation-{i:02d}.json" for i in range(1, 4))}
    for directory in paths:
        with os.scandir(directory) as stream:
            files = list(itertools.islice(stream, 6))
        if len(files) > 5 or {entry.name for entry in files} - allowed:
            _fail("close_preparation_request_files_incomplete")
        for entry in files:
            info = entry.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                _fail("close_preparation_request_files_incomplete")
            used += info.st_size
        if used > owner.MAX_PUBLIC_BYTES:
            _fail("close_preparation_request_byte_limit")
        request, _ = owner._command_read(directory / "request.json", key)
        owner._validate_adapter_request(request, directory.name, ticket.epoch)
        initial, _ = owner._command_read(directory / "initial.json", key)
        if initial != {
            "command_id": directory.name,
            "epoch": ticket.epoch,
            "original_outcome": "unknown",
            "immutable": True,
        }:
            _fail("close_preparation_request_initial_mismatch")
        if request["method"] == "close":
            _fail("close_preparation_close_already_queued")
        events = owner._public_command_observations(directory, key, request)
        if len(events) != 3 or events[-1]["phase"] != "command_returned":
            _fail("close_preparation_other_command_unresolved")
        if request["sdk_operation_id"] == operation_id:
            binding = request["authorization_binding"]
            if request["method"] != "identity" or binding != {
                "command": "session close",
                "scope_id": editing["scope_id"],
                "scope_sha256": editing["scope_sha256"],
                "source_operation_id": operation_id,
            }:
                _fail("close_preparation_write_or_foreign_request_present")
            identity_count += 1
    if paths != directories():
        _fail("close_preparation_request_inventory_changed")
    # Global state is a synchronized MAC chain; no in-process/GUI status is inferred.
    events = owner.L._events(root, key, intent)
    if not events or events[-1]["phase"] != "public_dispatch_ready":
        _fail("close_preparation_owner_not_serviceable")
    return identity_count


def observe_prepared_close(recorded_state, scope, get_record, validator, config_root):
    """Read-only proof of an SDK-not-started close preparation; old failures stay old."""
    state = copy.deepcopy(recorded_state)
    if state.get("state") != "closing":
        return None
    try:
        from . import designer_owner_client as C
        from ._vendor.tool_driver_sdk.scope import Scope, ScopeResolver

        operation_id, startup_id = (
            state.get("closing_operation_id"),
            state.get("startup_operation_id"),
        )
        actual = state.get("process_identity") or {}
        reference = C.validate_owner_ref(state.get("owner_ref"))
        if (
            state.get("domain") != "schematic"
            or not _ID.fullmatch(str(operation_id))
            or not _ID.fullmatch(str(startup_id))
            or not same_identity(actual, actual)
            or not callable(get_record)
            or validator is None
            or type(scope) is not Scope
        ):
            _fail("close_preparation_original_binding_incomplete")
        fresh = ScopeResolver(
            "xpedition-cli",
            validator=validator,
            environ={
                "XPEDITION_CLI_SCOPE_FILE": str(scope.grant_file),
            },
        ).resolve()
        if not fresh.granted or fresh.record != scope.record or fresh.scope_id != scope.scope_id:
            _fail("close_preparation_scope_changed_or_expired")
        record = get_record(operation_id)
        plan = record.get("plan") or {}
        target, extension = (
            plan.get("target") or {},
            (plan.get("target") or {}).get("extensions") or {},
        )
        validator.validate("plan", plan)
        declared = resources.locate("contract/driver-v2/product_contract.json")
        commands = json.loads(declared.read_text(encoding="utf-8"))["reference"]["commands"]
        close_cap = next(
            row["driver"]["capability_id"] for row in commands if row.get("path") == "session close"
        )
        document = (extension.get("application") or {}).get("document") or {}
        if (
            record.get("status") != "planned"
            or record.get("receipt") is not None
            or record.get("resolution") is not None
            or record.get("started_at") is not None
            or record.get("kind") != "single"
            or record.get("command") != "session close"
            or record.get("operation_id") != operation_id
            or plan.get("operation_id") != operation_id
            or record.get("plan_id") != plan.get("plan_id")
            or not plan.get("plan_id")
            or record.get("capability_id") != close_cap
            or plan.get("capability_id") != close_cap
            or record.get("scope_id") != fresh.scope_id
            or plan.get("authorization_scope_id") != fresh.scope_id
            or record.get("effects") != ["session"]
            or plan.get("effects") != ["session"]
            or ((record.get("extra") or {}).get("last_refusal") or {}).get("code") != "E_CONFLICT"
            or plan.get("can_apply") is not True
            or target.get("mode") != "session"
            or target.get("engine_id") != "xpedition-schematic"
            or target.get("instance_id") != str(actual["pid"])
            or extension.get("command") != "session close"
            or extension.get("domain") != "schematic"
            or not same_identity(extension.get("process_identity") or {}, actual)
            or extension.get("save_project")
            or document.get("open") is not False
            or document.get("status") != "observed"
        ):
            _fail("close_preparation_sdk_not_proven_not_started")
        fresh.require(effects=["session"], target=target)
        config_root = C._modules().L._plain_path(config_root)

        def revalidate():
            context, recovered = original_start(
                "schematic", actual, startup_id, get_record, validator, fresh.scope_id
            )
            registered = _read(path=config_root / "native-ownership.json").get(
                _key("schematic", actual)
            )
            start_record = get_record(startup_id)
            start_result = ((start_record.get("receipt") or {}).get("extensions") or {}).get(
                "result"
            ) or {}
            if (
                registered is None
                or not _pointer_equivalent(registered, recovered)
                or context.get("startup_operation_id") != startup_id
                or start_result.get("owner_ref") != reference
            ):
                _fail("close_preparation_original_start_or_registry_changed")

        revalidate()
        private = C._regular_json(C._locator_path(reference, config_root))
        locator = C.OwnerLocator(C._SEAL, reference, private, revalidate)
        owner, ticket, root, key = C._ticket(locator)
        _, _, intent = owner._context(ticket)
        editing = intent["editing_scope"]
        with Path(fresh.grant_file).open("rb") as stream:
            scope_bytes = stream.read(65537)
        if (
            len(scope_bytes) > 65536
            or editing.get("scope_id") != fresh.scope_id
            or editing.get("scope_sha256") != hashlib.sha256(scope_bytes).hexdigest()
        ):
            _fail("close_preparation_original_scope_bytes_changed")
        count = _prepared_command_census(owner, ticket, root, key, intent, operation_id, editing)
        revalidate()
        if get_record(operation_id) != record:
            _fail("close_preparation_sdk_record_changed")
        return PreparedCloseObservation(
            _PREPARED_CLOSE_SEAL,
            state,
            {
                "protocol": PREPARED_CLOSE_PROTOCOL,
                "original_close_operation_id": operation_id,
                "original_startup_operation_id": startup_id,
                "owner_ref": reference,
                "original_close_status": "planned",
                "original_resolution_null": True,
                "owner_request_inventory_complete": True,
                "identity_request_count": count,
                "close_request_count": 0,
                "write_request_count": 0,
                "quit_dispatched": False,
                "historical_receipt_changed": False,
            },
        )
    except CLIError:
        raise
    except Exception:
        _fail("close_preparation_evidence_incomplete")
