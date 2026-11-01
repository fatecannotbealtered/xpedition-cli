"""SDK producer context for the dedicated Designer connection.

No reference or JSON flag grants authority. Normal calls re-read the original
SDK start and registry. Only the in-flight confirmed start can use its freshly
created typed locator before the SDK has stored and registered its receipt.
"""

from __future__ import annotations

import hashlib
import secrets
from contextlib import nullcontext
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from .audit import config_dir
from .errors import CLIError

_PREPARED_CLOSE = ContextVar("designer_prepared_close_observation", default=None)


def current_prepared_close():
    return _PREPARED_CLOSE.get()


def prepared_close_observation(adapter, scope):
    from .session import read_state
    from .session_ownership import observe_prepared_close

    return observe_prepared_close(
        read_state(),
        scope,
        getattr(adapter, "ownership_record_get", None),
        getattr(adapter, "ownership_validator", None),
        config_dir(),
    )


def _scope(adapter, call=None):
    value = (getattr(call, "extras", None) or {}).get("scope")
    if value is None:
        getter = getattr(adapter, "ownership_scope", None)
        value = getter() if callable(getter) else None
    return value


def previous_start_observation(adapter, scope, *, expected_summary=None):
    from .designer_owner_recovery import observe_exited_failed_start
    from .session import read_state

    return observe_exited_failed_start(
        read_state(),
        scope,
        getattr(adapter, "ownership_record_get", None),
        getattr(adapter, "ownership_validator", None),
        config_dir(),
        expected_summary=expected_summary,
    )


def _binding(scope, command, operation_id, plan=None, *, options=None, target=None):
    if scope is None or not scope.granted:
        return None
    path = Path(scope.grant_file)
    value = {
        "command": command,
        "scope_id": scope.scope_id,
        "scope_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_operation_id": operation_id,
    }
    leave_closed = (
        (target.get("extensions") or {}).get("designer_leave_closed_requested")
        if target is not None
        else (options or {}).get("leave_closed", (options or {}).get("leave-closed"))
    )
    output = (
        (target.get("extensions") or {}).get("designer_lifecycle_output")
        if target is not None
        else (options or {}).get("output")
    )
    if command == "session close" or (
        command == "project backup" and leave_closed is True and isinstance(output, str)
    ):
        value["lifecycle_intent"] = {
            "scheme": "designer-owner-lifecycle-v1",
            "kind": "empty_quit" if command == "session close" else "leave_closed_checkpoint",
        }
        if command == "session close":
            pid = target.get("instance_id") if target is not None else (options or {}).get("pid")
            value["lifecycle_intent"]["pid"] = int(pid) if str(pid).isdecimal() else None
            if value["lifecycle_intent"]["pid"] is None or value["lifecycle_intent"]["pid"] <= 0:
                value.pop("lifecycle_intent")
        else:
            project = (
                (target.get("extensions") or {}).get("path")
                if target is not None
                else (options or {}).get("project")
            )
            value["lifecycle_intent"]["project"] = (
                str(Path(project).resolve()) if isinstance(project, str) else None
            )
            value["lifecycle_intent"]["output"] = str(Path(output).resolve())
    if command == "project create" and plan is not None:
        source = (
            ((plan.get("base_snapshot") or {}).get("extensions") or {})
            .get("facts", {})
            .get("designer_setup_source")
        )
        if source is not None:
            value["source_binding"] = source
    if command == "session attach" and plan is not None:
        capsule = ((plan.get("target") or {}).get("extensions") or {}).get(
            "designer_project_source"
        )
        if capsule is not None:
            value["closed_copy_open"] = capsule
    if command == "schematic export" and plan is not None:
        expected = (plan.get("extensions") or {}).get("schematic_pdf_artifact")
        if isinstance(expected, dict):
            import copy

            value["pdf_export"] = copy.deepcopy(expected)
    return value


def _provider(adapter, scope, request, current_operation_id=None, command=None):
    def resolve(*_request_args, **_request_kwargs):
        from .designer_owner_client import resolve_owner
        from .session import read_state
        from .session_ownership import pointer_for

        if request is not None and request.locator is not None:
            return request.locator
        state = read_state()
        reference = state.get("owner_ref")
        if reference is None:
            return None

        def revalidate(*_args, **_kwargs):
            if scope is None or not scope.granted:
                raise CLIError("E_FORBIDDEN", "The original dedicated task scope is unavailable")
            actual, startup_id = state.get("process_identity"), state.get("startup_operation_id")
            if not isinstance(actual, dict) or not isinstance(startup_id, str):
                raise CLIError("E_CONFLICT", "Dedicated original-start identity is incomplete")
            context, pointer = adapter._ownership(
                "schematic", actual, source_id=startup_id, scope_id=scope.scope_id
            )
            if pointer is None or pointer_for("schematic", actual) != pointer:
                raise CLIError("E_CONFLICT", "Dedicated original-start registry is unavailable")
            record = adapter.ownership_record_get(startup_id)
            result = ((record.get("receipt") or {}).get("extensions") or {}).get("result") or {}
            if (
                result.get("owner_ref") != reference
                or context.get("startup_operation_id") != startup_id
            ):
                raise CLIError("E_CONFLICT", "Dedicated owner differs from the original SDK start")
            if context.get("requested_project"):
                scope.require(
                    effects=["observe"], paths=[(context["requested_project"], "observe")]
                )
            from .designer_project_admission import require_admission_receipt

            require_admission_receipt(adapter, scope, state, current_operation_id, command)
            return context

        return resolve_owner(reference, revalidate)

    return resolve


def routed(stage):
    """Wrap the existing SDK adapter entry without replacing its scope gates."""

    def decorate(function):
        @wraps(function)
        def invoke(adapter, first, *args, **kwargs):
            from .backends.native_xpedition import NativeBackend

            # Tests and other adapter implementations retain their own JSON
            # boundary. A real NativeBackend always uses the producer below.
            if type(adapter.backend) is not NativeBackend:
                return function(adapter, first, *args, **kwargs)
            from .designer_owner_client import (
                bind_owner_routing,
                bind_start_request,
                make_start_request,
            )

            call = None if stage == "revision" else first
            target = first if stage == "revision" else None
            command = (target.get("extensions") or {}).get("command") if target else call.command
            operation_id = (
                (target.get("extensions") or {}).get("source_operation_id")
                if target
                else getattr(call, "operation_id", None)
            ) or "query-" + secrets.token_hex(16)
            scope = _scope(adapter, call)
            plan = args[0] if stage == "confirm" and args else None
            authority = _binding(
                scope,
                command,
                operation_id,
                plan,
                options=getattr(call, "params", None),
                target=target,
            )
            request = None
            preparation = None
            close_context = nullcontext()
            admission_context = nullcontext()
            from .designer_project_admission import bind_sdk_admission, requested

            admission_target = target or (plan or {}).get("target") or {}
            admission_extension = admission_target.get("extensions") or {}
            admission_capsule = admission_extension.get("designer_project_source")
            admission_options = getattr(call, "params", None) if call is not None else None
            if admission_options is None and admission_capsule:
                admission_options = {
                    "kind": "schematic",
                    "pid": admission_target.get("instance_id"),
                    "project": admission_extension.get("project"),
                    "project_source_operation_id": admission_capsule.get("copy_operation_id"),
                }
            if requested(command, admission_options):
                admission_context = bind_sdk_admission(
                    adapter, scope, operation_id, admission_options, admission_capsule
                )
            if command == "session close":
                from .session import read_state
                from .session_ownership import current_close_operation

                close_target = target or (plan or {}).get("target") or {}
                extension = close_target.get("extensions") or {}
                state = read_state()
                domain = extension.get("domain") or (getattr(call, "params", None) or {}).get(
                    "kind"
                )
                identity = extension.get("process_identity") or state.get("process_identity")
                if domain == "schematic" and isinstance(identity, dict):
                    preparation = prepared_close_observation(adapter, scope)
                    context_id = (
                        preparation.original_operation_id
                        if preparation is not None and stage in {"preview", "revision"}
                        else operation_id
                    )
                    close_context = current_close_operation(context_id, domain, identity)
            if (
                stage == "confirm"
                and command == "session start"
                and ((getattr(call, "params", None) or {}).get("kind") == "schematic")
            ):
                from .driver import _require_dedicated_designer

                _require_dedicated_designer(command, call.params, (plan or {}).get("target") or {})
                status = adapter.backend.status()
                home = status.get("sdd_home")
                if not home:
                    raise CLIError(
                        "E_BACKEND_UNAVAILABLE", "The installed Designer home is unavailable"
                    )
                request = make_start_request(scope, operation_id, config_dir(), Path(home))
            token = _PREPARED_CLOSE.set(preparation)
            try:
                with (
                    bind_start_request(request) if request is not None else nullcontext(),
                    bind_owner_routing(
                        _provider(adapter, scope, request, operation_id, command),
                        operation_id,
                        authority,
                    ),
                    close_context,
                    admission_context,
                ):
                    return function(adapter, first, *args, **kwargs)
            finally:
                _PREPARED_CLOSE.reset(token)

        return invoke

    return decorate
