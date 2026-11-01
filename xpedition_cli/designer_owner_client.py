"""Private locator/client for a normally scoped SDK-owned Designer session.

Locators store no epoch or ticket. They are an address index, never ownership
authority: the Driver must revalidate the original SDK receipt/registry/scope
on every use. COM references and the dispatch ContextVar stay inside the owner.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .errors import CLIError

PROTOCOL = "designer-owned-session-v1"
METHODS = frozenset(
    {
        "identity",
        "clone_project",
        "open",
        "snapshot",
        "apply_changeset",
        "save",
        "close",
        "draw",
        "show",
        "release_project_preflight",
        "release_project",
        "reopen_project",
        "read_project",
        "export_pdf",
    }
)
_START = ContextVar("designer_sdk_start_request", default=None)
_ROUTING = ContextVar("designer_sdk_owner_routing", default=None)
_SEAL = object()


def _error(reason, code="E_HUMAN_REQUIRED"):
    return CLIError(
        code,
        "Dedicated Designer routing refused",
        {
            "reason": reason,
            "write_attempted": False,
            "native_request_dispatched": False,
            "action": "other",
            "resume": 'xpedition-cli reference --command "session start"',
            "next_action": "Use the original scoped SDK start/registry binding; "
            "do not adopt a PID or replay an unknown.",
        },
    )


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _modules():
    from . import designer_native_owner as owner

    return owner


def _regular_json(path):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or info.st_size > 1024 * 1024
    ):
        raise _error("private_locator_not_regular_or_bounded", "E_IO")
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise _error("private_locator_byte_limit", "E_IO")
    return json.loads(raw)


def validate_owner_ref(reference):
    fields = {"protocol", "owner_id", "owner_epoch_sha256", "source_bundle_sha256"}
    if (
        type(reference) is not dict
        or set(reference) != fields
        or reference.get("protocol") != PROTOCOL
    ):
        raise _error("safe_owner_reference_required", "E_VALIDATION")
    for name, length in (
        ("owner_id", 32),
        ("owner_epoch_sha256", 64),
        ("source_bundle_sha256", 64),
    ):
        if not isinstance(reference[name], str) or not re.fullmatch(
            r"[a-f0-9]{" + str(length) + "}", reference[name]
        ):
            raise _error("safe_owner_reference_required", "E_VALIDATION")
    return copy.deepcopy(reference)


class OwnerStartRequest:
    def __init__(self, seal, scope, operation_id, config_root, installation_root):
        if seal is not _SEAL:
            raise _error("typed_sdk_start_request_required")
        self.scope, self.sdk_operation_id = scope, operation_id
        self.config_root = Path(config_root)
        self.owner_id = secrets.token_hex(16)
        self.root = (
            self.config_root.parent
            / (self.config_root.name + "-designer-runtime")
            / ("owner-" + self.owner_id)
        )
        self.plan = {
            "scheme": "designer-native-empty-launch-v1",
            "installation_root": str(Path(installation_root)),
            "runtime_directory": str(self.root / "startup"),
        }
        path = Path(scope.grant_file).resolve()
        self.editing_scope = {
            "scope_file": str(path),
            "scope_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "scope_id": scope.scope_id,
        }
        self.source = _modules()._source_binding()
        self.locator = None
        self.started = False

    def __repr__(self):
        return "<TypedDesignerSDKStartRequest>"


def make_start_request(scope, sdk_operation_id, config_root, installation_root):
    from ._vendor.tool_driver_sdk.scope import Scope

    if (
        type(scope) is not Scope
        or not scope.granted
        or not re.fullmatch(r"op-[A-Za-z0-9_-]{1,100}", str(sdk_operation_id))
    ):
        raise _error("normal_scoped_sdk_start_required")
    scope.require(effects=["session"])
    return OwnerStartRequest(_SEAL, scope, sdk_operation_id, config_root, installation_root)


@contextmanager
def bind_start_request(request):
    if type(request) is not OwnerStartRequest:
        raise _error("typed_sdk_start_request_required")
    token = _START.set(request)
    try:
        yield request
    finally:
        _START.reset(token)


@contextmanager
def bind_owner_routing(provider, sdk_operation_id, authorization_binding):
    if not callable(provider):
        raise _error("driver_owner_resolver_required")
    token = _ROUTING.set((provider, str(sdk_operation_id), copy.deepcopy(authorization_binding)))
    try:
        yield
    finally:
        _ROUTING.reset(token)


class OwnerLocator:
    def __init__(self, seal, reference, private, revalidate, *, pending=None):
        if seal is not _SEAL or not callable(revalidate):
            raise _error("validated_owner_locator_required")
        self.reference, self.private = validate_owner_ref(reference), copy.deepcopy(private)
        self.revalidate, self.pending = revalidate, pending

    def __repr__(self):
        return "<ScopedDesignerOwnerLocator>"


def _locator_path(reference, config_root=None):
    if config_root is None:
        from .audit import config_dir

        config_root = config_dir()
    return Path(config_root) / "dedicated-owner-locators" / (reference["owner_id"] + ".json")


def _locator_capacity(config_root):
    directory = Path(config_root) / "dedicated-owner-locators"
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=False)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or directory.is_symlink()
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise _error("owner_locator_directory_not_regular", "E_IO")
    entries = list(itertools.islice(directory.iterdir(), 65))
    if len(entries) >= 64:
        raise _error("owner_locator_limit", "E_IO")
    for path in entries:
        info = path.lstat()
        if (
            not re.fullmatch(r"[a-f0-9]{32}\.json", path.name)
            or not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or path.is_symlink()
            or info.st_size > 1024 * 1024
        ):
            raise _error("owner_locator_inventory_incomplete", "E_IO")
    return directory


def pending_start_path(sdk_operation_id, config_root=None):
    if not re.fullmatch(r"op-[A-Za-z0-9_-]{1,100}", str(sdk_operation_id)):
        raise _error("original_sdk_start_id_required", "E_VALIDATION")
    if config_root is None:
        from .audit import config_dir

        config_root = config_dir()
    return Path(config_root) / "pending-starts" / (sdk_operation_id + ".json")


def read_pending_start(sdk_operation_id, config_root=None):
    """Address diagnostics only; a pending record never issues a locator/grant."""
    record = _regular_json(pending_start_path(sdk_operation_id, config_root))
    if record.get("sdk_operation_id") != sdk_operation_id or record.get("protocol") != PROTOCOL:
        raise _error("pending_start_binding_invalid", "E_IO")
    return record


def _publish_pending_start(request):
    path = pending_start_path(request.sdk_operation_id, request.config_root)
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=False)
    info = path.parent.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.parent.is_symlink()
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise _error("pending_start_directory_invalid", "E_IO")
    if len(list(itertools.islice(path.parent.iterdir(), 65))) >= 64:
        raise _error("pending_start_limit", "E_IO")
    record = {
        "protocol": PROTOCOL,
        "sdk_operation_id": request.sdk_operation_id,
        "owner_id": request.owner_id,
        "root": str(request.root),
        "plan": request.plan,
        "source_binding": request.source,
        "editing_scope": request.editing_scope,
        "authority_granted": False,
        "original_outcome": "unknown",
    }
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        if _regular_json(path) != record:
            raise ValueError("pending_start_not_retained")
    except (OSError, ValueError, KeyError, TypeError):
        error = _error("pending_start_publication_incomplete", "E_IO")
        error.details["state_write_attempted"] = True
        raise error from None


def resolve_owner(reference, revalidate):
    reference = validate_owner_ref(reference)
    if not callable(revalidate):
        raise _error("driver_original_start_revalidator_required")
    revalidate()  # Driver source/registry/scope validation precedes address lookup.
    private = _regular_json(_locator_path(reference))
    locator = OwnerLocator(_SEAL, reference, private, revalidate)
    _ticket(locator)
    return locator


def _ticket(locator):
    if type(locator) is not OwnerLocator:
        raise _error("validated_owner_locator_required")
    if locator.pending is not None:
        if _START.get() is not locator.pending:
            raise _error("pending_start_context_ended")
        _check_start(locator.pending)
    else:
        locator.revalidate()
    owner = _modules()
    root = owner.L._plain_path(Path(locator.private["root"]))
    with (root / "integrity-key.bin").open("rb") as stream:
        key = stream.read(33)
    if len(key) != 32:
        raise _error("owner_locator_key_incomplete", "E_IO")
    intent, _ = owner.L._read(root / "intent.json", key)
    epoch = intent.get("execution_epoch")
    if not isinstance(epoch, str) or not re.fullmatch(r"[a-f0-9]{32}", epoch):
        raise _error("owner_locator_epoch_invalid")
    ticket = owner.OwnerTicket(root, epoch)
    _, _, checked = owner._context(ticket)
    if (
        hashlib.sha256(epoch.encode()).hexdigest() != locator.reference["owner_epoch_sha256"]
        or checked.get("source_sha256") != locator.private.get("source_binding")
        or checked.get("source_sha256") != owner._source_binding()
        or _digest(checked.get("source_sha256")) != locator.reference["source_bundle_sha256"]
        or checked.get("editing_scope") != locator.private.get("editing_scope")
    ):
        raise _error("owner_locator_source_scope_or_epoch_mismatch")
    return owner, ticket, root, key


def _check_start(request):
    request.scope.require(effects=["session"])
    if (
        hashlib.sha256(Path(request.editing_scope["scope_file"]).read_bytes()).hexdigest()
        != request.editing_scope["scope_sha256"]
    ):
        raise _error("start_scope_file_changed", "E_CONFLICT")


def _authorization(locator):
    routed = _ROUTING.get()
    if routed is not None:
        _, operation_id, binding = routed
    elif locator.pending is not None and _START.get() is locator.pending:
        request = locator.pending
        operation_id = request.sdk_operation_id
        binding = {
            "command": "session start",
            "scope_id": request.editing_scope["scope_id"],
            "scope_sha256": request.editing_scope["scope_sha256"],
            "source_operation_id": operation_id,
        }
    else:
        raise _error("confirmed_driver_owner_context_required")
    if (
        type(binding) is not dict
        or not {"command", "scope_id", "scope_sha256", "source_operation_id"} <= set(binding)
        or set(binding)
        - {
            "command",
            "scope_id",
            "scope_sha256",
            "source_operation_id",
            "source_binding",
            "closed_copy_open",
            "pdf_export",
            "lifecycle_intent",
        }
        or ("source_binding" in binding and type(binding["source_binding"]) is not dict)
        or ("closed_copy_open" in binding and type(binding["closed_copy_open"]) is not dict)
        or ("pdf_export" in binding and type(binding["pdf_export"]) is not dict)
        or binding["source_operation_id"] != operation_id
        or binding["scope_id"] != locator.private["editing_scope"]["scope_id"]
        or binding["scope_sha256"] != locator.private["editing_scope"]["scope_sha256"]
    ):
        raise _error("driver_owner_authorization_binding_mismatch", "E_FORBIDDEN")
    return operation_id, binding


def _unknown(locator, operation_id, command_id, method):
    readonly = method in {"identity", "snapshot", "release_project_preflight", "read_project"}
    if readonly:
        details = {
            "readonly_verification_incomplete": True,
            "readonly_request_id": operation_id,
            "owner_command_id": command_id,
            "owner_ref": copy.deepcopy(locator.reference),
            "request_replayed": False,
            "write_attempted": False,
            "engineering_write_attempted": False,
            "owner_request_published": True if command_id is not None else None,
            "owner_command_received": None,
            "native_request_dispatched": True if command_id is not None else None,
            # The pinned SDK reserves dispatch_state for effectful operations,
            # which do not exist for a read-only preview/setup observation.
            "dispatch_state": "not_started",
            "dispatch_state_scope": "effectful_sdk_operation_only",
            "native_observation_dispatch_state": "dispatched"
            if command_id is not None
            else "unknown",
            "next_action": (
                "Inspect the original authenticated owner record; no engineering write was "
                "requested. Do not replay the original owner command."
            ),
            "owner_command_lookup": {
                "record_kind": "original_authenticated_owner_runtime",
                "owner_command_id": command_id,
                "queues_new_request": False,
            },
        }
        return CLIError(
            "E_BACKEND_UNAVAILABLE",
            "The read-only owner observation did not complete; inspect its original owner record",
            details,
        )
    return CLIError(
        "E_OUTCOME_UNKNOWN",
        "The original owner command has no observed terminal result; do not replay",
        {
            "sdk_operation_id": operation_id,
            "owner_command_id": command_id,
            "owner_ref": copy.deepcopy(locator.reference),
            "request_replayed": False,
            "write_attempted": method
            not in {"identity", "snapshot", "release_project_preflight", "read_project"},
            "native_request_dispatched": None,
        },
    )


def _service_observation(owner, ticket):
    try:
        return owner.observe_owner(ticket, timeout_seconds=0)
    except (OSError, ValueError, KeyError, TypeError):
        return {"phase": "unobserved", "owner_state": "unobservable"}


def _service_unavailable(observation, *, require_live=True, require_ready=False):
    if not isinstance(observation, dict):
        return True
    if observation.get("phase") in {
        "owner_unresolved",
        "owner_cleanup_failed",
        "owner_cleanup_complete",
        "native_launch_incomplete",
        "native_process_exit_observed",
        "owner_unavailable",
    }:
        return True
    state = observation.get("owner_state")
    if state in {"verified_exited", "identity_mismatch", "not_owned", "unavailable"}:
        return True
    if require_live and state != "verified_alive":
        return True
    return require_ready and observation.get("phase") not in {
        "public_dispatch_ready",
        "public_readonly_quarantine",
    }


def _original_result_observation_pending(observation, method):
    """A published command may outlive service availability; never resubmit it.

    Global event/snapshot publication is independent of the immutable command
    chain. An incomplete global read is not a terminal failure. During normal
    close, process exit is published before resource release and the final
    command receipt. Only the original bounded command observation may wait
    through those phases; none of them establishes command success.
    """
    if not isinstance(observation, dict):
        return False
    phase, state = observation.get("phase"), observation.get("owner_state")
    if phase == "unobserved" and state == "unobservable":
        return True
    return (
        method == "close"
        and phase
        in {"public_dispatch_stopping", "native_process_exit_observed", "owner_cleanup_complete"}
        and state in {"verified_alive", "verified_exited", "unobservable"}
    )


def _unavailable(locator, operation_id, command_id, method, observation, *, published):
    observation = observation if isinstance(observation, dict) else {}
    if not published and method != "start":
        # No command exists to have an unknown outcome. A retained earlier
        # command may remain unknown, but it is not this unsubmitted request.
        error = CLIError(
            "E_BACKEND_UNAVAILABLE",
            "The retained dedicated owner is not ready; no new native request was published",
            {
                "sdk_operation_id": operation_id,
                "owner_command_id": None,
                "owner_ref": copy.deepcopy(locator.reference),
                "request_replayed": False,
                "dispatch_state": "not_started",
                "next_action": "Inspect the retained owner and original operation; do not replay "
                "an earlier write or publish another request until the owner is ready.",
            },
        )
    else:
        error = _unknown(locator, operation_id, command_id, method)
    error.details.update(
        reason="owner_service_unavailable",
        new_request_published=published,
        owner_service_observation={
            key: observation.get(key)
            for key in ("phase", "owner_state", "connection_observation", "cleanup_verified")
        },
    )
    if not published and method != "start":
        error.details.update(write_attempted=False, native_request_dispatched=False)
    return error


def current_owner_locator(method="identity", params=None):
    request, routed = _START.get(), _ROUTING.get()
    locator = (
        request.locator
        if request is not None
        else (routed[0](method, dict(params or {"domain": "schematic"})) if routed else None)
    )
    if type(locator) is not OwnerLocator:
        raise _error("validated_owner_locator_required")
    _ticket(locator)
    return locator


def validate_routed_owner(reference):
    locator = current_owner_locator()
    if validate_owner_ref(reference) != locator.reference:
        raise _error("active_owner_reference_changed", "E_CONFLICT")
    return None  # Validation only, never identity/COM dispatch after Quit.


def call_owner(locator, method, params, timeout_seconds):
    if method not in METHODS or type(params) is not dict:
        raise _error("unreviewed_owner_adapter_method", "E_UNSUPPORTED")
    owner, ticket, _, _ = _ticket(locator)
    operation_id, binding = _authorization(locator)
    from .operation_budget import native_call_remaining

    def remaining_allocation():
        remaining = native_call_remaining(method)
        if remaining is not None and remaining <= 0:
            raise CLIError(
                "E_TIMEOUT",
                "The native-call budget expired before publishing an owner request",
                {
                    "reason": "native_call_setup_budget_exhausted",
                    "sdk_operation_id": operation_id,
                    "owner_request_published": False,
                    "write_attempted": False,
                    "native_request_dispatched": False,
                    "request_replayed": False,
                },
            )
        return remaining

    if method == "export_pdf" and (
        type(binding.get("pdf_export")) is not dict
        or _digest(params.get("_pdf_expectation")) != _digest(binding["pdf_export"])
        or binding["pdf_export"].get("generation_id") != operation_id
    ):
        raise _error("confirmed_pdf_export_binding_required", "E_FORBIDDEN")
    remaining = remaining_allocation()
    allocated = (
        min(float(timeout_seconds), remaining) if remaining is not None else float(timeout_seconds)
    )
    deadline = time.monotonic() + max(0.0, allocated)
    service = _service_observation(owner, ticket)
    if _service_unavailable(service, require_ready=True):
        raise _unavailable(locator, operation_id, None, method, service, published=False)
    if service.get("phase") == "public_readonly_quarantine" and (
        method not in {"identity", "read_project", "snapshot", "close"}
        or (method == "snapshot" and params.get("observe_only") is not True)
    ):
        raise _error("readonly_quarantine_blocks_engineering_write", "E_CONFLICT")
    remaining = remaining_allocation()
    if remaining is not None:
        deadline = min(deadline, time.monotonic() + remaining)
    from .designer_native_owner import OwnerCommandNotPublished

    try:
        answer = owner.submit_owner_adapter_command(
            ticket,
            method,
            copy.deepcopy(params),
            sdk_operation_id=operation_id,
            authorization_binding=binding,
            observation_seconds=min(0.25, max(0.0, deadline - time.monotonic())),
        )
    except OwnerCommandNotPublished as error:
        allowed = {
            "public_owner_verification_budget_reserved",
            "public_owner_not_currently_serviceable",
            "public_owner_closure_already_queued",
            "readonly_quarantine_blocks_engineering_write",
            "original_write_command_already_published_no_replay",
            "original_write_command_payload_changed_no_replay",
            "public_owner_command_record_limit",
            "owner_command_source_binding_changed",
            "live_original_owner_required",
            "public_owner_admission_lock_busy",
            "public_owner_admission_lock_incomplete",
            "public_owner_capacity_admission_unobserved",
            "public_owner_capacity_exact_empty_target_required",
            "public_owner_capacity_exact_project_required",
            "public_owner_capacity_scope_denied",
            "public_owner_capacity_explicit_archive_required",
            "public_owner_capacity_readonly_selector_required",
            "public_owner_pdf_records_invalid",
            "public_owner_pdf_log_limit",
            "public_owner_pdf_record_unstable",
        }
        reason = str(error) if str(error) in allowed else "public_owner_admission_incomplete"
        details = {
            "reason": reason,
            "sdk_operation_id": operation_id,
            "owner_command_id": None,
            "owner_request_published": False,
            "new_request_published": False,
            "owner_command_received": False,
            "dispatch_state": "not_started",
            "write_attempted": False,
            "engineering_write_attempted": False,
            "native_request_dispatched": False,
            "request_replayed": False,
            "next_action": "No new request was published. Preserve all original IDs; "
            "complete original verification or use an authorized checkpoint with explicit "
            "--leave-closed and --output "
            "and normal empty Quit before starting a new dedicated session.",
        }
        if error.capacity is not None:
            details["owner_capacity"] = copy.deepcopy(error.capacity)
        raise CLIError(
            error.code
            if error.code in {"E_CONFLICT", "E_FORBIDDEN", "E_VALIDATION"}
            else "E_CONFLICT",
            "Dedicated owner refused this request before publication",
            details,
        ) from None
    except (OSError, ValueError, KeyError, TypeError) as error:
        command_id = getattr(error, "command_id", None)
        if not isinstance(command_id, str) or not re.fullmatch(
            r"owner-command-[a-f0-9]{32}", command_id
        ):
            command_id = None
        unknown = _unknown(locator, operation_id, command_id, method)
        unknown.details["owner_request_published"] = getattr(error, "owner_request_published", None)
        raise unknown from None
    command_id = answer.get("command_id")
    if not isinstance(command_id, str) or not re.fullmatch(
        r"owner-command-[a-f0-9]{32}", command_id
    ):
        raise _unknown(locator, operation_id, None, method)
    while True:
        if answer.get("command_id") != command_id:
            raise _unknown(locator, operation_id, command_id, method)
        events = answer.get("observations") or []
        if events and events[-1].get("phase") == "command_returned":
            result = events[-1].get("details", {}).get("result")
            if not isinstance(result, dict):
                raise _unknown(locator, operation_id, command_id, method)
            return copy.deepcopy(result)
        if events and events[-1].get("phase") == "command_failed":
            failure = events[-1].get("details", {}).get("adapter_error") or {}
            details = failure.get("details") or {}
            if (
                details.get("native_request_dispatched") is False
                and details.get("native_call_unknown") is False
            ):
                from .error_contract import CODES

                code = failure.get("code")
                if code in CODES:
                    raise CLIError(
                        code,
                        "Dedicated owner rejected the operation before native dispatch",
                        {
                            **copy.deepcopy(details),
                            "sdk_operation_id": operation_id,
                            "owner_command_id": command_id,
                            "request_replayed": False,
                            "write_attempted": False,
                        },
                    )
            unknown = _unknown(locator, operation_id, command_id, method)
            # This authenticated same-ID failure is additional evidence, not a
            # native terminal-success claim or permission to replay a write.
            unknown.details.update(
                owner_command_terminal_phase="command_failed",
                native_failure={"code": failure.get("code"), "details": copy.deepcopy(details)},
                _untrusted=["native_failure.details"],
            )
            if details.get("diagnostic_incomplete") is True:
                unknown.message = (
                    "The owner command ended with incomplete diagnostic evidence; "
                    "inspect the original ID, do not replay"
                )
                unknown.details.update(
                    diagnostic_incomplete=True,
                    owner_command_received=True,
                    native_request_dispatched=True,
                )
                for name in (
                    "native_call_returned",
                    "native_metadata_call_entered",
                    "engineering_write_attempted",
                    "fixed_document_member_attempted",
                    "fixed_document_cleanup_observed",
                ):
                    value = details.get(name)
                    unknown.details[name] = value if type(value) is bool else None
                if method in {"identity", "snapshot", "release_project_preflight", "read_project"}:
                    unknown.details["engineering_write_attempted"] = False
                if details.get("engineering_write_attempted") is False:
                    unknown.details["write_attempted"] = False
            raise unknown
        service = _service_observation(owner, ticket)
        if _service_unavailable(service) and not _original_result_observation_pending(
            service, method
        ):
            # Final same-ID read closes the cleanup/result publication window;
            # it never queues another command or re-enters COM.
            try:
                final = owner.observe_owner_command(ticket, command_id, timeout_seconds=0)
            except (OSError, ValueError, KeyError, TypeError):
                final = {}
            events = final.get("observations") or []
            if (
                final.get("command_id") == command_id
                and events
                and events[-1].get("phase") in {"command_returned", "command_failed"}
            ):
                answer = final
                continue
            raise _unavailable(locator, operation_id, command_id, method, service, published=True)
        if time.monotonic() >= deadline:
            raise _unknown(locator, operation_id, command_id, method)
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        try:
            answer = owner.observe_owner_command(
                ticket, command_id, timeout_seconds=min(0.25, max(0.0, deadline - time.monotonic()))
            )
        except (OSError, ValueError, KeyError, TypeError):
            answer = {"command_id": command_id, "observations": []}


def _start(request, timeout_seconds):
    deadline = time.monotonic() + max(0, float(timeout_seconds))
    if type(request) is not OwnerStartRequest or _START.get() is not request or request.started:
        raise _error("single_confirmed_sdk_start_required")
    _check_start(request)
    owner = _modules()
    if request.root.exists():
        raise _error("fresh_owner_root_required", "E_CONFLICT")
    _locator_capacity(request.config_root)  # Capacity errors precede any owner/CAD dispatch.
    _publish_pending_start(request)  # Durable original-ID address precedes owner launch.
    request.started = True  # Never relaunch after an unknown call/observation.
    request.root.parent.mkdir(parents=True, exist_ok=True)
    try:
        answer = owner.launch_public_owner(
            request.root, request.plan, request.editing_scope, observation_seconds=0.5
        )
    except (OSError, ValueError, KeyError, TypeError):
        raise CLIError(
            "E_OUTCOME_UNKNOWN",
            "The original dedicated-owner start has no complete observation; do not relaunch",
            {
                "sdk_operation_id": request.sdk_operation_id,
                "owner_id": request.owner_id,
                "write_attempted": True,
                "request_replayed": False,
            },
        ) from None
    ticket = answer["ticket"]
    root, key, intent = owner._context(ticket)
    if Path(root).resolve() != request.root.resolve():
        raise CLIError(
            "E_OUTCOME_UNKNOWN",
            "Dedicated owner returned a different private root",
            {
                "sdk_operation_id": request.sdk_operation_id,
                "owner_id": request.owner_id,
                "write_attempted": True,
                "request_replayed": False,
            },
        )
    reference = {
        "protocol": PROTOCOL,
        "owner_id": request.owner_id,
        "owner_epoch_sha256": hashlib.sha256(ticket.epoch.encode()).hexdigest(),
        "source_bundle_sha256": _digest(request.source),
    }
    private = {
        "root": str(root),
        "source_binding": request.source,
        "editing_scope": request.editing_scope,
    }
    request.locator = OwnerLocator(
        _SEAL, reference, private, lambda: _check_start(request), pending=request
    )
    _ticket(request.locator)
    path = _locator_path(reference, request.config_root)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(private, stream, sort_keys=True)
        if _regular_json(path) != private:
            raise ValueError("private_locator_not_retained")
    except (OSError, ValueError, KeyError, TypeError):
        error = _unknown(request.locator, request.sdk_operation_id, None, "start")
        error.details["state_write_attempted"] = True
        raise error from None
    observation = answer["observation"]
    if _service_unavailable(observation, require_live=False):
        raise _unavailable(
            request.locator, request.sdk_operation_id, None, "start", observation, published=False
        )
    while observation.get("phase") != "public_dispatch_ready":
        if time.monotonic() >= deadline:
            raise _unknown(request.locator, request.sdk_operation_id, None, "start")
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        observation = owner.observe_owner(
            ticket, timeout_seconds=min(0.25, max(0, deadline - time.monotonic()))
        )
        if _service_unavailable(observation, require_live=False):
            raise _unavailable(
                request.locator,
                request.sdk_operation_id,
                None,
                "start",
                observation,
                published=False,
            )
    try:
        identity = call_owner(
            request.locator,
            "identity",
            {"domain": "schematic"},
            max(0, deadline - time.monotonic()),
        )
    except CLIError as error:
        incomplete = _unknown(
            request.locator,
            request.sdk_operation_id,
            error.details.get("owner_command_id"),
            "start",
        )
        for key in ("reason", "new_request_published", "owner_service_observation"):
            if key in error.details:
                incomplete.details[key] = copy.deepcopy(error.details[key])
        raise incomplete from None
    launch, _ = owner.L._read(root / "native-launch-evidence.json", key)
    generation = launch["expected_native_generation"]
    actual = {
        "pid": generation["pid"],
        "executable": generation["executable"],
        "creation_time_100ns": generation["birth_filetime_100ns"],
    }
    app = identity["application"]
    if app.get("pid") != actual["pid"] or (app.get("document") or {}).get("open") is not False:
        raise _unknown(request.locator, request.sdk_operation_id, None, "start")
    from .native_process import resolve_startup_process, same_identity

    seed = launch["startup_seed"]
    try:
        resolved = resolve_startup_process("schematic", seed, app)
    except (OSError, ValueError, KeyError, TypeError) as error:
        incomplete = _unknown(request.locator, request.sdk_operation_id, None, "start")
        incomplete.details.update(
            reason="owner_startup_process_resolution_incomplete", error_type=type(error).__name__
        )
        raise incomplete from None
    if not isinstance(resolved, dict) or not same_identity(
        resolved.get("process_identity"), actual
    ):
        incomplete = _unknown(request.locator, request.sdk_operation_id, None, "start")
        incomplete.details["reason"] = "owner_startup_process_identity_mismatch"
        raise incomplete
    return {
        "started": True,
        "domain": "schematic",
        "pid": actual["pid"],
        "executable": actual["executable"],
        "process_identity": actual,
        "attached": True,
        "launcher_pid": seed["launcher_identity"]["pid"],
        "launcher_identity": copy.deepcopy(seed["launcher_identity"]),
        "startup_provenance": resolved["startup_provenance"],
        "startup_seed": seed,
        "application_identity": app,
        "adapter_identity": identity.get("adapter"),
        "application_document": app["document"],
        "automation_ready": True,
        "owner_ref": reference,
        "requested_project": None,
    }


_PCB_ONLY_METHODS = frozenset(
    {
        "batch_drc",
        "board_geometry",
        "board_info",
        "board_view_state",
        "cell_refresh_state",
        "hand_route",
        "manufacturing_output",
        "move_component",
        "outline_state",
        "placement_batch",
        "render_board",
        "routepass_state",
        "show_board",
    }
)


def _pcb_invocation(method, domain):
    # A .prj names both a schematic and its PCB. Only methods whose native
    # implementations are Layout-specific can establish the domain by name;
    # ambiguous lifecycle/snapshot methods still need an explicit PCB domain.
    return domain in {"pcb", "layout"} or (not domain and method in _PCB_ONLY_METHODS)


def route_invocation(method, params, timeout_seconds):
    """Return (handled, result); explicit PCB requests never use this route."""
    if method == "designer_host_bootstrap":
        return False, None
    selection = params or {}
    domain = str(selection.get("domain") or selection.get("kind") or "").lower()
    if _pcb_invocation(method, domain):
        return False, None
    designer = (
        domain in {"schematic", "designer"}
        or method in {"draw", "clone_project"}
        or str(selection.get("project") or selection.get("path") or "")
        .lower()
        .endswith((".prj", ".dproj"))
    )
    if not designer:
        return False, None
    request, routed = _START.get(), _ROUTING.get()
    if request is None and routed is None:
        return False, None
    if method == "start":
        if request is None and routed is not None:
            request = routed[0](method, copy.deepcopy(selection))
        if type(request) is not OwnerStartRequest:
            raise _error("typed_sdk_start_request_required")
        if _START.get() is request:
            return True, _start(request, timeout_seconds)
        with bind_start_request(request):
            return True, _start(request, timeout_seconds)
    locator = (
        request.locator if request is not None else routed[0](method, copy.deepcopy(selection))
    )
    if type(locator) is not OwnerLocator:
        raise _error("validated_owner_locator_required")
    return True, call_owner(locator, method, selection, timeout_seconds)


def owner_route_available(method, params):
    if method == "designer_host_bootstrap" or (_START.get() is None and _ROUTING.get() is None):
        return False
    selection = params or {}
    domain = str(selection.get("domain") or selection.get("kind") or "").lower()
    if _pcb_invocation(method, domain):
        return False
    return (
        domain in {"schematic", "designer"}
        or method in {"draw", "clone_project"}
        or str(selection.get("project") or selection.get("path") or "")
        .lower()
        .endswith((".prj", ".dproj"))
    )
