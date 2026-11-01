"""Public SDK context for opening one registered copy in the existing owner.

This neither starts nor adopts an application. The original empty startup stays
immutable; project admission is a separate operation and association.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .audit import config_dir
from .errors import CLIError

ROUTE = "owned-registered-project-first-open-v1"
NATIVE_OPEN_RESERVE_SECONDS = 60.0
_ID = re.compile(r"op-[A-Za-z0-9_-]{1,100}\Z")
_CURRENT = ContextVar("designer_project_admission", default=None)
_SEAL = object()


def _fail(reason, code="E_CONFLICT", *, dispatched=False):
    raise CLIError(
        code,
        "Dedicated project admission refused",
        {
            "reason": reason,
            "write_attempted": dispatched,
            "native_request_dispatched": dispatched,
        },
    )


def _options(value):
    return {key.replace("-", "_"): item for key, item in (value or {}).items()}


def requested(command, options):
    return command == "session attach" and bool(
        _options(options).get("project_source_operation_id")
    )


def validate_intent(options, target=None):
    value = _options(options)
    extension = (target or {}).get("extensions") or {}
    source = extension.get("designer_project_source") or {}
    if not value and source:
        value = {
            "kind": extension.get("domain"),
            "project": extension.get("project"),
            "pid": (target or {}).get("instance_id"),
            "project_source_operation_id": source.get("copy_operation_id"),
        }
    if (
        value.get("kind") != "schematic"
        or not isinstance(value.get("project"), str)
        or not Path(value["project"]).is_absolute()
        or not _ID.fullmatch(str(value.get("project_source_operation_id")))
        or str(value.get("pid", "")).isdecimal() is False
        or int(value.get("pid", 0)) <= 0
        or value.get("startup_operation_id")
        or value.get("host_bootstrap")
    ):
        _fail("exact_owned_project_source_selector_required", "E_USAGE")
    if target is not None and (
        extension.get("designer_project_open_route") != ROUTE
        or source.get("copy_operation_id") != value["project_source_operation_id"]
        or source.get("project") != str(Path(value["project"]).resolve())
        or source.get("open_operation_id") != extension.get("source_operation_id")
    ):
        _fail("original_first_open_plan_binding_required")
    return value


class _Admission:
    def __init__(self, seal, adapter, scope, operation_id, options, capsule):
        if seal is not _SEAL:
            _fail("typed_admission_context_required")
        self.adapter, self.scope, self.operation_id = adapter, scope, operation_id
        self.options, self.capsule = validate_intent(options), copy.deepcopy(capsule)


@contextmanager
def bind_sdk_admission(adapter, scope, operation_id, options, capsule=None):
    if scope is None or not scope.granted or not _ID.fullmatch(str(operation_id)):
        _fail("original_scoped_sdk_operation_required", "E_FORBIDDEN")
    context = _Admission(_SEAL, adapter, scope, operation_id, options, capsule)
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)


def _context():
    result = _CURRENT.get()
    if type(result) is not _Admission:
        _fail("original_scoped_sdk_operation_required")
    return result


def _generation_reader(context, actual):
    from . import native_process as P
    from .designer_owner_recovery import _observe_absent
    from .session import read_state
    from .session_ownership import pointer_for

    state = read_state()
    if state.get("domain") != "schematic" or not P.same_identity(
        state.get("process_identity") or {}, actual
    ):
        _fail("current_original_dedicated_generation_required")
    original, pointer = context.adapter._ownership(
        "schematic",
        actual,
        source_id=state.get("startup_operation_id"),
        scope_id=context.scope.scope_id,
    )
    if pointer_for("schematic", actual) != pointer or not state.get("owner_ref"):
        _fail("current_original_start_registry_required")
    launchers = P.owned_startup_launchers(original.get("startup_provenance"), actual)

    def observe():
        rows = []
        for role, identity in [("worker", actual), *(("launcher", p) for p in launchers)]:
            try:
                with P.ProcessHandle("schematic", identity["pid"], identity) as pinned:
                    alive = not pinned.exited()
            except (OSError, ValueError):
                # An exited original launcher may have lost its image getter;
                # only the original birth/handle absence proof can explain it.
                _observe_absent(identity)
                alive = False
            if role == "worker" and not alive:
                _fail("original_worker_generation_ended")
            rows.append(
                {"role": role, "domain": "schematic", "process_identity": identity, "alive": alive}
            )
        return rows

    return observe


def build_target_binding(actual):
    from .designer_first_open import build_closed_copy_open_binding
    from .designer_owner_client import validate_owner_ref
    from .session import read_state

    context = _context()
    value = context.options
    project = str(Path(value["project"]).resolve())
    context.scope.require(effects=["session", "persist"], paths=[(project, "persist")])
    reference = validate_owner_ref(read_state().get("owner_ref"))
    grant = Path(context.scope.grant_file).resolve()
    editing = {
        "scope_file": str(grant),
        "scope_id": context.scope.scope_id,
        "scope_sha256": hashlib.sha256(grant.read_bytes()).hexdigest(),
    }
    capsule = build_closed_copy_open_binding(
        project=project,
        source_operation_id=value["project_source_operation_id"],
        editing_scope=editing,
        sdk_operation_id=context.operation_id,
        owner_epoch_sha256=reference["owner_epoch_sha256"],
        trusted_config_root=config_dir(),
        own_generation_reader=_generation_reader(context, actual),
    )
    if context.capsule is not None and context.capsule != capsule:
        _fail("confirmed_first_open_source_changed")
    context.capsule = copy.deepcopy(capsule)
    return capsule


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def open_registered_project(options):
    """Existing session-attach leaf: one first open, never ROT attachment."""
    from .cli.common import confirmed, native, previewed
    from .cli.sessions import _require_no_pending_close, _verify_requested_document
    from .designer_first_open import last_observation_report
    from .native_process import ProcessHandle, same_identity
    from .session import _write_state, read_state

    context = _context()
    value = validate_intent(options)
    if any(
        value.get(k) != context.options.get(k)
        for k in ("project", "pid", "project_source_operation_id")
    ):
        _fail("handler_first_open_context_differs")
    _require_no_pending_close()
    state = read_state()
    actual = state.get("process_identity") or {}
    if not same_identity(actual, actual) or int(value["pid"]) != actual["pid"]:
        _fail("original_dedicated_process_required")
    capsule = build_target_binding(actual)
    binding_sha256 = _digest(capsule)
    # The same fresh, bounded allocation is used by preview and confirmation.
    # Complete source verification runs inside this owner call before Open;
    # reserve separate time for the native Open and exact document identity.
    native_open_seconds = (
        capsule["observation_budget"]["total_seconds"] + NATIVE_OPEN_RESERVE_SECONDS
    )
    token_scope = {
        "operation": "designer_registered_project_first_open",
        "project": capsule["project"],
        "project_source_operation_id": value["project_source_operation_id"],
        "binding_sha256": binding_sha256,
    }
    if options.get("dry_run"):
        return previewed(
            {
                **token_scope,
                "created": False,
                "source_kind": capsule.get("source_kind"),
                "observation_budget": capsule.get("observation_budget"),
                "source_observation_report": last_observation_report(),
                "native_open_observation_seconds": native_open_seconds,
                "changes": [
                    {"action": "open_registered_project_once", "project": capsule["project"]}
                ],
                "risk": {
                    "tier": "T1",
                    "blast_radius": "Only this registered copy in the original dedicated owner",
                },
            },
            token_scope,
        )
    try:
        confirmed(options, token_scope)
    except CLIError as error:
        raise CLIError(
            error.code,
            error.message,
            {
                **(error.details or {}),
                "failed_stage": "confirm_first_open_scope",
                "write_attempted": False,
                "native_request_dispatched": False,
            },
        ) from error
    backend = native()
    with ProcessHandle("schematic", actual["pid"], actual):
        before = backend.invoke("identity", {"domain": "schematic"})["application"]
        document = before.get("document") or {}
        if (
            document.get("status") != "observed"
            or document.get("open") is not False
            or document.get("path")
        ):
            _fail("observed_empty_original_owner_required")
        if read_state() != state:
            _fail("original_session_metadata_changed_before_first_open")
        pending = {
            **state,
            "active_bound_project": capsule["project"],
            "project_source_operation_id": value["project_source_operation_id"],
            "project_admission_operation_id": context.operation_id,
            "project_admission_binding": copy.deepcopy(capsule),
            "project_admission_observation": "pending_native_open",
        }
        _write_state(pending)
        if read_state() != pending:
            _fail("first_open_pending_association_not_retained", "E_IO")
        result = backend.invoke(
            "open",
            {
                "domain": "schematic",
                "project": capsule["project"],
                "pid": actual["pid"],
                "start": False,
            },
            timeout_seconds=native_open_seconds,
        )
        if result.get("opened") is not True:
            _fail("native_first_open_not_acknowledged", "E_VERIFY_INCOMPLETE", dispatched=True)
        try:
            after = backend.invoke("identity", {"domain": "schematic"})["application"]
            _verify_requested_document(after, capsule["project"], "schematic")
        except CLIError as error:
            raise CLIError(
                error.code,
                error.message,
                {
                    **(error.details or {}),
                    "write_attempted": True,
                    "native_request_dispatched": True,
                    "first_open_returned": True,
                    "failed_stage": "post_open_identity",
                },
            ) from error
    fresh = read_state()
    if fresh != pending:
        _fail("original_session_metadata_changed_during_first_open", dispatched=True)
    fresh.update(
        active_bound_project=capsule["project"],
        project_source_operation_id=value["project_source_operation_id"],
        project_admission_operation_id=context.operation_id,
        project_admission_binding=copy.deepcopy(capsule),
        project_admission_observation="native_open_returned_pending_sdk_verification",
    )
    _write_state(fresh)
    if read_state() != fresh:
        _fail("first_open_association_not_retained", "E_IO", dispatched=True)
    return {
        "domain": "schematic",
        "application": (after.get("public_fields") or {}).get("Name"),
        "pid": actual["pid"],
        "process_identity": actual,
        "started": False,
        "attached": True,
        "opened": True,
        "created": False,
        "path": capsule["project"],
        "owner_ref": state["owner_ref"],
        "startup_operation_id": state["startup_operation_id"],
        "project_source_operation_id": value["project_source_operation_id"],
        "first_open_binding_sha256": binding_sha256,
        "first_open": result,
        "_untrusted": ["path", "first_open"],
    }


def require_admission_receipt(adapter, scope, state, current_operation_id, command):
    """A project-open unknown cannot silently authorize a later engineering write."""
    from ._vendor.tool_driver_sdk.store import target_key
    from .native_process import same_identity

    observations = {
        "session inspect",
        "session close",
        "schematic parts",
        "schematic nets",
        "schematic sheets",
        "bom inspect",
        "bom check",
    }
    operation_id = state.get("project_admission_operation_id")
    if operation_id is None:
        if (
            any(
                state.get(key) is not None
                for key in (
                    "active_bound_project",
                    "project_source_operation_id",
                    "project_admission_binding",
                )
            )
            and command not in observations
        ):
            _fail("project_association_missing_original_operation_no_writes")
        return
    capsule = state.get("project_admission_binding")
    try:
        grant_sha = hashlib.sha256(Path(scope.grant_file).read_bytes()).hexdigest()
        association_matches = (
            isinstance(capsule, dict)
            and capsule.get("open_operation_id") == operation_id
            and capsule.get("copy_operation_id") == state.get("project_source_operation_id")
            and capsule.get("project") == state.get("active_bound_project")
            and capsule.get("scope_id") == scope.scope_id
            and capsule.get("scope_sha256") == grant_sha
            and capsule.get("owner_epoch_sha256")
            == (state.get("owner_ref") or {}).get("owner_epoch_sha256")
        )
    except Exception:
        association_matches = False
    context = _CURRENT.get()
    if (
        type(context) is _Admission
        and context.operation_id == operation_id == current_operation_id
        and command == "session attach"
        and context.scope.scope_id == scope.scope_id
        and context.capsule == capsule
        and association_matches
    ):
        return  # The same confirmed open still owes its independent SDK readback.
    record = adapter.ownership_record_get(operation_id)
    receipt = (record or {}).get("receipt") or {}
    plan = (record or {}).get("plan") or {}
    result = (receipt.get("extensions") or {}).get("result") or {}
    try:
        adapter.ownership_validator.validate("plan", plan)
        adapter.ownership_validator.validate("receipt", receipt)
        target = plan.get("target") or {}
        extension = target.get("extensions") or {}
        after = receipt.get("snapshot_after") or {}
        facts = (after.get("extensions") or {}).get("facts") or {}
        app = facts.get("application") or {}
        document = app.get("document") or {}
        verification = (receipt.get("outcome") or {}).get("verification") or {}
        identity = state.get("process_identity") or {}
        valid = (
            association_matches
            and record.get("status") == "succeeded"
            and record.get("command") == "session attach"
            and record.get("kind") == "single"
            and record.get("resolution") is None
            and record.get("operation_id")
            == operation_id
            == plan.get("operation_id")
            == receipt.get("operation_id")
            and bool(record.get("plan_id"))
            and record.get("plan_id") == plan.get("plan_id") == receipt.get("plan_id")
            and bool(record.get("capability_id"))
            and record.get("capability_id")
            == plan.get("capability_id")
            == receipt.get("capability_id")
            and record.get("effects") == plan.get("effects") == receipt.get("effects")
            and set(record.get("effects") or []) in ({"session"}, {"session", "persist"})
            and record.get("scope_id") == scope.scope_id
            and plan.get("authorization_scope_id")
            == receipt.get("authorization_scope_id")
            == scope.scope_id
            and plan.get("can_apply") is True
            and receipt.get("target") == target
            and target.get("mode") == "session"
            and target.get("engine_id") == "xpedition-schematic"
            and record.get("target_key") == target_key(target)
            and receipt.get("snapshot_before") == plan.get("base_snapshot")
            and extension.get("command") == "session attach"
            and extension.get("domain") == "schematic"
            and extension.get("designer_project_open_route") == ROUTE
            and extension.get("designer_project_source") == capsule
            and extension.get("project") == state.get("active_bound_project")
            and target.get("object_id") == state.get("active_bound_project")
            and target.get("locator") == Path(state["active_bound_project"]).as_uri()
            and same_identity(extension.get("process_identity") or {}, identity)
            and str(target.get("instance_id")) == str(identity.get("pid"))
            and (receipt.get("outcome") or {}).get("execution") == "completed"
            and verification.get("status") == "passed"
            and verification.get("coverage") == "complete"
            and verification.get("snapshot_id") == after.get("id")
            and adapter.ownership_validator.receipt_rule(receipt).get("ok") is True
            and result.get("opened") is True
            and result.get("created") is False
            and result.get("owner_ref") == state.get("owner_ref")
            and same_identity(result.get("process_identity") or {}, identity)
            and same_identity(facts.get("os_process_identity") or {}, identity)
            and result.get("pid") == app.get("pid") == identity.get("pid")
            and result.get("startup_operation_id") == state.get("startup_operation_id")
            and isinstance(facts.get("native"), dict)
            and document.get("status") == "observed"
            and document.get("open") is True
            and Path(document.get("path", "")).resolve()
            == Path(state["active_bound_project"]).resolve()
            and result.get("path") == state.get("active_bound_project")
            and result.get("project_source_operation_id")
            == state.get("project_source_operation_id")
            and result.get("first_open_binding_sha256")
            == _digest(state.get("project_admission_binding"))
        )
    except Exception:
        valid = False
    if not valid and command not in observations:
        _fail("original_project_admission_not_verified_no_writes")
